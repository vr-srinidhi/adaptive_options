"""
Paper Trading Engine — ORB Replay Orchestrator.

Given a date, instrument, capital, and a valid Zerodha access token:
  1. Fetches spot 1-min candles for the day
  2. Computes Opening Range (09:15–09:29)
  3. Pre-fetches option candles for ALL candidate Bull Call and Bear Put spread
     strikes (N_CANDIDATE_SPREADS per direction × 2 legs = up to 20 series)
  4. Replays every minute from 09:15 to session end, running G1–G7 gates
     (no trade) or the exit engine (trade open)
  5. Returns structured dicts ready for bulk DB insert — does NOT touch DB itself

All heavy I/O (Zerodha API calls) happens synchronously; the FastAPI router
wraps this in run_in_executor to avoid blocking the async event loop.
"""
import logging
import uuid
from datetime import date as date_type, datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services.entry_gates import evaluate_gates
from app.services.exit_engine import evaluate_exit
from app.services.opening_range import (
    OR_WINDOW_MINUTES,
    compute_opening_range,
    generate_bearish_candidates,
    generate_bullish_candidates,
)
from app.services.option_resolver import (
    NFO_NAME,
    UNDERLYING_TOKENS,
    resolve_instrument_token,
)
from app.services.zerodha_client import (
    DataUnavailableError,
    fetch_candles_with_token,
    get_instruments_with_token,
)

log = logging.getLogger(__name__)

# ── Charges config ─────────────────────────────────────────────────────────────
# Approximate Zerodha NSE-options round-trip charges for a 2-leg spread.
#   4 orders total (entry long-buy, entry short-sell, exit long-sell, exit short-buy)
_BROKERAGE_PER_ORDER  = 20.0      # ₹20 flat per executed order
_STT_RATE             = 0.0005    # 0.05% of premium on sell side
_EXCHANGE_TXN_RATE    = 0.00053   # 0.053% of premium turnover
_GST_RATE             = 0.18      # 18% on (brokerage + exchange charges)

# Maximum number of minutes a backfilled option price is considered usable
_MAX_PRICE_STALENESS = 5


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candle_time(candle: Dict):
    """Extract (time, naive_datetime) from a candle's date field.
    Strips timezone so timestamps are compatible with TIMESTAMP(timezone=False) columns.
    """
    ts = candle["date"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return ts.time(), ts


def _monthly_expiry_from_master(
    symbol: str, trade_date: date_type, instruments: List[Dict]
) -> Optional[date_type]:
    """
    Return the last (monthly) expiry within trade_date's calendar month.
    If no expiry exists in that month on/after trade_date, returns None.
    """
    nfo_name = NFO_NAME[symbol]
    month_expiries = sorted(
        {i["expiry"] for i in instruments
         if i.get("name") == nfo_name
         and i.get("expiry") is not None
         and i["expiry"].year == trade_date.year
         and i["expiry"].month == trade_date.month
         and i["expiry"] >= trade_date}
    )
    return month_expiries[-1] if month_expiries else None


def _nearest_expiry_from_master(
    symbol: str, trade_date: date_type, instruments: List[Dict]
) -> Optional[date_type]:
    """
    Find the nearest expiry date >= trade_date that actually exists in the
    instruments master for *symbol*.

    More reliable than weekday arithmetic because:
      - Expiry may shift due to holidays
      - Expired contracts are removed from the master on/after expiry day
    """
    nfo_name = NFO_NAME[symbol]
    expiries = sorted(
        {i["expiry"] for i in instruments
         if i.get("name") == nfo_name and i.get("expiry") >= trade_date}
    )
    if not expiries:
        return None
    return expiries[0]


def _lot_size_from_master(symbol: str, instruments: List[Dict]) -> Optional[int]:
    """Return lot size for *symbol* from the live instruments master."""
    nfo_name = NFO_NAME[symbol]
    for inst in instruments:
        if inst.get("name") == nfo_name and inst.get("lot_size"):
            return int(inst["lot_size"])
    return None


def _serialize_candles(candles: List[Dict]) -> List[Dict]:
    """Convert raw Zerodha candle dicts to a JSON-serialisable list of OHLCV dicts."""
    result = []
    for c in candles:
        ts = c["date"]
        if isinstance(ts, datetime):
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            ts = ts.isoformat()
        result.append({
            "time": ts,
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": int(c.get("volume", 0)),
        })
    return result


def _build_price_index(candles: List[Dict]) -> Dict[int, float]:
    """Return {minute_index: close_price} for a candle list."""
    return {i: float(c["close"]) for i, c in enumerate(candles)}


def _get_price_at(
    price_index: Dict[int, float], idx: int, lookback: int = _MAX_PRICE_STALENESS
) -> Tuple[Optional[float], int]:
    """
    Return (price, staleness_minutes) for minute index *idx*.

    If the exact minute is missing (thin liquidity gap), scan back up to
    *lookback* minutes.  staleness_minutes=0 means a fresh price was found.
    Returns (None, 0) when no price is found within the lookback window.
    """
    for i in range(idx, max(-1, idx - lookback - 1), -1):
        if i in price_index:
            return price_index[i], idx - i
    return None, 0


def _compute_charges(
    entry_long_price: float,
    entry_short_price: float,
    exit_long_price: float,
    exit_short_price: float,
    lot_size: int,
    approved_lots: int,
) -> float:
    """
    Approximate round-trip charges for a Bull Call / Bear Put Spread.

    Orders:
      Entry : buy long leg  (long buy)  + sell short leg (short sell)
      Exit  : sell long leg (long sell) + buy short leg  (short buy)

    STT applies only on the sell side of options.
    """
    qty = lot_size * approved_lots

    brokerage = 4 * _BROKERAGE_PER_ORDER

    # STT on sell side: short leg at entry + long leg at exit
    stt = (entry_short_price + exit_long_price) * qty * _STT_RATE

    # Exchange transaction charges on total premium turnover
    turnover = (
        entry_long_price + entry_short_price + exit_long_price + exit_short_price
    ) * qty
    exchange = turnover * _EXCHANGE_TXN_RATE

    # GST on brokerage + exchange charges
    gst = (brokerage + exchange) * _GST_RATE

    return round(brokerage + stt + exchange + gst, 2)


# ── Main engine ───────────────────────────────────────────────────────────────

def run_paper_engine(
    session_id: uuid.UUID,
    trade_date: date_type,
    instrument: str,
    capital: float,
    access_token: str,
) -> Dict[str, Any]:
    """
    Execute the full ORB replay for one trading day.

    Returns:
      {
        "decisions":      [ {fields matching MinuteDecision}, ... ],
        "trade_header":   { fields matching PaperTradeHeader } | None,
        "minute_marks":   [ {fields matching PaperTradeMinuteMark}, ... ],
        "trade_legs":     [ {fields matching PaperTradeLeg}, ... ],
        "candle_series":  [ {session_id, series_type, candles}, ... ],
      }

    Raises DataUnavailableError if spot candles cannot be fetched.
    """
    # ── 1. Fetch spot candles ─────────────────────────────────────────────────
    spot_token = UNDERLYING_TOKENS.get(instrument)
    if spot_token is None:
        raise DataUnavailableError(f"Unknown instrument: {instrument}")

    log.info("Fetching spot candles for %s on %s", instrument, trade_date)
    spot_candles = fetch_candles_with_token(spot_token, trade_date, access_token)

    if len(spot_candles) < OR_WINDOW_MINUTES:
        raise DataUnavailableError(
            f"Only {len(spot_candles)} spot candles available; "
            f"need ≥ {OR_WINDOW_MINUTES} to compute OR."
        )

    # ── 2. Compute Opening Range ──────────────────────────────────────────────
    or_high, or_low = compute_opening_range(spot_candles)
    log.info("OR computed: high=%.2f low=%.2f", or_high, or_low)

    # ── 3. Fetch instruments master — expiry + lot size ───────────────────────
    log.info("Fetching NFO instrument master…")
    instruments_master = get_instruments_with_token(access_token)

    expiry = _nearest_expiry_from_master(instrument, trade_date, instruments_master)
    if expiry is None:
        raise DataUnavailableError(
            f"No {instrument} option expiries found in instruments master "
            f"on or after {trade_date}."
        )
    log.info("Nearest expiry from master: %s", expiry)

    # Read lot size from live master (falls back to hardcoded if not found)
    lot_size = _lot_size_from_master(instrument, instruments_master)
    if lot_size is None:
        from app.services.entry_gates import _FALLBACK_LOT_SIZES
        lot_size = _FALLBACK_LOT_SIZES.get(instrument, 75)
        log.warning("Lot size not found in master for %s; using fallback %d", instrument, lot_size)
    else:
        log.info("Lot size from master: %d", lot_size)

    # ── 4. Pre-fetch ALL candidate option series ──────────────────────────────
    # Generate all candidate strike pairs for both directions up-front so the
    # replay loop has every possible price available without further API calls.
    bullish_candidates = generate_bullish_candidates(or_high)
    bearish_candidates = generate_bearish_candidates(or_low)

    # Deduplicated set of (strike, opt_type) to fetch
    legs_to_fetch = set()
    for long_s, short_s in bullish_candidates:
        legs_to_fetch.add((long_s, "CE"))
        legs_to_fetch.add((short_s, "CE"))
    for long_s, short_s in bearish_candidates:
        legs_to_fetch.add((long_s, "PE"))
        legs_to_fetch.add((short_s, "PE"))

    # Also find monthly expiry (last expiry in trade month) for candle export
    monthly_expiry = _monthly_expiry_from_master(instrument, trade_date, instruments_master)
    if monthly_expiry and monthly_expiry == expiry:
        monthly_expiry = None   # already weekly == monthly; no need to fetch twice
    log.info("Monthly expiry: %s", monthly_expiry)

    option_price_index: Dict[Tuple[int, str], Dict[int, float]] = {}
    option_candles_raw: Dict[Tuple[int, str], List[Dict]] = {}  # raw candles for export

    for strike, opt_type in sorted(legs_to_fetch):
        token = resolve_instrument_token(
            instrument, expiry, strike, opt_type, instruments_master
        )
        if token is None:
            log.warning(
                "Token not found for %s %s%s expiry=%s", instrument, strike, opt_type, expiry
            )
            continue
        try:
            candles = fetch_candles_with_token(token, trade_date, access_token)
            option_price_index[(strike, opt_type)] = _build_price_index(candles)
            option_candles_raw[(strike, opt_type)] = candles
            log.info("Fetched %d candles for %s%s", len(candles), strike, opt_type)
        except DataUnavailableError as exc:
            log.warning("Option data unavailable for %s%s: %s", strike, opt_type, exc)

    # ── 5. Replay loop ────────────────────────────────────────────────────────
    decisions: List[Dict] = []
    minute_marks: List[Dict] = []
    trade_header: Optional[Dict] = None
    trade_legs: List[Dict] = []
    active_trade: Optional[Dict] = None

    for idx, candle in enumerate(spot_candles):
        current_time, ts = _candle_time(candle)
        or_ready = idx >= OR_WINDOW_MINUTES   # True from index 15 (09:30)

        # Previous candle close — for G4 next-candle follow-through check
        prev_candle_close: Optional[float] = (
            float(spot_candles[idx - 1]["close"]) if idx > 0 else None
        )

        def opt_price(strike, otype) -> Tuple[Optional[float], int]:
            return _get_price_at(option_price_index.get((strike, otype), {}), idx)

        if active_trade is None:
            # ── No open trade: run entry gates ────────────────────────────────
            # Build option price snapshot for all candidate strikes (both dirs)
            prices: Dict[Tuple[int, str], float] = {}
            max_staleness = 0
            for (s, t) in legs_to_fetch:
                p, stale = opt_price(s, t)
                if p is not None:
                    prices[(s, t)] = p
                    if stale > max_staleness:
                        max_staleness = stale

            gate = evaluate_gates(
                candle=candle,
                or_high=or_high,
                or_low=or_low,
                or_ready=or_ready,
                has_open_trade=False,
                option_prices=prices,
                instrument=instrument,
                capital=capital,
                expiry=expiry,
                prev_candle_close=prev_candle_close,
                lot_size=lot_size,
            )

            # candidate_structure carries full economics on ENTER;
            # pre_entry_snapshot carries partial economics on NO_TRADE (G3+)
            audit_structure = (
                gate.candidate_structure
                if gate.action == "ENTER"
                else gate.pre_entry_snapshot
            )
            if audit_structure and max_staleness > 0:
                audit_structure = {**audit_structure, "max_price_staleness_min": max_staleness}

            decisions.append({
                "session_id": session_id,
                "timestamp": ts,
                "spot_close": float(candle["close"]),
                "opening_range_high": or_high,
                "opening_range_low": or_low,
                "trade_state": "NO_OPEN_TRADE",
                "signal_state": "EVALUATE" if or_ready else "SKIP_MINUTE",
                "action": gate.action,
                "reason_code": gate.reason_code,
                "reason_text": gate.reason_text,
                "candidate_structure": audit_structure,
                "computed_max_loss": gate.computed_max_loss,
                "computed_target": gate.computed_target,
            })

            if gate.action == "ENTER":
                trade_id = uuid.uuid4()
                long_ep, _ = opt_price(gate.long_strike, gate.opt_type)
                short_ep, _ = opt_price(gate.short_strike, gate.opt_type)

                active_trade = {
                    "id": trade_id,
                    "entry_time": ts,
                    "bias": gate.bias,
                    "long_strike": gate.long_strike,
                    "short_strike": gate.short_strike,
                    "opt_type": gate.opt_type,
                    "entry_debit": gate.entry_debit,
                    "approved_lots": gate.approved_lots,
                    "lot_size": lot_size,
                    "total_max_loss": gate.computed_max_loss,
                    "target_profit": gate.computed_target,
                    "expiry": expiry,
                    # Store entry prices for charges calculation at close
                    "_entry_long_price": round(long_ep, 2) if long_ep else 0.0,
                    "_entry_short_price": round(short_ep, 2) if short_ep else 0.0,
                }
                trade_legs = [
                    {
                        "trade_id": trade_id,
                        "leg_side": "LONG",
                        "option_type": gate.opt_type,
                        "strike": gate.long_strike,
                        "expiry": expiry,
                        "entry_price": round(long_ep, 2) if long_ep else None,
                        "exit_price": None,
                    },
                    {
                        "trade_id": trade_id,
                        "leg_side": "SHORT",
                        "option_type": gate.opt_type,
                        "strike": gate.short_strike,
                        "expiry": expiry,
                        "entry_price": round(short_ep, 2) if short_ep else None,
                        "exit_price": None,
                    },
                ]

        else:
            # ── Trade open: run exit engine ───────────────────────────────────
            long_p, long_stale   = opt_price(active_trade["long_strike"],  active_trade["opt_type"])
            short_p, short_stale = opt_price(active_trade["short_strike"], active_trade["opt_type"])

            if long_p is None or short_p is None:
                decisions.append({
                    "session_id": session_id,
                    "timestamp": ts,
                    "spot_close": float(candle["close"]),
                    "opening_range_high": or_high,
                    "opening_range_low": or_low,
                    "trade_state": "OPEN_TRADE",
                    "signal_state": "SKIP_MINUTE",
                    "action": "HOLD",
                    "reason_code": "DATA_GAP",
                    "reason_text": "Option price data unavailable for this minute; holding.",
                    "candidate_structure": None,
                    "computed_max_loss": active_trade["total_max_loss"],
                    "computed_target": active_trade["target_profit"],
                })
                continue

            ev = evaluate_exit(
                current_time=current_time,
                long_price=long_p,
                short_price=short_p,
                entry_debit=active_trade["entry_debit"],
                lot_size=active_trade["lot_size"],
                approved_lots=active_trade["approved_lots"],
                total_max_loss=active_trade["total_max_loss"],
                target_profit=active_trade["target_profit"],
            )

            staleness_note = (
                {"long_price_staleness_min": long_stale, "short_price_staleness_min": short_stale}
                if (long_stale > 0 or short_stale > 0) else None
            )

            decisions.append({
                "session_id": session_id,
                "timestamp": ts,
                "spot_close": float(candle["close"]),
                "opening_range_high": or_high,
                "opening_range_low": or_low,
                "trade_state": "OPEN_TRADE",
                "signal_state": "EVALUATE",
                "action": ev.action,
                "reason_code": ev.action,
                "reason_text": ev.reason,
                "candidate_structure": staleness_note,
                "computed_max_loss": active_trade["total_max_loss"],
                "computed_target": active_trade["target_profit"],
            })

            minute_marks.append({
                "trade_id": active_trade["id"],
                "timestamp": ts,
                "long_leg_price": round(long_p, 2),
                "short_leg_price": round(short_p, 2),
                "current_spread_value": round(ev.current_spread, 2),
                "mtm_per_lot": round(ev.mtm_per_lot, 2),
                "total_mtm": round(ev.total_mtm, 2),
                "distance_to_target": round(ev.distance_to_target, 2),
                "distance_to_stop": round(ev.distance_to_stop, 2),
                "action": ev.action,
                "reason": ev.reason[:200],
            })

            if ev.action != "HOLD":
                # ── Close trade: compute gross + net P&L ──────────────────
                realized_gross = round(ev.total_mtm, 2)
                charges = _compute_charges(
                    entry_long_price=active_trade["_entry_long_price"],
                    entry_short_price=active_trade["_entry_short_price"],
                    exit_long_price=round(long_p, 2),
                    exit_short_price=round(short_p, 2),
                    lot_size=active_trade["lot_size"],
                    approved_lots=active_trade["approved_lots"],
                )
                realized_net = round(realized_gross - charges, 2)

                for leg in trade_legs:
                    if leg["leg_side"] == "LONG":
                        leg["exit_price"] = round(long_p, 2)
                    else:
                        leg["exit_price"] = round(short_p, 2)

                trade_header = {
                    "id": active_trade["id"],
                    "session_id": session_id,
                    "entry_time": active_trade["entry_time"],
                    "exit_time": ts,
                    "bias": active_trade["bias"],
                    "expiry": active_trade["expiry"],
                    "lot_size": active_trade["lot_size"],
                    "approved_lots": active_trade["approved_lots"],
                    "entry_debit": active_trade["entry_debit"],
                    "total_max_loss": active_trade["total_max_loss"],
                    "target_profit": active_trade["target_profit"],
                    "realized_gross_pnl": realized_gross,
                    "realized_net_pnl": realized_net,
                    "status": "CLOSED",
                    "exit_reason": ev.action,
                    "long_strike": active_trade["long_strike"],
                    "short_strike": active_trade["short_strike"],
                    "option_type": active_trade["opt_type"],
                }
                active_trade = None

    # ── 6. Trade still open at end of data (failsafe) ────────────────────────
    if active_trade is not None and trade_header is None:
        last_candle = spot_candles[-1]
        _, last_ts = _candle_time(last_candle)
        long_p, _ = opt_price(active_trade["long_strike"], active_trade["opt_type"])
        short_p, _ = opt_price(active_trade["short_strike"], active_trade["opt_type"])

        if long_p and short_p:
            spread = float(long_p) - float(short_p)
            realized_gross = round(
                (spread - active_trade["entry_debit"])
                * active_trade["lot_size"]
                * active_trade["approved_lots"],
                2,
            )
            charges = _compute_charges(
                entry_long_price=active_trade["_entry_long_price"],
                entry_short_price=active_trade["_entry_short_price"],
                exit_long_price=round(long_p, 2),
                exit_short_price=round(short_p, 2),
                lot_size=active_trade["lot_size"],
                approved_lots=active_trade["approved_lots"],
            )
            realized_net = round(realized_gross - charges, 2)
        else:
            realized_gross = 0.0
            realized_net = 0.0

        for leg in trade_legs:
            leg["exit_price"] = (
                round(long_p, 2)
                if (leg["leg_side"] == "LONG" and long_p)
                else (round(short_p, 2) if short_p else None)
            )

        trade_header = {
            "id": active_trade["id"],
            "session_id": session_id,
            "entry_time": active_trade["entry_time"],
            "exit_time": last_ts,
            "bias": active_trade["bias"],
            "expiry": active_trade["expiry"],
            "lot_size": active_trade["lot_size"],
            "approved_lots": active_trade["approved_lots"],
            "entry_debit": active_trade["entry_debit"],
            "total_max_loss": active_trade["total_max_loss"],
            "target_profit": active_trade["target_profit"],
            "realized_gross_pnl": realized_gross,
            "realized_net_pnl": realized_net,
            "status": "CLOSED",
            "exit_reason": "EXIT_TIME",
            "long_strike": active_trade["long_strike"],
            "short_strike": active_trade["short_strike"],
            "option_type": active_trade["opt_type"],
        }

    # ── 7. Build candle series for export ────────────────────────────────────
    candle_series: List[Dict] = [
        {
            "session_id": session_id,
            "series_type": "SPOT",
            "candles": _serialize_candles(spot_candles),
        }
    ]

    # If a trade was opened, include weekly + monthly option candles for the
    # actual trade legs (long + short strike, both expiries if available).
    if trade_header:
        long_s   = trade_header["long_strike"]
        short_s  = trade_header["short_strike"]
        opt_type = trade_header["option_type"]

        for strike in (long_s, short_s):
            raw = option_candles_raw.get((strike, opt_type))
            if raw:
                candle_series.append({
                    "session_id": session_id,
                    "series_type": f"{strike}_{opt_type}_WEEKLY",
                    "candles": _serialize_candles(raw),
                })

        # Monthly expiry option candles (if different from weekly)
        if monthly_expiry:
            monthly_legs = set()
            for strike in (long_s, short_s):
                token = resolve_instrument_token(
                    instrument, monthly_expiry, strike, opt_type, instruments_master
                )
                if token and (strike, opt_type, monthly_expiry) not in monthly_legs:
                    monthly_legs.add((strike, opt_type, monthly_expiry))
                    try:
                        m_candles = fetch_candles_with_token(token, trade_date, access_token)
                        candle_series.append({
                            "session_id": session_id,
                            "series_type": f"{strike}_{opt_type}_MONTHLY",
                            "candles": _serialize_candles(m_candles),
                        })
                        log.info(
                            "Fetched %d monthly candles for %s%s exp=%s",
                            len(m_candles), strike, opt_type, monthly_expiry,
                        )
                    except DataUnavailableError as exc:
                        log.warning(
                            "Monthly option data unavailable for %s%s: %s", strike, opt_type, exc
                        )

    log.info(
        "Engine complete: %d decisions, trade=%s, %d marks, %d candle series",
        len(decisions),
        "YES" if trade_header else "NO",
        len(minute_marks),
        len(candle_series),
    )

    return {
        "decisions":     decisions,
        "trade_header":  trade_header,
        "minute_marks":  minute_marks,
        "trade_legs":    trade_legs,
        "candle_series": candle_series,
    }

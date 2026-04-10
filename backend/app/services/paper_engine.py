"""
Paper Trading Engine — ORB Replay Orchestrator.

Given a date, instrument, capital, and a valid Zerodha access token:
  1. Fetches spot 1-min candles for the day
  2. Computes Opening Range (09:15–09:29)
  3. Pre-fetches option candles for candidate Bull Call and Bear Put spread strikes
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

from app.services.entry_gates import LOT_SIZES, evaluate_gates
from app.services.exit_engine import evaluate_exit
from app.services.opening_range import (
    OR_WINDOW_MINUTES,
    compute_opening_range,
    select_bearish_strikes,
    select_bullish_strikes,
)
from app.services.option_resolver import (
    UNDERLYING_TOKENS,
    NFO_NAME,
    resolve_instrument_token,
)
from app.services.zerodha_client import (
    DataUnavailableError,
    fetch_candles_with_token,
    get_instruments_with_token,
)

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candle_time(candle: Dict):
    """Extract a (time, naive_datetime) from a candle's date field.
    Strips timezone so timestamps are compatible with TIMESTAMP(timezone=False) columns.
    """
    ts = candle["date"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return ts.time(), ts


def _nearest_expiry_from_master(
    symbol: str, trade_date: date_type, instruments: List[Dict]
) -> Optional[date_type]:
    """
    Find the nearest expiry date >= trade_date that actually exists in the
    instruments master for *symbol*.

    This is more reliable than computing from weekday because:
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


def _build_price_index(candles: List[Dict]) -> Dict[int, float]:
    """Return {minute_index: close_price} for a candle list."""
    return {i: float(c["close"]) for i, c in enumerate(candles)}


def _get_price_at(price_index: Dict[int, float], idx: int, lookback: int = 5) -> Optional[float]:
    """
    Return the price for minute index *idx*.
    If missing (thin liquidity gap), scan up to *lookback* minutes back.
    Returns None if no price found within the lookback window.
    """
    for i in range(idx, max(-1, idx - lookback - 1), -1):
        if i in price_index:
            return price_index[i]
    return None


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
        "decisions":    [ {fields matching MinuteDecision}, ... ],
        "trade_header": { fields matching PaperTradeHeader } | None,
        "minute_marks": [ {fields matching PaperTradeMinuteMark}, ... ],
        "trade_legs":   [ {fields matching PaperTradeLeg}, ... ],
      }

    Raises DataUnavailableError if spot candles cannot be fetched.
    """
    lot_size = LOT_SIZES.get(instrument, 75)

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

    # ── 3. Resolve expiry and candidate option tokens ─────────────────────────
    log.info("Fetching NFO instrument master…")
    instruments_master = get_instruments_with_token(access_token)

    # Use the actual nearest expiry from the master (handles holiday shifts
    # and avoids missing expired contracts when replaying past dates)
    expiry = _nearest_expiry_from_master(instrument, trade_date, instruments_master)
    if expiry is None:
        raise DataUnavailableError(
            f"No {instrument} option expiries found in instruments master "
            f"on or after {trade_date}."
        )
    log.info("Nearest expiry from master: %s", expiry)

    # Candidate strikes for both directions
    long_ce, short_ce = select_bullish_strikes(or_high)
    long_pe, short_pe = select_bearish_strikes(or_low)

    candidate_legs = [
        (long_ce, "CE"),
        (short_ce, "CE"),
        (long_pe, "PE"),
        (short_pe, "PE"),
    ]

    option_price_index: Dict[Tuple[int, str], Dict[int, float]] = {}

    for strike, opt_type in candidate_legs:
        token = resolve_instrument_token(
            instrument, expiry, strike, opt_type, instruments_master
        )
        if token is None:
            log.warning("Token not found for %s %s%s expiry=%s", instrument, strike, opt_type, expiry)
            continue
        try:
            candles = fetch_candles_with_token(token, trade_date, access_token)
            option_price_index[(strike, opt_type)] = _build_price_index(candles)
            log.info("Fetched %d candles for %s%s", len(candles), strike, opt_type)
        except DataUnavailableError as exc:
            log.warning("Option data unavailable for %s%s: %s", strike, opt_type, exc)

    # ── 4. Replay loop ────────────────────────────────────────────────────────
    decisions: List[Dict] = []
    minute_marks: List[Dict] = []
    trade_header: Optional[Dict] = None
    trade_legs: List[Dict] = []
    active_trade: Optional[Dict] = None

    for idx, candle in enumerate(spot_candles):
        current_time, ts = _candle_time(candle)
        or_ready = idx >= OR_WINDOW_MINUTES   # True from index 15 (09:30)

        def opt_price(strike, opt_type):
            return _get_price_at(
                option_price_index.get((strike, opt_type), {}), idx
            )

        if active_trade is None:
            # ── No open trade: run entry gates ────────────────────────────────
            # Build option price snapshot for candidate strikes
            prices = {}
            for (s, t) in [(long_ce, "CE"), (short_ce, "CE"), (long_pe, "PE"), (short_pe, "PE")]:
                p = opt_price(s, t)
                if p is not None:
                    prices[(s, t)] = p

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
            )

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
                "candidate_structure": gate.candidate_structure,
                "computed_max_loss": gate.computed_max_loss,
                "computed_target": gate.computed_target,
            })

            if gate.action == "ENTER":
                trade_id = uuid.uuid4()
                long_ep = opt_price(gate.long_strike, gate.opt_type)
                short_ep = opt_price(gate.short_strike, gate.opt_type)

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
            long_p = opt_price(active_trade["long_strike"], active_trade["opt_type"])
            short_p = opt_price(active_trade["short_strike"], active_trade["opt_type"])

            if long_p is None or short_p is None:
                # Can't price this minute — log a data gap and hold
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
                "candidate_structure": None,
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
                # Close trade
                realized_pnl = round(ev.total_mtm, 2)
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
                    "realized_gross_pnl": realized_pnl,
                    "realized_net_pnl": realized_pnl,
                    "status": "CLOSED",
                    "exit_reason": ev.action,
                    "long_strike": active_trade["long_strike"],
                    "short_strike": active_trade["short_strike"],
                    "option_type": active_trade["opt_type"],
                }
                active_trade = None

    # ── 5. Trade still open at end of data (failsafe) ────────────────────────
    if active_trade is not None and trade_header is None:
        last_candle = spot_candles[-1]
        _, last_ts = _candle_time(last_candle)
        long_p = opt_price(active_trade["long_strike"], active_trade["opt_type"])
        short_p = opt_price(active_trade["short_strike"], active_trade["opt_type"])

        if long_p and short_p:
            spread = float(long_p) - float(short_p)
            realized_pnl = round(
                (spread - active_trade["entry_debit"])
                * active_trade["lot_size"]
                * active_trade["approved_lots"],
                2,
            )
        else:
            realized_pnl = 0.0

        for leg in trade_legs:
            leg["exit_price"] = round(long_p, 2) if (leg["leg_side"] == "LONG" and long_p) else (
                round(short_p, 2) if short_p else None
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
            "realized_gross_pnl": realized_pnl,
            "realized_net_pnl": realized_pnl,
            "status": "CLOSED",
            "exit_reason": "EXIT_TIME",
            "long_strike": active_trade["long_strike"],
            "short_strike": active_trade["short_strike"],
            "option_type": active_trade["opt_type"],
        }

    log.info(
        "Engine complete: %d decisions, trade=%s, %d marks",
        len(decisions),
        "YES" if trade_header else "NO",
        len(minute_marks),
    )

    return {
        "decisions": decisions,
        "trade_header": trade_header,
        "minute_marks": minute_marks,
        "trade_legs": trade_legs,
    }

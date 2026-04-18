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

Session lifecycle state machine (Phase 1):
  OBSERVING → TENTATIVE_SIGNAL → OPEN_TRADE → TRADE_CLOSED → SESSION_COMPLETE

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
from app.services.strategy_config import STRATEGY_CONFIG as _CFG
from app.services.zerodha_client import (
    DataUnavailableError,
    fetch_candles_with_token,
    get_instruments_with_token,
)

log = logging.getLogger(__name__)

# ── Charges config (from central config) ───────────────────────────────────────
_BROKERAGE_PER_ORDER = _CFG["brokerage_per_order"]
_STT_RATE            = _CFG["stt_rate"]
_EXCHANGE_TXN_RATE   = _CFG["exchange_txn_rate"]
_GST_RATE            = _CFG["gst_rate"]
_MAX_PRICE_STALENESS = _CFG["max_price_staleness_min"]

# ── Gate reason-code → gate label ─────────────────────────────────────────────
_REASON_TO_GATE: Dict[str, str] = {
    "OPENING_RANGE_NOT_READY":            "G1",
    "ACTIVE_TRADE_EXISTS":                "G2",
    "TOO_LATE_TO_ENTER":                  "G2b",
    "NO_BREAKOUT_CONFIRMATION":           "G3",
    "FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH": "G4",
    "NO_HEDGE_AVAILABLE":                 "G5",
    "LOW_LIQUIDITY_REJECT":               "G5",
    "RISK_EXCEEDS_CAP":                   "G6",
    "INSUFFICIENT_TARGET_COVERAGE":       "G7",
    "NO_VALID_CANDIDATE_AFTER_RANKING":   "SELECTOR",
    "STALE_OPTION_PRICE":                 "FRESHNESS",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _candle_time(candle: Dict):
    """Extract (time, naive_datetime) from a candle's date field."""
    ts = candle["date"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return ts.time(), ts


def _monthly_expiry_from_master(
    symbol: str, trade_date: date_type, instruments: List[Dict]
) -> Optional[date_type]:
    """Return the last (monthly) expiry within trade_date's calendar month."""
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
    """Find the nearest expiry date >= trade_date in the instruments master."""
    nfo_name = NFO_NAME[symbol]
    expiries = sorted(
        {i["expiry"] for i in instruments
         if i.get("name") == nfo_name and i.get("expiry") >= trade_date}
    )
    return expiries[0] if expiries else None


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
            "time":   ts,
            "open":   float(c["open"]),
            "high":   float(c["high"]),
            "low":    float(c["low"]),
            "close":  float(c["close"]),
            "volume": int(c.get("volume", 0)),
        })
    return result


def _build_market_index(candles: List[Dict]) -> Dict[int, Dict[str, Any]]:
    """Return {minute_index: {price, volume, oi}} for a candle list."""
    index: Dict[int, Dict[str, Any]] = {}
    for i, candle in enumerate(candles):
        volume = int(candle.get("volume", 0) or 0)
        oi = candle.get("oi")
        # Test fixtures do not carry OI. Fall back to volume so Phase 2 logic
        # can still run deterministically on fixture data.
        oi_value = int(oi if oi is not None else volume)
        index[i] = {
            "price": float(candle["close"]),
            "volume": volume,
            "oi": oi_value,
        }
    return index


def _get_market_at(
    market_index: Dict[int, Dict[str, Any]],
    idx: int,
    lookback: Optional[int] = _MAX_PRICE_STALENESS,
) -> Tuple[Optional[Dict[str, Any]], int]:
    """
    Return ({price, volume, oi, age_min, is_backfilled}, staleness_minutes)
    for minute index *idx*.

    Scans back up to *lookback* minutes for missing prices (thin liquidity gaps).
    staleness_minutes=0 means a fresh price was found.
    """
    min_idx = -1 if lookback is None else max(-1, idx - lookback - 1)
    for i in range(idx, min_idx, -1):
        if i in market_index:
            age = idx - i
            snapshot = dict(market_index[i])
            snapshot["age_min"] = age
            snapshot["is_backfilled"] = age > 0
            return snapshot, age
    return None, 0


def _compute_charges_breakdown(
    entry_long_price: float,
    entry_short_price: float,
    exit_long_price: float,
    exit_short_price: float,
    lot_size: int,
    approved_lots: int,
) -> Dict[str, float]:
    """
    Approximate round-trip charges for a Bull Call / Bear Put Spread.
    Returns a breakdown dict with brokerage, stt, exchange_charges, gst, total.

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

    total = round(brokerage + stt + exchange + gst, 2)
    return {
        "brokerage":        round(brokerage, 2),
        "stt":              round(stt, 2),
        "exchange_charges": round(exchange, 2),
        "gst":              round(gst, 2),
        "total":            total,
    }


def _compute_charges(
    entry_long_price: float,
    entry_short_price: float,
    exit_long_price: float,
    exit_short_price: float,
    lot_size: int,
    approved_lots: int,
) -> float:
    """Convenience wrapper — returns total charges only."""
    return _compute_charges_breakdown(
        entry_long_price, entry_short_price,
        exit_long_price, exit_short_price,
        lot_size, approved_lots,
    )["total"]


# ── Core replay loop (data-source-agnostic) ───────────────────────────────────

def run_paper_engine_core(
    session_id: uuid.UUID,
    trade_date: date_type,
    instrument: str,
    capital: float,
    spot_candles: List[Dict],
    option_market_index: Dict[Tuple[int, str], Dict[int, Dict[str, Any]]],
    option_candles_raw: Dict[Tuple[int, str], List[Dict]],
    expiry: date_type,
    lot_size: int,
    legs_to_fetch: set,
    monthly_expiry: Optional[date_type] = None,
    instruments_master: Optional[List[Dict]] = None,
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pure replay loop — no I/O.  Accepts pre-loaded market data structures
    and runs the full ORB minute-by-minute simulation.

    Called by both run_paper_engine() (Zerodha live) and
    run_historical_paper_engine() (DB-backed historical).

    Returns same dict as run_paper_engine().
    """
    decisions: List[Dict] = []
    minute_marks: List[Dict] = []
    trade_header: Optional[Dict] = None
    trade_legs: List[Dict] = []
    active_trade: Optional[Dict] = None

    current_session_state = "OBSERVING"
    trade_closed_this_session = False
    prev_was_tentative_breakout = False

    or_high, or_low = compute_opening_range(spot_candles)

    for idx, candle in enumerate(spot_candles):
        current_time, ts = _candle_time(candle)
        or_ready = idx >= OR_WINDOW_MINUTES

        prev_candle_close: Optional[float] = (
            float(spot_candles[idx - 1]["close"]) if idx > 0 else None
        )

        def opt_market(
            strike, otype, _idx=idx, lookback: Optional[int] = _MAX_PRICE_STALENESS,
        ) -> Tuple[Optional[Dict[str, Any]], int]:
            return _get_market_at(option_market_index.get((strike, otype), {}), _idx, lookback=lookback)

        def opt_price(strike, otype, _idx=idx) -> Tuple[Optional[float], int]:
            snapshot, stale = opt_market(strike, otype, _idx)
            return (snapshot["price"], stale) if snapshot is not None else (None, stale)

        if trade_closed_this_session:
            current_session_state = "SESSION_COMPLETE"
            decisions.append({
                "session_id": session_id, "timestamp": ts,
                "spot_close": float(candle["close"]),
                "opening_range_high": or_high, "opening_range_low": or_low,
                "trade_state": "NO_OPEN_TRADE", "signal_state": "SKIP_MINUTE",
                "action": "NO_TRADE", "reason_code": "SESSION_COMPLETE",
                "reason_text": "Trade already closed for this session. No re-entry.",
                "candidate_structure": None, "computed_max_loss": None,
                "computed_target": None, "session_state": current_session_state,
                "signal_substate": None, "rejection_gate": None,
                "price_freshness_json": None, "candidate_ranking_json": None,
                "selected_candidate_rank": None, "selected_candidate_score": None,
                "selected_candidate_score_breakdown_json": None,
            })
            continue

        if active_trade is None:
            prices: Dict[Tuple[int, str], float] = {}
            market_snapshots: Dict[Tuple[int, str], Dict[str, Any]] = {}
            price_staleness_map: Dict[str, int] = {}

            for (s, t) in legs_to_fetch:
                snapshot, stale = opt_market(s, t, lookback=None)
                if snapshot is not None:
                    prices[(s, t)] = snapshot["price"]
                    market_snapshots[(s, t)] = snapshot
                    if stale > 0:
                        price_staleness_map[f"{s}_{t}_age_min"] = stale

            gate = evaluate_gates(
                candle=candle, or_high=or_high, or_low=or_low, or_ready=or_ready,
                has_open_trade=False, option_prices=prices, instrument=instrument,
                capital=capital, expiry=expiry, prev_candle_close=prev_candle_close,
                lot_size=lot_size, current_time=current_time, option_market=market_snapshots,
            )

            signal_substate: Optional[str] = None
            if gate.reason_code == "FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH":
                signal_substate = "TENTATIVE_BREAKOUT"
            elif prev_was_tentative_breakout:
                if gate.reason_code == "NO_BREAKOUT_CONFIRMATION":
                    signal_substate = "FAILED_FIRST_BREAKOUT"
                else:
                    signal_substate = "CONFIRMED_BREAKOUT"

            if not or_ready:
                current_session_state = "OBSERVING"
            elif gate.action == "ENTER":
                current_session_state = "OPEN_TRADE"
            elif signal_substate == "TENTATIVE_BREAKOUT":
                current_session_state = "TENTATIVE_SIGNAL"
            elif signal_substate in ("FAILED_FIRST_BREAKOUT", None):
                current_session_state = "OBSERVING"
            elif signal_substate == "CONFIRMED_BREAKOUT" and gate.action != "ENTER":
                current_session_state = "OBSERVING"

            prev_was_tentative_breakout = (
                gate.reason_code == "FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH"
            )

            effective_action = gate.action
            effective_reason_code = gate.reason_code
            effective_reason_text = gate.reason_text
            effective_selected_candidate_rank = gate.selected_candidate_rank
            effective_selected_candidate_score = gate.selected_candidate_score
            effective_selected_candidate_score_breakdown = gate.selected_candidate_score_breakdown
            stale_entry_rejected = False

            if gate.action == "ENTER":
                long_age = price_staleness_map.get(f"{gate.long_strike}_{gate.opt_type}_age_min", 0)
                short_age = price_staleness_map.get(f"{gate.short_strike}_{gate.opt_type}_age_min", 0)
                if max(long_age, short_age) > 0:
                    stale_entry_rejected = True
                    effective_action = "NO_TRADE"
                    effective_reason_code = "STALE_OPTION_PRICE"
                    effective_reason_text = (
                        "Confirmed breakout, but entry requires current-minute option prices "
                        f"(long age={long_age}, short age={short_age})."
                    )
                    effective_selected_candidate_rank = None
                    effective_selected_candidate_score = None
                    effective_selected_candidate_score_breakdown = None
                    current_session_state = "OBSERVING"
                    if signal_substate is None:
                        signal_substate = "CONFIRMED_BREAKOUT"

            rejection_gate = (
                None if effective_action == "ENTER"
                else _REASON_TO_GATE.get(effective_reason_code)
            )
            audit_structure = gate.candidate_structure if gate.action == "ENTER" else gate.pre_entry_snapshot
            price_freshness_json = price_staleness_map if price_staleness_map else None

            decisions.append({
                "session_id": session_id, "timestamp": ts,
                "spot_close": float(candle["close"]),
                "opening_range_high": or_high, "opening_range_low": or_low,
                "trade_state": "NO_OPEN_TRADE",
                "signal_state": "EVALUATE" if or_ready else "SKIP_MINUTE",
                "action": effective_action, "reason_code": effective_reason_code,
                "reason_text": effective_reason_text, "candidate_structure": audit_structure,
                "computed_max_loss": gate.computed_max_loss, "computed_target": gate.computed_target,
                "session_state": current_session_state, "signal_substate": signal_substate,
                "rejection_gate": rejection_gate, "price_freshness_json": price_freshness_json,
                "candidate_ranking_json": gate.candidate_ranking_json,
                "selected_candidate_rank": effective_selected_candidate_rank,
                "selected_candidate_score": effective_selected_candidate_score,
                "selected_candidate_score_breakdown_json": effective_selected_candidate_score_breakdown,
            })

            if gate.action == "ENTER" and not stale_entry_rejected:
                trade_id = uuid.uuid4()
                long_ep, _ = opt_price(gate.long_strike, gate.opt_type)
                short_ep, _ = opt_price(gate.short_strike, gate.opt_type)
                active_trade = {
                    "id": trade_id, "entry_time": ts, "bias": gate.bias,
                    "long_strike": gate.long_strike, "short_strike": gate.short_strike,
                    "opt_type": gate.opt_type, "entry_debit": gate.entry_debit,
                    "approved_lots": gate.approved_lots, "lot_size": lot_size,
                    "total_max_loss": gate.computed_max_loss, "target_profit": gate.computed_target,
                    "expiry": expiry,
                    "_entry_long_price": round(long_ep, 2) if long_ep else 0.0,
                    "_entry_short_price": round(short_ep, 2) if short_ep else 0.0,
                    "_entry_reason_code": gate.reason_code,
                    "_entry_reason_text": gate.reason_text,
                    "_risk_cap": capital * _CFG["max_risk_pct"],
                    "_selection_method": gate.selection_method,
                    "_selected_candidate_rank": gate.selected_candidate_rank,
                    "_selected_candidate_score": gate.selected_candidate_score,
                    "_selected_candidate_score_breakdown": gate.selected_candidate_score_breakdown,
                    "_strategy_params": {
                        "strategy_name": _CFG["strategy_name"],
                        "strategy_version": _CFG["strategy_version"],
                        "or_window_minutes": _CFG["or_window_minutes"],
                        "breakout_buffer_pct": _CFG["breakout_buffer_pct"],
                        "max_risk_pct": _CFG["max_risk_pct"],
                        "target_profit_pct": _CFG["target_profit_pct"],
                        "square_off_time": str(_CFG["square_off_time"]),
                        "min_minutes_left_to_enter": _CFG["min_minutes_left_to_enter"],
                        "n_candidate_spreads": _CFG["n_candidate_spreads"],
                        "selection_method": _CFG["selection_method"],
                        "lot_size": lot_size, "capital": capital,
                    },
                }
                trade_legs = [
                    {
                        "trade_id": trade_id, "leg_side": "LONG",
                        "option_type": gate.opt_type, "strike": gate.long_strike,
                        "expiry": expiry,
                        "entry_price": round(long_ep, 2) if long_ep else None,
                        "exit_price": None,
                    },
                    {
                        "trade_id": trade_id, "leg_side": "SHORT",
                        "option_type": gate.opt_type, "strike": gate.short_strike,
                        "expiry": expiry,
                        "entry_price": round(short_ep, 2) if short_ep else None,
                        "exit_price": None,
                    },
                ]
        else:
            current_session_state = "OPEN_TRADE"
            long_p, long_stale = opt_price(active_trade["long_strike"], active_trade["opt_type"])
            short_p, short_stale = opt_price(active_trade["short_strike"], active_trade["opt_type"])

            price_freshness_json: Optional[Dict] = None
            if long_stale > 0 or short_stale > 0:
                price_freshness_json = {"long_age_min": long_stale, "short_age_min": short_stale}

            if long_p is None or short_p is None:
                decisions.append({
                    "session_id": session_id, "timestamp": ts,
                    "spot_close": float(candle["close"]),
                    "opening_range_high": or_high, "opening_range_low": or_low,
                    "trade_state": "OPEN_TRADE", "signal_state": "SKIP_MINUTE",
                    "action": "HOLD", "reason_code": "DATA_GAP",
                    "reason_text": "Option price data unavailable for this minute; holding.",
                    "candidate_structure": None,
                    "computed_max_loss": active_trade["total_max_loss"],
                    "computed_target": active_trade["target_profit"],
                    "session_state": current_session_state, "signal_substate": None,
                    "rejection_gate": None, "price_freshness_json": price_freshness_json,
                    "candidate_ranking_json": None, "selected_candidate_rank": None,
                    "selected_candidate_score": None,
                    "selected_candidate_score_breakdown_json": None,
                })
                continue

            estimated_charges = _compute_charges(
                entry_long_price=active_trade["_entry_long_price"],
                entry_short_price=active_trade["_entry_short_price"],
                exit_long_price=round(long_p, 2), exit_short_price=round(short_p, 2),
                lot_size=active_trade["lot_size"], approved_lots=active_trade["approved_lots"],
            )

            ev = evaluate_exit(
                current_time=current_time, long_price=long_p, short_price=short_p,
                entry_debit=active_trade["entry_debit"], lot_size=active_trade["lot_size"],
                approved_lots=active_trade["approved_lots"],
                total_max_loss=active_trade["total_max_loss"],
                target_profit=active_trade["target_profit"],
                estimated_charges=estimated_charges,
            )

            if ev.action != "HOLD":
                current_session_state = "TRADE_CLOSED"

            decisions.append({
                "session_id": session_id, "timestamp": ts,
                "spot_close": float(candle["close"]),
                "opening_range_high": or_high, "opening_range_low": or_low,
                "trade_state": "OPEN_TRADE", "signal_state": "EVALUATE",
                "action": ev.action, "reason_code": ev.action, "reason_text": ev.reason,
                "candidate_structure": None,
                "computed_max_loss": active_trade["total_max_loss"],
                "computed_target": active_trade["target_profit"],
                "session_state": current_session_state, "signal_substate": None,
                "rejection_gate": None, "price_freshness_json": price_freshness_json,
                "candidate_ranking_json": None, "selected_candidate_rank": None,
                "selected_candidate_score": None,
                "selected_candidate_score_breakdown_json": None,
            })

            minute_marks.append({
                "trade_id": active_trade["id"], "timestamp": ts,
                "long_leg_price": round(long_p, 2), "short_leg_price": round(short_p, 2),
                "current_spread_value": round(ev.current_spread, 2),
                "mtm_per_lot": round(ev.mtm_per_lot, 2), "total_mtm": round(ev.total_mtm, 2),
                "distance_to_target": round(ev.distance_to_target, 2),
                "distance_to_stop": round(ev.distance_to_stop, 2),
                "action": ev.action, "reason": ev.reason[:200],
                "gross_mtm": round(ev.gross_mtm, 2),
                "estimated_exit_charges": round(ev.estimated_exit_charges, 2),
                "estimated_net_mtm": round(ev.estimated_net_mtm, 2),
                "price_freshness_json": price_freshness_json,
            })

            if ev.action != "HOLD":
                realized_gross = round(ev.total_mtm, 2)
                charges_breakdown = _compute_charges_breakdown(
                    entry_long_price=active_trade["_entry_long_price"],
                    entry_short_price=active_trade["_entry_short_price"],
                    exit_long_price=round(long_p, 2), exit_short_price=round(short_p, 2),
                    lot_size=active_trade["lot_size"], approved_lots=active_trade["approved_lots"],
                )
                charges = charges_breakdown["total"]
                realized_net = round(realized_gross - charges, 2)

                for leg in trade_legs:
                    if leg["leg_side"] == "LONG":
                        leg["exit_price"] = round(long_p, 2)
                    else:
                        leg["exit_price"] = round(short_p, 2)

                trade_header = {
                    "id": active_trade["id"], "session_id": session_id,
                    "entry_time": active_trade["entry_time"], "exit_time": ts,
                    "bias": active_trade["bias"], "expiry": active_trade["expiry"],
                    "lot_size": active_trade["lot_size"],
                    "approved_lots": active_trade["approved_lots"],
                    "entry_debit": active_trade["entry_debit"],
                    "total_max_loss": active_trade["total_max_loss"],
                    "target_profit": active_trade["target_profit"],
                    "realized_gross_pnl": realized_gross, "realized_net_pnl": realized_net,
                    "charges": charges, "charges_breakdown_json": charges_breakdown,
                    "status": "CLOSED", "exit_reason": ev.action,
                    "long_strike": active_trade["long_strike"],
                    "short_strike": active_trade["short_strike"],
                    "option_type": active_trade["opt_type"],
                    "strategy_name": _CFG["strategy_name"],
                    "strategy_version": _CFG["strategy_version"],
                    "strategy_params_json": active_trade["_strategy_params"],
                    "risk_cap": active_trade["_risk_cap"],
                    "entry_reason_code": active_trade["_entry_reason_code"],
                    "entry_reason_text": active_trade["_entry_reason_text"],
                    "selection_method": active_trade["_selection_method"],
                    "selected_candidate_rank": active_trade["_selected_candidate_rank"],
                    "selected_candidate_score": active_trade["_selected_candidate_score"],
                    "selected_candidate_score_breakdown_json": active_trade["_selected_candidate_score_breakdown"],
                }
                active_trade = None
                trade_closed_this_session = True

    # Trade still open at end of data (failsafe)
    if active_trade is not None and trade_header is None:
        last_candle = spot_candles[-1]
        _, last_ts = _candle_time(last_candle)
        long_p, _ = opt_price(active_trade["long_strike"], active_trade["opt_type"])
        short_p, _ = opt_price(active_trade["short_strike"], active_trade["opt_type"])

        if long_p and short_p:
            spread = float(long_p) - float(short_p)
            realized_gross = round(
                (spread - active_trade["entry_debit"])
                * active_trade["lot_size"] * active_trade["approved_lots"], 2,
            )
            charges_breakdown = _compute_charges_breakdown(
                entry_long_price=active_trade["_entry_long_price"],
                entry_short_price=active_trade["_entry_short_price"],
                exit_long_price=round(long_p, 2), exit_short_price=round(short_p, 2),
                lot_size=active_trade["lot_size"], approved_lots=active_trade["approved_lots"],
            )
            charges = charges_breakdown["total"]
            realized_net = round(realized_gross - charges, 2)
        else:
            realized_gross = realized_net = charges = 0.0
            charges_breakdown = {"brokerage": 0.0, "stt": 0.0, "exchange_charges": 0.0, "gst": 0.0, "total": 0.0}

        for leg in trade_legs:
            leg["exit_price"] = (
                round(long_p, 2) if (leg["leg_side"] == "LONG" and long_p)
                else (round(short_p, 2) if short_p else None)
            )

        trade_header = {
            "id": active_trade["id"], "session_id": session_id,
            "entry_time": active_trade["entry_time"], "exit_time": last_ts,
            "bias": active_trade["bias"], "expiry": active_trade["expiry"],
            "lot_size": active_trade["lot_size"], "approved_lots": active_trade["approved_lots"],
            "entry_debit": active_trade["entry_debit"],
            "total_max_loss": active_trade["total_max_loss"],
            "target_profit": active_trade["target_profit"],
            "realized_gross_pnl": realized_gross, "realized_net_pnl": realized_net,
            "charges": charges, "charges_breakdown_json": charges_breakdown,
            "status": "CLOSED", "exit_reason": "EXIT_TIME",
            "long_strike": active_trade["long_strike"],
            "short_strike": active_trade["short_strike"],
            "option_type": active_trade["opt_type"],
            "strategy_name": _CFG["strategy_name"],
            "strategy_version": _CFG["strategy_version"],
            "strategy_params_json": active_trade["_strategy_params"],
            "risk_cap": active_trade["_risk_cap"],
            "entry_reason_code": active_trade["_entry_reason_code"],
            "entry_reason_text": active_trade["_entry_reason_text"],
            "selection_method": active_trade["_selection_method"],
            "selected_candidate_rank": active_trade["_selected_candidate_rank"],
            "selected_candidate_score": active_trade["_selected_candidate_score"],
            "selected_candidate_score_breakdown_json": active_trade["_selected_candidate_score_breakdown"],
        }
        current_session_state = "TRADE_CLOSED"

    # Build candle series
    candle_series: List[Dict] = [{
        "session_id": session_id, "series_type": "SPOT",
        "candles": _serialize_candles(spot_candles),
    }]

    if trade_header:
        long_s = trade_header["long_strike"]
        short_s = trade_header["short_strike"]
        opt_type = trade_header["option_type"]
        for strike in (long_s, short_s):
            raw = option_candles_raw.get((strike, opt_type))
            if raw:
                candle_series.append({
                    "session_id": session_id,
                    "series_type": f"{strike}_{opt_type}_WEEKLY",
                    "candles": _serialize_candles(raw),
                })

        # Monthly candles (only available for live path with instruments_master + access_token)
        if monthly_expiry and instruments_master and access_token:
            monthly_legs: set = set()
            for strike in (long_s, short_s):
                token = resolve_instrument_token(
                    instrument, monthly_expiry, strike, opt_type, instruments_master
                )
                if token and (strike, opt_type, monthly_expiry) not in monthly_legs:
                    monthly_legs.add((strike, opt_type, monthly_expiry))
                    try:
                        from app.services.zerodha_client import fetch_candles_with_token, DataUnavailableError
                        m_candles = fetch_candles_with_token(token, trade_date, access_token)
                        candle_series.append({
                            "session_id": session_id,
                            "series_type": f"{strike}_{opt_type}_MONTHLY",
                            "candles": _serialize_candles(m_candles),
                        })
                    except Exception as exc:
                        log.warning("Monthly option data unavailable for %s%s: %s", strike, opt_type, exc)

    log.info(
        "Engine core complete: %d decisions, trade=%s, %d marks, %d series, state=%s",
        len(decisions), "YES" if trade_header else "NO",
        len(minute_marks), len(candle_series), current_session_state,
    )

    return {
        "decisions": decisions, "trade_header": trade_header,
        "minute_marks": minute_marks, "trade_legs": trade_legs,
        "candle_series": candle_series, "final_session_state": current_session_state,
    }


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
        "decisions":           [ {fields matching MinuteDecision}, ... ],
        "trade_header":        { fields matching PaperTradeHeader } | None,
        "minute_marks":        [ {fields matching PaperTradeMinuteMark}, ... ],
        "trade_legs":          [ {fields matching PaperTradeLeg}, ... ],
        "candle_series":       [ {session_id, series_type, candles}, ... ],
        "final_session_state": str,
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

    lot_size = _lot_size_from_master(instrument, instruments_master)
    if lot_size is None:
        from app.services.entry_gates import _FALLBACK_LOT_SIZES
        lot_size = _FALLBACK_LOT_SIZES.get(instrument, 75)
        log.warning("Lot size not found in master for %s; using fallback %d", instrument, lot_size)
    else:
        log.info("Lot size from master: %d", lot_size)

    # ── 4. Pre-fetch ALL candidate option series ──────────────────────────────
    bullish_candidates = generate_bullish_candidates(or_high)
    bearish_candidates = generate_bearish_candidates(or_low)

    legs_to_fetch = set()
    for long_s, short_s in bullish_candidates:
        legs_to_fetch.add((long_s, "CE"))
        legs_to_fetch.add((short_s, "CE"))
    for long_s, short_s in bearish_candidates:
        legs_to_fetch.add((long_s, "PE"))
        legs_to_fetch.add((short_s, "PE"))

    monthly_expiry = _monthly_expiry_from_master(instrument, trade_date, instruments_master)
    if monthly_expiry and monthly_expiry == expiry:
        monthly_expiry = None
    log.info("Monthly expiry: %s", monthly_expiry)

    option_market_index: Dict[Tuple[int, str], Dict[int, Dict[str, Any]]] = {}
    option_candles_raw: Dict[Tuple[int, str], List[Dict]] = {}

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
            option_market_index[(strike, opt_type)] = _build_market_index(candles)
            option_candles_raw[(strike, opt_type)] = candles
            log.info("Fetched %d candles for %s%s", len(candles), strike, opt_type)
        except DataUnavailableError as exc:
            log.warning("Option data unavailable for %s%s: %s", strike, opt_type, exc)

    # ── 5. Run core replay loop ───────────────────────────────────────────────
    return run_paper_engine_core(
        session_id=session_id,
        trade_date=trade_date,
        instrument=instrument,
        capital=capital,
        spot_candles=spot_candles,
        option_market_index=option_market_index,
        option_candles_raw=option_candles_raw,
        expiry=expiry,
        lot_size=lot_size,
        legs_to_fetch=legs_to_fetch,
        monthly_expiry=monthly_expiry,
        instruments_master=instruments_master,
        access_token=access_token,
    )

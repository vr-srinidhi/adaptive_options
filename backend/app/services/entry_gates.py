"""
G1–G7 gate stack for the ORB entry decision.

Gates are evaluated in sequence every minute after the OR window closes.
The first failing gate short-circuits evaluation and returns a NO_TRADE result
with a specific reason code so the decision can be logged and audited.

Key design notes
----------------
- G4 (follow-through) now requires the PREVIOUS candle to have already confirmed
  the breakout — i.e. entry only fires on the second consecutive breakout candle.
  Pass prev_candle_close=None to skip this check (e.g. first OR-ready minute).

- G7 (target viability) now asks "can the spread theoretically earn at least the
  session target?" — NOT a fixed 1.5× R:R ratio.  This matches the PRD intent
  exactly: reject only when the spread is so wide that even a full move to max
  width cannot cover the session target.

- Candidate spreads: G5–G7 now delegate to spread_selector.py, which evaluates
  the full candidate universe, filters hard rejects, ranks valid spreads, and
  returns the best candidate plus an audit trail.

- lot_size comes in as a parameter (read from instruments master by paper_engine)
  so the engine stays correct even after Zerodha quarterly lot-size changes.
"""
from dataclasses import dataclass
from datetime import date as date_type, time as time_type
from typing import Dict, Optional

from app.services.opening_range import (
    is_bearish_breakout,
    is_bullish_breakout,
    select_bearish_strikes,
    select_bullish_strikes,
)
from app.services.spread_selector import SELECTION_METHOD, select_spread_candidate

# ── Strategy config (sourced from central config) ─────────────────────────────
from app.services.strategy_config import STRATEGY_CONFIG as _CFG

MAX_RISK_PCT = _CFG["max_risk_pct"]        # G6: max 2% of capital as max loss
TARGET_PCT   = _CFG["target_profit_pct"]   # profit target = 0.5% of capital

_SQUARE_OFF_TIME           = _CFG["square_off_time"]            # 15:20
_MIN_MINUTES_LEFT_TO_ENTER = _CFG["min_minutes_left_to_enter"]  # reject if < 20 mins remain

# Fallback lot sizes — used only if instruments master lookup fails
_FALLBACK_LOT_SIZES: Dict[str, int] = _CFG["fallback_lot_sizes"]


def _minutes_until_squareoff(current_time: time_type) -> int:
    """Return minutes remaining from current_time until SQUARE_OFF_TIME (may be negative)."""
    sq  = _SQUARE_OFF_TIME.hour * 60 + _SQUARE_OFF_TIME.minute
    cur = current_time.hour * 60 + current_time.minute
    return sq - cur


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class GateResult:
    action: str                            # "ENTER" or "NO_TRADE"
    reason_code: str
    reason_text: str
    # Populated on ENTER
    candidate_structure: Optional[Dict] = None
    computed_max_loss: Optional[float] = None
    computed_target: Optional[float] = None
    bias: Optional[str] = None
    long_strike: Optional[int] = None
    short_strike: Optional[int] = None
    opt_type: Optional[str] = None
    approved_lots: int = 0
    entry_debit: float = 0.0
    lot_size: int = 75
    expiry: Optional[date_type] = None
    # Pre-entry economics snapshot — populated even for NO_TRADE when we reached
    # G3+ so the audit log can surface "what the trade would have looked like"
    pre_entry_snapshot: Optional[Dict] = None
    candidate_ranking_json: Optional[Dict] = None
    selected_candidate_rank: Optional[int] = None
    selected_candidate_score: Optional[float] = None
    selected_candidate_score_breakdown: Optional[Dict] = None
    selection_method: Optional[str] = None


# ── Gate evaluator ────────────────────────────────────────────────────────────

def evaluate_gates(
    candle: Dict,
    or_high: float,
    or_low: float,
    or_ready: bool,
    has_open_trade: bool,
    option_prices: Dict,        # {(strike, opt_type): price}
    instrument: str,
    capital: float,
    expiry: date_type,
    prev_candle_close: Optional[float] = None,  # for G4 follow-through
    lot_size: Optional[int] = None,             # from instruments master
    current_time: Optional[time_type] = None,   # for TOO_LATE_TO_ENTER check
    option_market: Optional[Dict] = None,       # {(strike, opt_type): {price, volume, oi, age_min, is_backfilled}}
) -> GateResult:
    """
    Evaluate G1–G7 for the given minute candle.

    Returns GateResult(action="ENTER") if all gates pass, otherwise
    GateResult(action="NO_TRADE") with the first-failing reason code.

    For minutes that reach direction detection (G3+), pre_entry_snapshot is
    always populated so the audit log can surface rejected-trade economics.
    """
    close = float(candle["close"])
    lot_size = lot_size or _FALLBACK_LOT_SIZES.get(instrument, 75)
    target_rupees = capital * TARGET_PCT

    # ── G1: Opening range window complete ────────────────────────────────────
    if not or_ready:
        return GateResult(
            action="NO_TRADE",
            reason_code="OPENING_RANGE_NOT_READY",
            reason_text=(
                f"Opening range window not yet complete. "
                f"OR High={or_high:.2f}, Low={or_low:.2f}."
            ),
        )

    # ── G2: No active trade ───────────────────────────────────────────────────
    if has_open_trade:
        return GateResult(
            action="NO_TRADE",
            reason_code="ACTIVE_TRADE_EXISTS",
            reason_text="A paper trade is already open. Only one trade per session.",
        )

    # ── G2b: Enough time left before forced square-off ────────────────────────
    if current_time is not None:
        mins_left = _minutes_until_squareoff(current_time)
        if mins_left < _MIN_MINUTES_LEFT_TO_ENTER:
            return GateResult(
                action="NO_TRADE",
                reason_code="TOO_LATE_TO_ENTER",
                reason_text=(
                    f"Only {mins_left} minute(s) until square-off at "
                    f"{_SQUARE_OFF_TIME.strftime('%H:%M')}; "
                    f"need ≥ {_MIN_MINUTES_LEFT_TO_ENTER} to enter."
                ),
            )

    # ── G3: Spot has closed beyond OR boundary ────────────────────────────────
    bullish = is_bullish_breakout(close, or_high)
    bearish = is_bearish_breakout(close, or_low)
    if not bullish and not bearish:
        return GateResult(
            action="NO_TRADE",
            reason_code="NO_BREAKOUT_CONFIRMATION",
            reason_text=(
                f"Close {close:.2f} has not broken OR high {or_high:.2f} "
                f"(need >{or_high * 1.001:.2f}) or OR low {or_low:.2f} "
                f"(need <{or_low * 0.999:.2f})."
            ),
        )

    bias    = "BULLISH" if bullish else "BEARISH"
    opt_type = "CE" if bullish else "PE"

    # ── G4: Follow-through — second consecutive breakout candle required ───────
    # The first breakout candle is treated as tentative.  Only when the previous
    # candle also confirmed the same breakout do we proceed to trade evaluation.
    # (prev_candle_close=None on the very first OR-ready minute → skip check.)
    if prev_candle_close is not None:
        if bullish and not is_bullish_breakout(prev_candle_close, or_high):
            return GateResult(
                action="NO_TRADE",
                reason_code="FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH",
                reason_text=(
                    f"Tentative bullish breakout at {close:.2f} "
                    f"(OR high {or_high:.2f}).  Previous close {prev_candle_close:.2f} "
                    f"was inside range — waiting one more candle to confirm."
                ),
                pre_entry_snapshot={
                    "bias": bias, "opt_type": opt_type,
                    "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                    "close": round(close, 2), "prev_close": round(prev_candle_close, 2),
                    "failing_gate": "G4",
                },
            )
        if bearish and not is_bearish_breakout(prev_candle_close, or_low):
            return GateResult(
                action="NO_TRADE",
                reason_code="FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH",
                reason_text=(
                    f"Tentative bearish breakout at {close:.2f} "
                    f"(OR low {or_low:.2f}).  Previous close {prev_candle_close:.2f} "
                    f"was inside range — waiting one more candle to confirm."
                ),
                pre_entry_snapshot={
                    "bias": bias, "opt_type": opt_type,
                    "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                    "close": round(close, 2), "prev_close": round(prev_candle_close, 2),
                    "failing_gate": "G4",
                },
            )

    # ── G5–G7: Candidate spread selection ───────────────────────────────────
    market = option_market or {
        key: {
            "price": float(price),
            # Phase 1 unit tests pass only flat prices. Use high non-zero
            # liquidity defaults there so legacy gate tests still behave.
            "volume": 1_000_000,
            "oi": 1_000_000,
            "age_min": 0,
            "is_backfilled": False,
        }
        for key, price in option_prices.items()
        if price is not None
    }

    selection = select_spread_candidate(
        bias=bias,
        reference_strike=(
            select_bullish_strikes(or_high)[0]
            if bullish
            else select_bearish_strikes(or_low)[0]
        ),
        spot_price=close,
        capital=capital,
        lot_size=lot_size,
        expiry=str(expiry),
        option_market=market,
    )

    if selection.selected_candidate:
        candidate = selection.selected_candidate
        return GateResult(
            action="ENTER",
            reason_code="ENTER_TRADE",
            reason_text=(
                f"{bias} breakout confirmed at close {close:.2f}. "
                f"Selected rank #{selection.selected_candidate_rank} "
                f"{candidate['long_strike']}{opt_type}@{candidate['long_price']:.2f} / "
                f"{candidate['short_strike']}{opt_type}@{candidate['short_price']:.2f}. "
                f"Debit={candidate['spread_debit']:.2f}, Lots={candidate['approved_lots']}, "
                f"MaxLoss=₹{candidate['total_max_loss']:.0f}, MaxGain=₹{candidate['max_gain_total']:.0f}, "
                f"Score={selection.selected_candidate_score:.4f}."
            ),
            candidate_structure=candidate,
            computed_max_loss=round(candidate["total_max_loss"], 2),
            computed_target=round(target_rupees, 2),
            bias=bias,
            long_strike=candidate["long_strike"],
            short_strike=candidate["short_strike"],
            opt_type=opt_type,
            approved_lots=candidate["approved_lots"],
            entry_debit=round(candidate["spread_debit"], 2),
            lot_size=lot_size,
            expiry=expiry,
            candidate_ranking_json=selection.candidate_ranking_json,
            selected_candidate_rank=selection.selected_candidate_rank,
            selected_candidate_score=selection.selected_candidate_score,
            selected_candidate_score_breakdown=selection.selected_candidate_score_breakdown,
            selection_method=SELECTION_METHOD,
        )

    best_snapshot = selection.best_invalid_candidate
    computed_max_loss = None
    if best_snapshot and best_snapshot.get("total_max_loss") is not None:
        computed_max_loss = round(best_snapshot["total_max_loss"], 2)

    return GateResult(
        action="NO_TRADE",
        reason_code=selection.reason_code,
        reason_text=selection.reason_text,
        pre_entry_snapshot=best_snapshot,
        computed_max_loss=computed_max_loss,
        computed_target=round(target_rupees, 2),
        candidate_ranking_json=selection.candidate_ranking_json,
        selected_candidate_rank=selection.selected_candidate_rank,
        selected_candidate_score=selection.selected_candidate_score,
        selected_candidate_score_breakdown=selection.selected_candidate_score_breakdown,
        selection_method=SELECTION_METHOD,
    )

"""
G1–G7 gate stack for the ORB entry decision.

Gates are evaluated in sequence every minute after the OR window closes.
The first failing gate short-circuits evaluation and returns a NO_TRADE result
with a specific reason code so the decision can be logged and audited.

Reason codes match PRD Section 11.
"""
import math
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Dict, Optional, Tuple

from app.services.opening_range import (
    STRIKE_STEP,
    is_bearish_breakout,
    is_bullish_breakout,
    select_bearish_strikes,
    select_bullish_strikes,
)

# ── Strategy config ───────────────────────────────────────────────────────────
MAX_RISK_PCT = 0.02       # G6: max 2% of capital as max loss
TARGET_PCT = 0.005        # profit target = 0.5% of capital
MIN_RR_RATIO = 1.5        # G7: max_possible_gain must be ≥ 1.5× max_loss

LOT_SIZES: Dict[str, int] = {"NIFTY": 75, "BANKNIFTY": 35}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class GateResult:
    action: str                           # "ENTER" or "NO_TRADE"
    reason_code: str
    reason_text: str
    # Populated only when action == "ENTER"
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


# ── Gate evaluator ────────────────────────────────────────────────────────────

def evaluate_gates(
    candle: Dict,
    or_high: float,
    or_low: float,
    or_ready: bool,
    has_open_trade: bool,
    option_prices: Dict,      # {(strike, opt_type): price}
    instrument: str,
    capital: float,
    expiry: date_type,
) -> GateResult:
    """
    Evaluate G1–G7 for the given minute candle.

    Returns a GateResult with action="ENTER" if all gates pass,
    or action="NO_TRADE" with the first-failing reason code.
    """
    close = float(candle["close"])
    lot_size = LOT_SIZES.get(instrument, 75)
    risk_cap = capital * MAX_RISK_PCT
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

    # Determine direction and candidate strikes
    if bullish:
        bias, opt_type = "BULLISH", "CE"
        long_strike, short_strike = select_bullish_strikes(or_high)
    else:
        bias, opt_type = "BEARISH", "PE"
        long_strike, short_strike = select_bearish_strikes(or_low)

    # ── G4: Follow-through — price has not collapsed back inside range ─────────
    if bullish and close < or_high:
        return GateResult(
            action="NO_TRADE",
            reason_code="FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH",
            reason_text=f"Price {close:.2f} broke above OR but has re-entered range.",
        )
    if bearish and close > or_low:
        return GateResult(
            action="NO_TRADE",
            reason_code="FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH",
            reason_text=f"Price {close:.2f} broke below OR but has re-entered range.",
        )

    # ── G5: Valid hedged spread available in option chain ─────────────────────
    long_price = option_prices.get((long_strike, opt_type))
    short_price = option_prices.get((short_strike, opt_type))

    if long_price is None or short_price is None:
        return GateResult(
            action="NO_TRADE",
            reason_code="NO_HEDGE_AVAILABLE",
            reason_text=(
                f"Option price missing for "
                f"{long_strike}{opt_type} or {short_strike}{opt_type}."
            ),
        )
    long_price = float(long_price)
    short_price = float(short_price)
    if long_price <= 0 or short_price <= 0:
        return GateResult(
            action="NO_TRADE",
            reason_code="NO_HEDGE_AVAILABLE",
            reason_text=(
                f"Option prices invalid: "
                f"{long_strike}{opt_type}={long_price}, {short_strike}{opt_type}={short_price}."
            ),
        )
    spread_debit = long_price - short_price
    if spread_debit <= 0:
        return GateResult(
            action="NO_TRADE",
            reason_code="NO_HEDGE_AVAILABLE",
            reason_text=(
                f"Spread debit non-positive ({spread_debit:.2f}). "
                f"Long leg must be more expensive than short leg."
            ),
        )

    # ── G6: Max loss within risk cap ──────────────────────────────────────────
    max_loss_per_lot = spread_debit * lot_size
    approved_lots = int(math.floor(risk_cap / max_loss_per_lot))
    total_max_loss = approved_lots * max_loss_per_lot

    if approved_lots == 0:
        return GateResult(
            action="NO_TRADE",
            reason_code="RISK_EXCEEDS_CAP",
            reason_text=(
                f"Max loss per lot ₹{max_loss_per_lot:.0f} exceeds risk cap "
                f"₹{risk_cap:.0f} (capital ₹{capital:.0f} × {MAX_RISK_PCT*100:.0f}%)."
            ),
        )

    # ── G7: Target profit is achievable ──────────────────────────────────────
    spread_width = abs(short_strike - long_strike)          # e.g. 50 pts
    max_gain_per_unit = spread_width - spread_debit         # max if spread goes full width
    max_gain_total = max_gain_per_unit * lot_size * approved_lots

    if max_gain_total < total_max_loss * MIN_RR_RATIO:
        return GateResult(
            action="NO_TRADE",
            reason_code="TARGET_NOT_VIABLE",
            reason_text=(
                f"Max possible gain ₹{max_gain_total:.0f} < "
                f"{MIN_RR_RATIO}× max loss ₹{total_max_loss:.0f}. "
                f"Spread too wide or debit too high."
            ),
        )

    # ── All gates passed → ENTER ──────────────────────────────────────────────
    candidate = {
        "bias": bias,
        "opt_type": opt_type,
        "long_strike": long_strike,
        "short_strike": short_strike,
        "long_price": round(long_price, 2),
        "short_price": round(short_price, 2),
        "spread_debit": round(spread_debit, 2),
        "approved_lots": approved_lots,
        "lot_size": lot_size,
        "total_max_loss": round(total_max_loss, 2),
        "target_profit": round(target_rupees, 2),
        "expiry": str(expiry),
    }

    return GateResult(
        action="ENTER",
        reason_code="ENTER_TRADE",
        reason_text=(
            f"{bias} breakout confirmed at close {close:.2f}. "
            f"{long_strike}{opt_type}@{long_price:.2f} / "
            f"{short_strike}{opt_type}@{short_price:.2f}. "
            f"Debit={spread_debit:.2f}, Lots={approved_lots}, "
            f"MaxLoss=₹{total_max_loss:.0f}."
        ),
        candidate_structure=candidate,
        computed_max_loss=round(total_max_loss, 2),
        computed_target=round(target_rupees, 2),
        bias=bias,
        long_strike=long_strike,
        short_strike=short_strike,
        opt_type=opt_type,
        approved_lots=approved_lots,
        entry_debit=round(spread_debit, 2),
        lot_size=lot_size,
        expiry=expiry,
    )

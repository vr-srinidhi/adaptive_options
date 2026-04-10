"""
Exit engine for open paper trades.

Evaluated every minute while a position is open.
Priority order: TARGET → STOP → TIME → HOLD

Reason codes match PRD Section 11.
"""
from dataclasses import dataclass
from datetime import time as time_type

# ── Config ────────────────────────────────────────────────────────────────────
SQUARE_OFF_TIME = time_type(15, 20)   # Force exit at or after 15:20


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ExitEval:
    action: str           # HOLD / EXIT_TARGET / EXIT_STOP / EXIT_TIME / EXIT_INVALIDATION
    reason: str
    long_price: float
    short_price: float
    current_spread: float
    mtm_per_lot: float
    total_mtm: float
    distance_to_target: float
    distance_to_stop: float


# ── Evaluator ─────────────────────────────────────────────────────────────────

def evaluate_exit(
    current_time: time_type,
    long_price: float,
    short_price: float,
    entry_debit: float,
    lot_size: int,
    approved_lots: int,
    total_max_loss: float,
    target_profit: float,
) -> ExitEval:
    """
    Compute the current MTM and decide whether to hold or exit.

    Formula (from PRD §6):
      current_spread_value = long_price - short_price
      mtm_per_lot          = (current_spread - entry_debit) × lot_size
      total_mtm            = mtm_per_lot × approved_lots
    """
    current_spread = float(long_price) - float(short_price)
    mtm_per_lot = (current_spread - float(entry_debit)) * int(lot_size)
    total_mtm = mtm_per_lot * int(approved_lots)

    dist_to_target = float(target_profit) - total_mtm
    dist_to_stop = total_mtm + float(total_max_loss)   # positive = cushion remaining

    # Priority 1 — Target hit
    if total_mtm >= float(target_profit):
        return ExitEval(
            action="EXIT_TARGET",
            reason=(
                f"Target reached. MTM ₹{total_mtm:.0f} ≥ target ₹{target_profit:.0f}. "
                f"Spread {entry_debit:.2f} → {current_spread:.2f}."
            ),
            long_price=long_price, short_price=short_price,
            current_spread=current_spread, mtm_per_lot=mtm_per_lot,
            total_mtm=total_mtm,
            distance_to_target=dist_to_target,
            distance_to_stop=dist_to_stop,
        )

    # Priority 2 — Stop hit
    if total_mtm <= -float(total_max_loss):
        return ExitEval(
            action="EXIT_STOP",
            reason=(
                f"Stop hit. MTM ₹{total_mtm:.0f} ≤ -₹{total_max_loss:.0f}. "
                f"Spread {entry_debit:.2f} → {current_spread:.2f}."
            ),
            long_price=long_price, short_price=short_price,
            current_spread=current_spread, mtm_per_lot=mtm_per_lot,
            total_mtm=total_mtm,
            distance_to_target=dist_to_target,
            distance_to_stop=dist_to_stop,
        )

    # Priority 3 — Time-based square-off
    if current_time >= SQUARE_OFF_TIME:
        return ExitEval(
            action="EXIT_TIME",
            reason=(
                f"Forced square-off at {current_time.strftime('%H:%M')}. "
                f"MTM ₹{total_mtm:.0f}."
            ),
            long_price=long_price, short_price=short_price,
            current_spread=current_spread, mtm_per_lot=mtm_per_lot,
            total_mtm=total_mtm,
            distance_to_target=dist_to_target,
            distance_to_stop=dist_to_stop,
        )

    # Hold
    return ExitEval(
        action="HOLD",
        reason=(
            f"Holding. MTM ₹{total_mtm:.0f} | "
            f"To target: ₹{dist_to_target:.0f} | "
            f"Cushion before stop: ₹{dist_to_stop:.0f}."
        ),
        long_price=long_price, short_price=short_price,
        current_spread=current_spread, mtm_per_lot=mtm_per_lot,
        total_mtm=total_mtm,
        distance_to_target=dist_to_target,
        distance_to_stop=dist_to_stop,
    )

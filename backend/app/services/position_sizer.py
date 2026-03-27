"""
Position sizer per PRD Section 7.6.
"""
from typing import Dict, List, Tuple


def size_position(
    capital: float,
    legs: List[Dict],
    lot_size: int,
    tick_size: int,
) -> Tuple[int, float, float]:
    """
    Returns (lots, max_profit_per_lot, max_loss_per_lot).

    Formula (PRD §7.6):
      max_risk          = capital × 0.02
      spread_width      = 2 × tick_size × lot_size   (2-tick wide spread)
      net_credit_per_lot = (sum of SELL premiums − sum of BUY premiums) × lot_size
      max_loss_per_lot  = spread_width − net_credit_per_lot
      lots              = floor(max_risk / max_loss_per_lot), minimum 1
    """
    max_risk = capital * 0.02

    # Net premium per unit (positive = credit received)
    net_credit_per_unit = sum(
        leg["ep"] if leg["act"] == "SELL" else -leg["ep"]
        for leg in legs
    )
    net_credit_per_lot = net_credit_per_unit * lot_size

    # Spread width (2-tick wide on each spread leg)
    spread_width = 2 * tick_size * lot_size

    max_profit_per_lot = max(net_credit_per_lot, 0.0)
    max_loss_per_lot = max(spread_width - net_credit_per_lot, spread_width * 0.05)

    lots = max(1, int(max_risk / max_loss_per_lot))

    return lots, max_profit_per_lot, max_loss_per_lot

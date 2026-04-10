"""
Position sizer per PRD Section 7.6.

v2 adds:
  size_long_position  — 1%-capital risk-based sizing for long option buys
  size_debit_spread   — 1%-capital risk-based sizing for debit spreads
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


# ── v2: Risk-based sizing for directional buys ────────────────────────────────

def size_long_position(
    entry_price: float,
    sl_price: float,
    capital: float,
    lot_size: int,
    max_risk_pct: float = 0.01,
) -> int:
    """
    Size a long option (CE or PE buy) using the 1%-risk rule.

    Risk per lot = (entry_price - sl_price) × lot_size
    Lots = floor(capital × max_risk_pct / risk_per_lot), minimum 1.
    Also capped so no more than 15% of capital is tied up in premium.
    """
    max_risk = capital * max_risk_pct
    risk_per_lot = max(entry_price - sl_price, 0.50) * lot_size
    lots_by_risk = int(max_risk / risk_per_lot)

    # Capital cap: premium cost ≤ 15% of capital
    max_capital = capital * 0.15
    capital_per_lot = entry_price * lot_size
    lots_by_capital = int(max_capital / capital_per_lot) if capital_per_lot > 0 else lots_by_risk

    return max(1, min(lots_by_risk, lots_by_capital))


def size_debit_spread(
    net_debit: float,
    capital: float,
    lot_size: int,
    max_risk_pct: float = 0.01,
) -> int:
    """
    Size a debit spread (BULL_CALL_SPREAD / BEAR_PUT_SPREAD).
    Max loss = net_debit per unit × lot_size.
    """
    max_risk = capital * max_risk_pct
    max_loss_per_lot = max(net_debit, 0.50) * lot_size
    return max(1, int(max_risk / max_loss_per_lot))

"""
Shared brokerage charges service.

A single source of truth for NSE F&O brokerage charges, extracted from
paper_engine.py so every strategy uses identical charge math.

Charge model (per round-trip, 4 orders: 2 SELL entry + 2 BUY exit)
  brokerage  = 4 × ₹20
  STT        = 0.05% × sell-side premium × qty   (only on SELL legs)
  exchange   = 0.053% × total premium × qty       (both sides)
  GST        = 18% × (brokerage + exchange txn fee)
  total      = brokerage + STT + exchange + GST

Public API
----------
compute_entry_charges(lots, lot_size, leg_prices)        → float
compute_exit_charges_estimate(lots, lot_size, leg_prices) → float
compute_total_charges(lots, lot_size, entry_prices, exit_prices) → float
"""
from __future__ import annotations

from typing import List, Tuple

# ── Rate constants ─────────────────────────────────────────────────────────────
_BROKERAGE_PER_ORDER = 20.0      # ₹20 flat per order (Zerodha)
_ORDERS_PER_ENTRY   = 2          # 2 SELL orders at entry (for short straddle / any short strategy)
_ORDERS_PER_EXIT    = 2          # 2 BUY orders at exit
_STT_RATE           = 0.0005     # 0.05% on sell-side premium
_EXCHANGE_TXN_RATE  = 0.00053    # 0.053% on total premium
_GST_RATE           = 0.18       # 18% on (brokerage + exchange)


def _charges(
    lots: int,
    lot_size: int,
    sell_prices: List[float],
    buy_prices: List[float],
    *,
    n_sell_orders: int,
    n_buy_orders: int,
) -> float:
    """Core charge formula for a given set of sell and buy leg prices."""
    qty = lots * lot_size
    sell_premium = sum(sell_prices) * qty
    buy_premium  = sum(buy_prices)  * qty
    total_premium = sell_premium + buy_premium

    brokerage = (n_sell_orders + n_buy_orders) * _BROKERAGE_PER_ORDER
    stt       = _STT_RATE * sell_premium
    exchange  = _EXCHANGE_TXN_RATE * total_premium
    gst       = _GST_RATE * (brokerage + exchange)

    return round(brokerage + stt + exchange + gst, 2)


def compute_entry_charges(
    lots: int,
    lot_size: int,
    sell_prices: List[float],
) -> float:
    """
    Charges for entering a short position (only SELL orders fire at entry).
    buy_prices is empty because exit hasn't happened yet.
    """
    return _charges(
        lots, lot_size,
        sell_prices=sell_prices,
        buy_prices=[0.0] * len(sell_prices),
        n_sell_orders=len(sell_prices),
        n_buy_orders=0,
    )


def compute_exit_charges_estimate(
    lots: int,
    lot_size: int,
    current_prices: List[float],
) -> float:
    """
    Estimated exit charges using current market prices as the projected fill.
    Only BUY orders fire at exit for a short position.
    """
    return _charges(
        lots, lot_size,
        sell_prices=[0.0] * len(current_prices),
        buy_prices=current_prices,
        n_sell_orders=0,
        n_buy_orders=len(current_prices),
    )


def compute_total_charges(
    lots: int,
    lot_size: int,
    entry_prices: List[float],
    exit_prices: List[float],
) -> float:
    """Full round-trip charges once both entry and exit prices are known."""
    return _charges(
        lots, lot_size,
        sell_prices=entry_prices,
        buy_prices=exit_prices,
        n_sell_orders=len(entry_prices),
        n_buy_orders=len(exit_prices),
    )

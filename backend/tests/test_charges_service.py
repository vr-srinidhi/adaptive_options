"""
Unit tests for charges_service.py.

All math is deterministic — no DB, no external dependencies.
"""
from app.services.charges_service import (
    compute_entry_charges,
    compute_exit_charges_estimate,
    compute_total_charges,
)

_LOTS = 1
_LOT_SIZE = 75


def test_entry_charges_uses_only_sell_side():
    # STT is 0.05% of sell-side premium × qty
    # For 1 lot, lot_size=75, sell price = 100 → premium = 7500
    # brokerage = 1 × 20 = 20  (entry: 1 sell order, 0 buy)
    # stt       = 0.0005 × 7500 = 3.75
    # exchange  = 0.00053 × 7500 = 3.975
    # gst       = 0.18 × (20 + 3.975) ≈ 4.31
    # total ≈ 32.04
    charges = compute_entry_charges(1, 75, [100.0])
    assert 25 < charges < 40


def test_entry_charges_zero_price():
    # Edge: zero premium — brokerage still fires
    charges = compute_entry_charges(1, 75, [0.0])
    assert charges > 0   # brokerage always present


def test_exit_charges_estimate_uses_buy_side():
    # Exit: 2 BUY orders, no STT (STT is sell-side only)
    charges = compute_exit_charges_estimate(1, 75, [50.0, 50.0])
    # brokerage = 2 × 20 = 40
    # exchange  = 0.00053 × (50+50)×75 = 0.00053 × 7500 = 3.975
    # gst       = 0.18 × (40 + 3.975) = 7.9155
    # stt = 0 (no sell at exit)
    assert 40 < charges < 60


def test_total_charges_equals_entry_plus_exit_brokerage_combined():
    # Round-trip: entry + exit brokerage is 4 × 20 = 80, plus tax components
    charges = compute_total_charges(1, 75, [100.0, 100.0], [60.0, 60.0])
    assert charges > 80


def test_total_charges_monotone_with_lots():
    # More lots → higher absolute charges
    c1 = compute_total_charges(1, 75, [100.0], [60.0])
    c2 = compute_total_charges(3, 75, [100.0], [60.0])
    assert c2 > c1


def test_total_charges_monotone_with_premium():
    # Higher premium → higher STT + exchange
    low  = compute_total_charges(1, 75, [50.0], [30.0])
    high = compute_total_charges(1, 75, [200.0], [120.0])
    assert high > low


def test_entry_charges_two_legs():
    # Short straddle: SELL CE + SELL PE
    charges_2leg = compute_entry_charges(1, 75, [100.0, 100.0])
    charges_1leg = compute_entry_charges(1, 75, [100.0])
    # 2-leg has more sell premium → more STT + exchange
    assert charges_2leg > charges_1leg


def test_charges_rounded_to_two_decimals():
    c = compute_entry_charges(2, 75, [123.45])
    assert round(c, 2) == c

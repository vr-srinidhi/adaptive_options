"""
Unit tests for app/services/position_sizer.py

Positive tests: correct lot sizing and P&L bounds.
Negative tests: edge cases that must never break the 2% rule or minimum-1-lot floor.
"""
import pytest

from app.services.position_sizer import size_position


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_legs(sell_premium: float, buy_premium: float, strategy="BULL_PUT_SPREAD"):
    """
    Two-legged spread: one SELL and one BUY.
    sell_premium > buy_premium → net credit position.
    """
    if strategy == "BULL_PUT_SPREAD":
        return [
            {"act": "SELL", "typ": "PE", "strike": 21900, "delta": -0.28, "ep": sell_premium},
            {"act": "BUY",  "typ": "PE", "strike": 21800, "delta": -0.12, "ep": buy_premium},
        ]
    return [
        {"act": "SELL", "typ": "CE", "strike": 22100, "delta": 0.28, "ep": sell_premium},
        {"act": "BUY",  "typ": "CE", "strike": 22200, "delta": 0.12, "ep": buy_premium},
    ]


def _make_ic_legs(sell_ce, buy_ce, sell_pe, buy_pe):
    """Four-legged Iron Condor."""
    return [
        {"act": "SELL", "typ": "CE", "strike": 22150, "delta":  0.17, "ep": sell_ce},
        {"act": "BUY",  "typ": "CE", "strike": 22250, "delta":  0.08, "ep": buy_ce},
        {"act": "SELL", "typ": "PE", "strike": 21850, "delta": -0.17, "ep": sell_pe},
        {"act": "BUY",  "typ": "PE", "strike": 21750, "delta": -0.08, "ep": buy_pe},
    ]


# ── Positive tests ────────────────────────────────────────────────────────────

class TestSizePositionPositive:
    LOT_SIZE = 50     # Nifty
    TICK_SIZE = 50    # Nifty

    def test_returns_three_element_tuple(self):
        legs = _make_legs(115.0, 97.0)
        result = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert len(result) == 3

    def test_lots_is_integer(self):
        legs = _make_legs(115.0, 97.0)
        lots, _, _ = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert isinstance(lots, int)

    def test_max_profit_per_lot_is_float(self):
        legs = _make_legs(115.0, 97.0)
        _, max_profit, _ = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert isinstance(max_profit, float)

    def test_minimum_1_lot_returned(self):
        legs = _make_legs(115.0, 97.0)
        lots, _, _ = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots >= 1

    def test_example_from_prd_docstring(self):
        """
        PRD worked example:
          capital=500,000, SELL=115, BUY=97
          net_credit/lot = (115-97)*50 = 900
          spread_width   = 2*50*50 = 5000
          max_loss/lot   = 5000-900 = 4100
          lots           = floor(10000/4100) = 2
        """
        legs = _make_legs(115.0, 97.0)
        lots, max_profit, max_loss = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots == 2
        assert max_profit == pytest.approx(900.0)
        assert max_loss == pytest.approx(4100.0)

    def test_larger_capital_gives_more_lots(self):
        legs = _make_legs(115.0, 97.0)
        lots_small, _, _ = size_position(100_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        lots_large, _, _ = size_position(2_000_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots_large >= lots_small

    def test_max_profit_is_nonnegative(self):
        legs = _make_legs(115.0, 97.0)
        _, max_profit, _ = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert max_profit >= 0.0

    def test_max_loss_is_positive(self):
        legs = _make_legs(115.0, 97.0)
        _, _, max_loss = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert max_loss > 0.0

    def test_iron_condor_4_legs_positive_net_credit(self):
        legs = _make_ic_legs(sell_ce=80.0, buy_ce=50.0, sell_pe=80.0, buy_pe=50.0)
        lots, max_profit, max_loss = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots >= 1
        assert max_profit > 0

    def test_capital_doubles_approximately_doubles_lots(self):
        legs = _make_legs(115.0, 97.0)
        lots_base, _, _ = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        lots_double, _, _ = size_position(1_000_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots_double == lots_base * 2

    def test_banknifty_lot_tick_sizes(self):
        """BankNifty: lot=25, tick=100."""
        legs = _make_legs(250.0, 190.0)
        lots, _, _ = size_position(500_000, legs, lot_size=25, tick_size=100)
        assert lots >= 1


# ── Negative / edge-case tests ────────────────────────────────────────────────

class TestSizePositionNegative:
    LOT_SIZE = 50
    TICK_SIZE = 50

    def test_tiny_capital_still_gives_1_lot(self):
        """Even with ₹50,000 capital, minimum is 1 lot (PRD floor)."""
        legs = _make_legs(115.0, 97.0)
        lots, _, _ = size_position(50_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots == 1

    def test_net_credit_exceeds_spread_width_max_loss_floored(self):
        """
        If net credit ≥ spread_width, max_loss would be ≤ 0.
        The code floors it at 5% of spread_width.
        """
        # spread_width = 2*50*50 = 5000; make credit > 5000/50 = 100 per unit
        legs = _make_legs(sell_premium=200.0, buy_premium=0.0)  # net = 200/unit → 10000/lot
        lots, max_profit, max_loss = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        spread_width = 2 * self.TICK_SIZE * self.LOT_SIZE  # 5000
        assert max_loss == pytest.approx(spread_width * 0.05)
        assert lots >= 1

    def test_zero_premium_legs_still_returns_valid(self):
        """Legs with ep=0 (edge: no credit collected)."""
        legs = _make_legs(0.0, 0.0)
        lots, max_profit, max_loss = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots >= 1
        assert max_profit == 0.0
        assert max_loss > 0.0

    def test_very_high_premium_does_not_crash(self):
        legs = _make_legs(9999.0, 1.0)
        lots, max_profit, max_loss = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert lots >= 1

    def test_max_loss_never_zero(self):
        """max_loss_per_lot must always be positive so we avoid division by zero in lot calc."""
        legs = _make_legs(100.0, 0.0)  # net_credit_per_lot = 5000 = spread_width
        _, _, max_loss = size_position(500_000, legs, self.LOT_SIZE, self.TICK_SIZE)
        assert max_loss > 0.0

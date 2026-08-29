"""Tests for post-hoc execution costing.

The book math is where a wrong answer would quietly mis-state the strategy's
edge, so it is tested directly rather than through the report.
"""
from datetime import datetime

import pytest

from app.services.execution_cost import (
    FillCost, cost_session, index_depth, mid_price, nearest_depth,
    price_fill, slippage_per_unit, walk_book,
)

TS = datetime(2026, 9, 1, 9, 50, 10)


def _depth(bid=65.40, ask=66.10, buy=None, sell=None, strike=24300, opt="CE", ts=TS):
    return {
        "timestamp": ts, "strike": strike, "option_type": opt,
        "bid": bid, "ask": ask,
        "depth_json": {
            "buy": buy if buy is not None else [{"price": bid, "quantity": 1500}],
            "sell": sell if sell is not None else [{"price": ask, "quantity": 1200}],
        },
    }


# ── walk_book ────────────────────────────────────────────────────────────────
def test_fills_entirely_at_the_touch_when_size_rests_there():
    avg, filled = walk_book([{"price": 65.4, "quantity": 1500}], 975, buying=False)
    assert avg == 65.4 and filled == 975


def test_walks_deeper_when_size_exceeds_the_touch():
    # 975 needed, only 500 at the touch -> 500@65.40 + 475@65.20
    avg, filled = walk_book(
        [{"price": 65.4, "quantity": 500}, {"price": 65.2, "quantity": 1000}],
        975, buying=False,
    )
    assert filled == 975
    assert avg == pytest.approx((500 * 65.4 + 475 * 65.2) / 975)
    assert avg < 65.4          # selling deeper is worse


def test_buying_walks_asks_upward():
    avg, filled = walk_book(
        [{"price": 66.1, "quantity": 500}, {"price": 66.4, "quantity": 1000}],
        975, buying=True,
    )
    assert filled == 975
    assert avg == pytest.approx((500 * 66.1 + 475 * 66.4) / 975)
    assert avg > 66.1          # buying deeper is worse


def test_reports_shortfall_when_book_cannot_fill():
    avg, filled = walk_book([{"price": 65.4, "quantity": 300}], 975, buying=False)
    assert filled == 300 and avg == 65.4


def test_level_order_is_not_trusted():
    """Zerodha returns best-first, but a wrong order must not flatter the fill."""
    scrambled = [{"price": 65.0, "quantity": 500}, {"price": 65.4, "quantity": 500}]
    avg, _ = walk_book(scrambled, 1000, buying=False)
    ordered, _ = walk_book(list(reversed(scrambled)), 1000, buying=False)
    assert avg == pytest.approx(ordered)


def test_empty_and_degenerate_books():
    assert walk_book([], 975, buying=False) == (None, 0)
    assert walk_book([{"price": 65.4, "quantity": 1500}], 0, buying=False) == (None, 0)
    assert walk_book([{"price": 0, "quantity": 100}], 10, buying=False) == (None, 0)
    assert walk_book([{"price": "x", "quantity": None}], 10, buying=False) == (None, 0)


# ── slippage sign ────────────────────────────────────────────────────────────
def test_slippage_sign_is_a_cost_in_both_directions():
    # Sold below the assumed price -> cost. Bought above it -> cost.
    assert slippage_per_unit("SELL", 65.8, 65.4) == pytest.approx(0.4)
    assert slippage_per_unit("BUY", 65.8, 66.1) == pytest.approx(0.3)
    # And a better-than-assumed fill is a credit, not a cost.
    assert slippage_per_unit("SELL", 65.0, 65.4) == pytest.approx(-0.4)


def test_mid_price():
    assert mid_price(65.4, 66.1) == pytest.approx(65.75)
    assert mid_price(None, 66.1) is None
    assert mid_price(0, 66.1) is None


# ── price_fill ───────────────────────────────────────────────────────────────
def test_selling_at_last_price_costs_the_half_spread_or_worse():
    f = price_fill(label="entry", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=65.80, timestamp=TS, depth=_depth())
    assert f.walked_price == 65.4
    assert f.cost_walked == pytest.approx((65.80 - 65.40) * 975, abs=0.01)
    assert f.cost_mid == pytest.approx((65.80 - 65.75) * 975, abs=0.01)
    assert f.cost_mid < f.cost_walked        # mid is the optimistic bound


def test_missing_depth_is_reported_not_assumed_costless():
    f = price_fill(label="entry", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=65.80, timestamp=TS, depth=None)
    assert f.cost_walked is None and f.priced is False
    assert f.note == "no depth captured"


def test_missing_recorded_price_is_reported():
    f = price_fill(label="entry", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=None, timestamp=TS, depth=_depth())
    assert f.priced is False and f.note == "no recorded price"


def test_falls_back_to_touch_when_ladder_absent():
    d = _depth(); d["depth_json"] = {}
    f = price_fill(label="entry", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=65.80, timestamp=TS, depth=d)
    assert f.walked_price == 65.4 and "no ladder" in f.note


def test_shortfall_is_surfaced():
    d = _depth(buy=[{"price": 65.4, "quantity": 300}])
    f = price_fill(label="entry", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=65.80, timestamp=TS, depth=d)
    assert f.shortfall_qty == 675 and "book short by 675" in f.note


# ── depth lookup ─────────────────────────────────────────────────────────────
def test_nearest_depth_picks_the_closest_snapshot():
    snaps = [_depth(ts=datetime(2026, 9, 1, 9, 50, 0)),
             _depth(ts=datetime(2026, 9, 1, 9, 50, 12))]
    got = nearest_depth(snaps, datetime(2026, 9, 1, 9, 50, 10))
    assert got["timestamp"] == datetime(2026, 9, 1, 9, 50, 12)


def test_stale_depth_is_rejected_rather_than_used():
    """A book from ten minutes away is not evidence about this fill."""
    snaps = [_depth(ts=datetime(2026, 9, 1, 9, 40, 0))]
    assert nearest_depth(snaps, datetime(2026, 9, 1, 9, 50, 10)) is None


def test_index_depth_groups_by_contract():
    idx = index_depth([_depth(strike=24300, opt="CE"), _depth(strike=24200, opt="PE")])
    assert set(idx) == {(24300, "CE"), (24200, "PE")}


# ── session roll-up ──────────────────────────────────────────────────────────
def _fill(side, strike, opt, price, qty=975, ts=TS, label="x"):
    return {"label": label, "side": side, "option_type": opt, "strike": strike,
            "quantity": qty, "price": price, "timestamp": ts}


def test_session_totals_sum_both_bounds():
    sc = cost_session(
        run_id="r1", trade_date="2026-09-01",
        fills=[_fill("SELL", 24300, "CE", 65.80), _fill("SELL", 24200, "PE", 24.20)],
        depth_rows=[_depth(strike=24300, opt="CE"),
                    _depth(strike=24200, opt="PE", bid=23.70, ask=24.30)],
    )
    assert sc.coverage == 1.0
    assert sc.cost_walked == pytest.approx((65.80 - 65.40) * 975 + (24.20 - 23.70) * 975, abs=0.05)
    assert sc.cost_mid < sc.cost_walked


def test_coverage_reflects_partially_captured_sessions():
    sc = cost_session(
        run_id="r1", trade_date="2026-09-01",
        fills=[_fill("SELL", 24300, "CE", 65.80), _fill("SELL", 99999, "PE", 24.20)],
        depth_rows=[_depth(strike=24300, opt="CE")],
    )
    assert sc.coverage == 0.5
    assert len(sc.unpriced_fills) == 1
    # The cost reported covers only what could be priced -- never extrapolated.
    assert sc.cost_walked == pytest.approx((65.80 - 65.40) * 975, abs=0.05)


def test_session_with_no_depth_reports_zero_coverage_not_zero_cost():
    sc = cost_session(run_id="r1", trade_date="2026-09-01",
                      fills=[_fill("SELL", 24300, "CE", 65.80)], depth_rows=[])
    assert sc.coverage == 0.0 and sc.cost_walked == 0.0
    assert sc.summary()["fills_priced"] == 0       # the honest signal


def test_empty_session_is_safe():
    sc = cost_session(run_id="r1", trade_date="2026-09-01", fills=[], depth_rows=[])
    assert sc.coverage == 0.0 and sc.summary()["fills_total"] == 0

"""Tests for post-hoc execution costing.

The book math is where a wrong answer would quietly mis-state the strategy's
edge, so it is tested directly rather than through the report.
"""
from datetime import date, datetime

import pytest

from app.services.execution_cost import (
    FillCost, contract_key, cost_session, index_depth, mid_price, nearest_depth,
    price_fill, slippage_per_unit, walk_book,
)

TS = datetime(2026, 9, 1, 9, 50, 10)


def _depth(bid=65.40, ask=66.10, buy=None, sell=None, strike=24300, opt="CE", ts=TS,
           expiry=date(2026, 9, 1)):
    return {
        "timestamp": ts, "strike": strike, "option_type": opt, "expiry_date": expiry,
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


def test_shortfall_is_surfaced():
    d = _depth(buy=[{"price": 65.4, "quantity": 300}])
    f = price_fill(label="entry", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=65.80, timestamp=TS, depth=d)
    assert f.shortfall_qty == 675 and "book covered 300 of 975" in f.note


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
    e = date(2026, 9, 1)
    idx = index_depth([_depth(strike=24300, opt="CE"), _depth(strike=24200, opt="PE")])
    assert set(idx) == {(e, 24300, "CE"), (e, 24200, "PE")}


# ── session roll-up ──────────────────────────────────────────────────────────
def _fill(side, strike, opt, price, qty=975, ts=TS, label="x", expiry=date(2026, 9, 1)):
    return {"label": label, "side": side, "option_type": opt, "strike": strike,
            "expiry_date": expiry, "quantity": qty, "price": price, "timestamp": ts}


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


# ── Contract identity ────────────────────────────────────────────────────────
def test_same_strike_on_a_different_expiry_is_a_different_contract():
    """A 24300 CE exists on every expiry; matching on strike alone would let a
    next-expiry book price a current-expiry fill."""
    near = _depth(strike=24300, opt="CE", expiry=date(2026, 9, 1))
    far = _depth(strike=24300, opt="CE", expiry=date(2026, 9, 8), bid=90.0, ask=91.0)
    idx = index_depth([near, far])
    assert len(idx) == 2
    assert contract_key(near) != contract_key(far)


def test_fill_is_not_priced_off_another_expirys_book():
    store_only_far = [_depth(strike=24300, opt="CE", expiry=date(2026, 9, 8),
                             bid=90.0, ask=91.0)]
    sc = cost_session(run_id="r", trade_date="2026-09-01",
                      fills=[_fill("SELL", 24300, "CE", 65.80, expiry=date(2026, 9, 1))],
                      depth_rows=store_only_far)
    assert sc.coverage == 0.0
    assert sc.fills[0].note == "no depth captured"


# ── Partial fills must not be imputed ────────────────────────────────────────
def test_partial_fill_costs_only_the_visible_quantity():
    """300 of 975 visible must not be charged as though all 975 filled."""
    d = _depth(buy=[{"price": 65.4, "quantity": 300}])
    f = price_fill(label="x", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=65.80, timestamp=TS, depth=d)
    assert f.filled_qty == 300 and f.shortfall_qty == 675
    assert f.cost_walked == pytest.approx((65.80 - 65.40) * 300, abs=0.01)
    assert f.priced is True and f.fully_priced is False


def test_no_ladder_is_left_unpriced_rather_than_costed_at_the_touch():
    """Costing a whole order at the touch assumes unlimited size there."""
    d = _depth(); d["depth_json"] = {}
    f = price_fill(label="x", side="SELL", option_type="CE", strike=24300,
                   quantity=975, assumed_price=65.80, timestamp=TS, depth=d)
    assert f.priced is False and f.cost_walked is None
    assert f.note == "no ladder"


def test_quantity_coverage_reports_the_shortfall():
    sc = cost_session(run_id="r", trade_date="2026-09-01",
                      fills=[_fill("SELL", 24300, "CE", 65.80, qty=975)],
                      depth_rows=[_depth(buy=[{"price": 65.4, "quantity": 300}])])
    assert sc.coverage == 1.0            # the fill was priced
    assert sc.quantity_coverage == pytest.approx(300 / 975, abs=0.001)
    assert sc.complete is False          # but not end to end


# ── The midpoint bound must not be fabricated ────────────────────────────────
def test_one_sided_book_withholds_the_midpoint_bound():
    """A walked cost with no midpoint must not report cost_mid = 0."""
    d = _depth(); d["ask"] = None; d["depth_json"]["sell"] = []
    sc = cost_session(run_id="r", trade_date="2026-09-01",
                      fills=[_fill("SELL", 24300, "CE", 65.80)], depth_rows=[d])
    assert sc.cost_walked != 0
    assert sc.cost_mid is None
    assert sc.complete is False
    assert sc.summary()["cost_mid"] is None


def test_complete_only_when_every_fill_is_fully_priced_on_both_bounds():
    sc = cost_session(run_id="r", trade_date="2026-09-01",
                      fills=[_fill("SELL", 24300, "CE", 65.80)],
                      depth_rows=[_depth()])
    assert sc.complete is True
    sc2 = cost_session(run_id="r", trade_date="2026-09-01",
                       fills=[_fill("SELL", 24300, "CE", 65.80),
                              _fill("SELL", 24200, "PE", 24.20)],
                       depth_rows=[_depth()])
    assert sc2.complete is False         # second fill has no depth

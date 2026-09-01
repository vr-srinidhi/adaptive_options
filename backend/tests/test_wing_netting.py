"""Tests for netting lock wings against protection the delta hedge already holds.

The lock and the delta hedge react to the same adverse move, so they land on
the same contract often enough that double-buying is the normal case, not an
edge case. 31 Aug 2026: the hedge bought 375 of the 24150 CE at 53.35, and the
lock bought 975 more of that same strike at 66.85 ninety-nine seconds later.
"""
import pytest

from app.services.delta_hedge import wing_lots_after_hedges


def _hedge(strike, opt, lots, side="BUY"):
    return {"strike": strike, "option_type": opt, "lots": lots, "side": side}


def test_no_hedges_means_full_wing():
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13, delta_hedges=[]
    ) == 13
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13, delta_hedges=None
    ) == 13


def test_nets_a_hedge_on_the_same_contract():
    """The 31 Aug case: 5 lots already held, so buy 8 rather than 13."""
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13,
        delta_hedges=[_hedge(24150, "CE", 5)],
    ) == 8


def test_ignores_hedges_on_a_different_strike_or_type():
    hedges = [_hedge(23950, "PE", 5), _hedge(24200, "CE", 3)]
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13, delta_hedges=hedges
    ) == 13


def test_sums_multiple_hedges_on_the_same_contract():
    hedges = [_hedge(24150, "CE", 5), _hedge(24150, "CE", 3)]
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13, delta_hedges=hedges
    ) == 5


def test_fully_covered_wing_buys_nothing():
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13,
        delta_hedges=[_hedge(24150, "CE", 13)],
    ) == 0


def test_never_returns_negative():
    """Protection beyond approved_lots must not produce a negative order."""
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13,
        delta_hedges=[_hedge(24150, "CE", 20)],
    ) == 0


def test_sell_legs_are_not_counted_as_protection():
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13,
        delta_hedges=[_hedge(24150, "CE", 5, side="SELL")],
    ) == 13


def test_malformed_hedge_entries_are_skipped_not_fatal():
    hedges = [
        {"strike": 24150, "option_type": "CE"},              # no lots
        {"strike": 24150, "option_type": "CE", "lots": None},
        {"strike": 24150, "option_type": "CE", "lots": "x"},  # unparseable
        _hedge(24150, "CE", 4),
    ]
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=13, delta_hedges=hedges
    ) == 9


def test_zero_or_negative_approved_lots():
    assert wing_lots_after_hedges(
        wing_strike=24150, option_type="CE", approved_lots=0, delta_hedges=[]
    ) == 0


def test_total_protection_is_conserved():
    """Netting must not reduce protection -- hedge lots + wing lots == approved."""
    for held in range(0, 14):
        wing = wing_lots_after_hedges(
            wing_strike=24150, option_type="CE", approved_lots=13,
            delta_hedges=[_hedge(24150, "CE", held)] if held else [],
        )
        assert held + wing == 13

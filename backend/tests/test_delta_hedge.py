from datetime import date, datetime, timedelta

import pytest

from app.services.delta_hedge import (
    black_scholes_delta,
    black_scholes_price,
    hedge_lots_for_delta,
    implied_volatility,
    parse_delta_hedge_settings,
    signed_position_delta,
    unit_delta_for_option,
    years_to_expiry,
)


def test_delta_hedge_defaults_disabled():
    settings = parse_delta_hedge_settings({})

    assert settings.enabled is False
    assert settings.delta_threshold == 150
    assert settings.hedge_action == "BUY_WING"
    assert settings.max_hedge_triggers == 3


def test_delta_hedge_accepts_nested_prd_shape():
    settings = parse_delta_hedge_settings({
        "deltaHedge": {
            "enabled": True,
            "deltaThreshold": 175,
            "hedgeAction": "BUY_WING",
            "hedgeQtyMode": "PARTIAL",
            "maxHedgeTriggers": 2,
            "reentryBuffer": 25,
        }
    })

    assert settings.enabled is True
    assert settings.delta_threshold == 175
    assert settings.max_hedge_triggers == 2
    assert settings.reentry_buffer == 25


def test_delta_hedge_accepts_flat_ui_shape():
    settings = parse_delta_hedge_settings({
        "delta_hedge_enabled": True,
        "delta_threshold": 125,
        "hedge_action": "BUY_WING",
        "hedge_qty_mode": "PARTIAL",
        "max_hedge_triggers": 4,
        "reentry_buffer": 40,
    })

    assert settings.enabled is True
    assert settings.delta_threshold == 125
    assert settings.max_hedge_triggers == 4
    assert settings.reentry_buffer == 40


def test_implied_volatility_round_trips_from_black_scholes_price():
    now = datetime(2026, 5, 7, 10, 15)
    years = years_to_expiry(now, date(2026, 5, 14))
    price = black_scholes_price(spot=24350, strike=24350, years=years, sigma=0.18, option_type="CE")

    iv = implied_volatility(price, spot=24350, strike=24350, years=years, option_type="CE", initial_sigma=0.12)

    assert iv == pytest.approx(0.18, abs=0.01)


def test_short_straddle_net_delta_turns_negative_on_rally():
    ts = datetime.now()
    expiry = (ts + timedelta(days=7)).date()
    qty = 650

    ce_delta = signed_position_delta(
        side="SELL",
        option_type="CE",
        price=240,
        spot=24550,
        strike=24350,
        quantity=qty,
        timestamp=ts,
        expiry_date=expiry,
        vix=14,
    )
    pe_delta = signed_position_delta(
        side="SELL",
        option_type="PE",
        price=80,
        spot=24550,
        strike=24350,
        quantity=qty,
        timestamp=ts,
        expiry_date=expiry,
        vix=14,
    )

    assert ce_delta + pe_delta < 0


def test_long_ce_hedge_offsets_negative_delta():
    delta = black_scholes_delta(spot=24550, strike=24450, years=7 / 365, sigma=0.14, option_type="CE")

    assert delta > 0


# ── Hedge sizing ──────────────────────────────────────────────────────────────
# The 12-Aug-2026 live session bought 13 lots to correct a +152 delta gap and
# landed at -293, inverting the book. These pin the corrected behaviour.

_AUG12_UNIT_DELTA = -0.4569   # implied per-unit delta of the 24300 PE that day


def test_partial_sizing_neutralises_without_inverting():
    lots = hedge_lots_for_delta(
        net_delta=152.05,
        option_delta=_AUG12_UNIT_DELTA,
        lot_size=75,
        max_lots=13,
    )

    assert lots == 4
    residual = 152.05 + lots * 75 * _AUG12_UNIT_DELTA
    assert residual == pytest.approx(14.98, abs=0.5)
    assert residual > 0          # same sign as before: the book never flipped


@pytest.mark.parametrize("gap", [60, 100, 152.05, 180, 250, 400, 600, 5000])
def test_partial_sizing_never_flips_the_sign(gap):
    """Rounding down is what guarantees this; it must hold at every gap size."""
    lots = hedge_lots_for_delta(
        net_delta=gap, option_delta=_AUG12_UNIT_DELTA, lot_size=75, max_lots=13
    )

    assert lots <= 13
    assert gap + lots * 75 * _AUG12_UNIT_DELTA >= 0


def test_partial_sizing_is_symmetric_for_negative_delta():
    """A CE-side breach sizes the same as the mirrored PE-side breach."""
    ce = hedge_lots_for_delta(net_delta=-152.05, option_delta=0.4569, lot_size=75, max_lots=13)
    pe = hedge_lots_for_delta(net_delta=152.05, option_delta=-0.4569, lot_size=75, max_lots=13)

    assert ce == pe == 4


def test_gap_too_small_for_one_lot_returns_zero():
    lots = hedge_lots_for_delta(
        net_delta=20, option_delta=_AUG12_UNIT_DELTA, lot_size=75, max_lots=13
    )

    assert lots == 0


def test_sizing_is_capped_at_available_lots():
    lots = hedge_lots_for_delta(
        net_delta=99_999, option_delta=_AUG12_UNIT_DELTA, lot_size=75, max_lots=13
    )

    assert lots == 13


def test_full_mode_preserves_legacy_full_size_behaviour():
    lots = hedge_lots_for_delta(
        net_delta=152.05, option_delta=_AUG12_UNIT_DELTA, lot_size=75, max_lots=13, mode="FULL"
    )

    assert lots == 13


def test_near_zero_option_delta_is_not_hedgeable():
    """A far-OTM wing barely moves the book; dividing by it would explode."""
    assert hedge_lots_for_delta(
        net_delta=500, option_delta=0.0001, lot_size=75, max_lots=13
    ) == 0
    assert hedge_lots_for_delta(
        net_delta=500, option_delta=None, lot_size=75, max_lots=13
    ) == 0


def test_unit_delta_matches_position_delta_per_unit():
    ts = datetime(2026, 8, 12, 11, 2)
    expiry = date(2026, 8, 18)

    unit = unit_delta_for_option(
        option_type="PE", price=110.70, spot=24312, strike=24300,
        timestamp=ts, expiry_date=expiry, vix=None,
    )
    position = signed_position_delta(
        side="BUY", option_type="PE", price=110.70, spot=24312, strike=24300,
        quantity=975, timestamp=ts, expiry_date=expiry, vix=None,
    )

    assert unit < 0
    assert position == pytest.approx(unit * 975, rel=1e-9)

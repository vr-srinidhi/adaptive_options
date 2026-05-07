from datetime import date, datetime, timedelta

import pytest

from app.services.delta_hedge import (
    black_scholes_delta,
    black_scholes_price,
    implied_volatility,
    parse_delta_hedge_settings,
    signed_position_delta,
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

"""Per-leg marks broadcast on the live MTM tick.

These rows are what the Positions & MTM panel renders, so they must agree with
the header's gross MTM exactly -- a panel whose rows do not sum to the number
above them is worse than no panel.
"""
from datetime import date, datetime

import pytest

from app.services.delta_hedge import black_scholes_price, years_to_expiry
from app.services.live_paper_engine import _leg_marks

LOT = 75
APPROVED = 13


ENTRY_TS = datetime(2026, 9, 8, 9, 50, 3)
LOCK_TS = datetime(2026, 9, 8, 11, 56, 15)


def _marks(*, wings_locked=False, wing_lots=None, hedges=None,
           straddle_cur=(40.0, 60.0), wing_cur=(12.0, 9.0),
           entry_ts=ENTRY_TS, lock_ts=LOCK_TS,
           spot=None, vix=None, expiry_date=None, now=None):
    return _leg_marks(
        ["s0", "s1"], [48.35, 46.25], list(straddle_cur), 23700, entry_ts,
        ["w0", "w1"], [16.0, 14.0], list(wing_cur), [23800, 23600], lock_ts,
        wing_lots, wings_locked, hedges or [], LOT, APPROVED,
        spot=spot, vix=vix, expiry_date=expiry_date, now=now,
    )


def test_straddle_legs_are_marked_with_sell_sign():
    m = _marks()
    assert [x["leg_index"] for x in m] == [0, 1]
    # SELL earns entry - current.
    assert m[0]["pnl"] == pytest.approx((48.35 - 40.0) * 975, abs=0.01)
    assert m[1]["pnl"] == pytest.approx((46.25 - 60.0) * 975, abs=0.01)
    assert m[0]["side"] == "SELL" and m[0]["strike"] == 23700
    assert m[0]["quantity"] == 975


def test_hedge_legs_use_their_own_lot_count():
    """A 5-lot hedge must not be marked at the straddle's 13."""
    h = [{"id": "h0", "side": "BUY", "option_type": "PE", "strike": 23600,
          "entry_price": 16.05, "last_price": 20.0, "lots": 5}]
    m = _marks(hedges=h)
    hedge = m[-1]
    assert hedge["leg_index"] == 4 and hedge["quantity"] == 375
    # BUY earns current - entry.
    assert hedge["pnl"] == pytest.approx((20.0 - 16.05) * 375, abs=0.01)


def test_netted_wing_with_zero_lots_is_omitted():
    """A fully netted wing buys nothing, so it must not appear as a position."""
    m = _marks(wings_locked=True, wing_lots=[0, 13])
    idx = [x["leg_index"] for x in m]
    assert 2 not in idx, "zero-lot wing should not be listed"
    assert 3 in idx
    assert next(x for x in m if x["leg_index"] == 3)["quantity"] == 975


def test_partially_netted_wing_is_marked_at_its_netted_size():
    m = _marks(wings_locked=True, wing_lots=[8, 13])
    w = next(x for x in m if x["leg_index"] == 2)
    assert w["quantity"] == 600
    assert w["pnl"] == pytest.approx((12.0 - 16.0) * 600, abs=0.01)


def test_wings_absent_until_locked():
    assert all(x["leg_index"] not in (2, 3) for x in _marks(wings_locked=False))


def test_missing_price_yields_no_pnl_rather_than_zero():
    m = _marks(straddle_cur=(None, 60.0))
    assert m[0]["pnl"] is None          # not 0.0 -- absence, not break-even
    assert m[1]["pnl"] is not None


def test_rows_sum_to_the_same_gross_the_header_reports():
    """The panel's rows must reconcile with gross MTM, or it misleads."""
    hedges = [
        {"id": "h0", "side": "BUY", "option_type": "PE", "strike": 23600,
         "entry_price": 16.05, "last_price": 20.0, "lots": 5},
        {"id": "h1", "side": "BUY", "option_type": "CE", "strike": 23800,
         "entry_price": 21.0, "last_price": 5.05, "lots": 8},
    ]
    m = _marks(wings_locked=True, wing_lots=[8, 13], hedges=hedges)

    expected = (
        (48.35 - 40.0) * 975 + (46.25 - 60.0) * 975          # straddle
        + (12.0 - 16.0) * 600 + (9.0 - 14.0) * 975           # wings
        + (20.0 - 16.05) * 375 + (5.05 - 21.0) * 600         # hedges
    )
    assert sum(x["pnl"] for x in m) == pytest.approx(expected, abs=0.01)


def test_legacy_session_without_per_wing_sizes_falls_back_to_full():
    m = _marks(wings_locked=True, wing_lots=None)
    assert next(x for x in m if x["leg_index"] == 2)["quantity"] == 975


# ── Entry timestamps ─────────────────────────────────────────────────────────
# Live marks replace the persisted legs wholesale in the client, so a mark
# without a timestamp blanks the Entered column for the whole session.
def test_every_mark_carries_the_time_its_leg_was_opened():
    h = [{"id": "h0", "side": "BUY", "option_type": "PE", "strike": 23600,
          "entry_price": 16.05, "last_price": 20.0, "lots": 5,
          "entry_ts": datetime(2026, 9, 8, 10, 20, 11)}]
    m = _marks(wings_locked=True, wing_lots=[8, 13], hedges=h)

    by_idx = {x["leg_index"]: x for x in m}
    assert by_idx[0]["entry_timestamp"] == ENTRY_TS.isoformat()   # straddle
    assert by_idx[2]["entry_timestamp"] == LOCK_TS.isoformat()    # lock wing
    assert by_idx[4]["entry_timestamp"] == "2026-09-08T10:20:11"  # hedge
    assert all(x["entry_timestamp"] is not None for x in m)


def test_missing_timestamp_is_null_rather_than_raising():
    """Sessions started before entry times were recorded must still render."""
    h = [{"id": "h0", "side": "BUY", "option_type": "PE", "strike": 23600,
          "entry_price": 16.05, "last_price": 20.0, "lots": 5}]
    m = _marks(entry_ts=None, hedges=h)
    assert m[0]["entry_timestamp"] is None
    assert m[-1]["entry_timestamp"] is None


# ── Implied volatility for the intraday payoff curve ─────────────────────────
# The UI reprices the whole book at other spots to draw what the position is
# worth *today* rather than at expiry. That is only possible if every leg
# carries the volatility backed out of its own live price, so a mark without an
# IV silently costs the chart a leg.

EXPIRY = date(2026, 9, 15)
NOW = datetime(2026, 9, 10, 11, 29, 0)
SPOT = 23429.05


def _iv_marks(**kw):
    return _marks(spot=SPOT, vix=11.76, expiry_date=EXPIRY, now=NOW, **kw)


def test_every_leg_carries_an_iv_backed_out_of_its_own_price():
    m = _iv_marks(wings_locked=True, hedges=[
        {"id": "h0", "side": "BUY", "option_type": "PE", "strike": 23600,
         "entry_price": 16.05, "last_price": 20.0, "lots": 5}])
    assert len(m) == 5
    assert all(x["iv"] is not None for x in m), [x["leg_index"] for x in m if x["iv"] is None]
    # Each leg is priced off its own strike and price, so no two of these
    # collapse to a single shared number.
    assert len({x["iv"] for x in m}) > 1
    # Sane option volatilities, not a runaway solve.
    assert all(0.01 <= x["iv"] <= 3.0 for x in m)


def test_iv_inverts_each_legs_own_strike_and_type():
    """Every leg must be solved against its own contract.

    Prices are generated from known volatilities and handed back in, so the
    round trip pins strike and CE/PE per leg: solving a wing at the straddle's
    strike, or a put as a call, lands on a different number for every one of
    these five legs.
    """
    years = years_to_expiry(NOW, EXPIRY)
    price = lambda k, t, iv: black_scholes_price(SPOT, k, years, iv, t)
    m = _iv_marks(
        wings_locked=True,
        straddle_cur=(price(23700, "CE", 0.110), price(23700, "PE", 0.090)),
        wing_cur=(price(23800, "CE", 0.130), price(23600, "PE", 0.140)),
        hedges=[{"id": "h0", "side": "BUY", "option_type": "PE", "strike": 23600,
                 "entry_price": 16.05, "last_price": price(23600, "PE", 0.160),
                 "lots": 5}],
    )
    got = {x["leg_index"]: x["iv"] for x in m}
    assert got == {
        0: pytest.approx(0.110, abs=1e-3), 1: pytest.approx(0.090, abs=1e-3),
        2: pytest.approx(0.130, abs=1e-3), 3: pytest.approx(0.140, abs=1e-3),
        4: pytest.approx(0.160, abs=1e-3),
    }


def test_iv_is_omitted_when_there_is_nothing_to_measure():
    """No spot, no expiry or no price means no honest volatility, so the field
    stays null and the UI drops back to the expiry-only chart rather than
    guessing one."""
    assert all(x["iv"] is None for x in _marks())                       # neither
    assert all(x["iv"] is None for x in _marks(spot=SPOT))              # no expiry
    assert all(x["iv"] is None for x in _marks(spot=SPOT, now=NOW))     # no expiry, clock set
    assert all(x["iv"] is None for x in _marks(expiry_date=EXPIRY, now=NOW))  # no spot
    assert all(x["iv"] is None for x in _iv_marks(straddle_cur=(None, None)))


def test_iv_falls_back_to_vix_when_the_price_cannot_be_inverted():
    """A price below intrinsic has no implied volatility. Rather than drop the
    leg, fall back to VIX -- the same ladder the delta hedge already uses."""
    deep = _marks(spot=25000.0, vix=11.76, expiry_date=EXPIRY, now=NOW,
                  straddle_cur=(0.05, 0.05))
    assert deep[0]["iv"] == pytest.approx(0.1176, abs=1e-4)

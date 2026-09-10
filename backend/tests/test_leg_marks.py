"""Per-leg marks broadcast on the live MTM tick.

These rows are what the Positions & MTM panel renders, so they must agree with
the header's gross MTM exactly -- a panel whose rows do not sum to the number
above them is worse than no panel.
"""
from datetime import datetime

import pytest

from app.services.live_paper_engine import _leg_marks

LOT = 75
APPROVED = 13


ENTRY_TS = datetime(2026, 9, 8, 9, 50, 3)
LOCK_TS = datetime(2026, 9, 8, 11, 56, 15)


def _marks(*, wings_locked=False, wing_lots=None, hedges=None,
           straddle_cur=(40.0, 60.0), wing_cur=(12.0, 9.0),
           entry_ts=ENTRY_TS, lock_ts=LOCK_TS):
    return _leg_marks(
        ["s0", "s1"], [48.35, 46.25], list(straddle_cur), 23700, entry_ts,
        ["w0", "w1"], [16.0, 14.0], list(wing_cur), [23800, 23600], lock_ts,
        wing_lots, wings_locked, hedges or [], LOT, APPROVED,
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

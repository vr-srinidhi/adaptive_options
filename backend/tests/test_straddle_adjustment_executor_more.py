from datetime import date, datetime
from types import SimpleNamespace
import uuid

import pytest

import app.services.straddle_adjustment_executor as executor
from app.services import charges_service
from app.models.strategy_run import (
    StrategyLegMtm, StrategyRun, StrategyRunEvent, StrategyRunLeg, StrategyRunMtm,
)
from app.services.generic_executor import ValidationResult


class _FakeDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _validation(**overrides):
    values = {
        "validated": True,
        "instrument": "NIFTY",
        "trade_date": "2026-05-06",
        "entry_time": "09:50",
        "resolved_expiry": "2026-05-12",
        "spot_at_entry": 22500.0,
        "atm_strike": 22500,
        "contracts": [],
        "lot_size": 75,
        "approved_lots": 1,
        "estimated_margin": 250000,
        "warnings": [],
    }
    values.update(overrides)
    return ValidationResult(**values)


def _strategy():
    return {
        "id": "short_straddle_dual_lock",
        "version": "v1",
        "executor": "straddle_adjustment_v1",
        "entry_rule_id": "timed_entry",
        "exit_rule": {
            "time_exit": "15:25",
            "stop_capital_pct": 0.2,
            "trail_trigger": 0,
            "trail_pct": 0,
            "lock_trigger": 1000,
            "loss_lock_trigger": 1000,
            "wing_width_steps": 2,
        },
    }


def _config(**overrides):
    values = {
        "run_type": "single_session_backtest",
        "instrument": "NIFTY",
        "trade_date": "2026-05-06",
        "entry_time": "09:50",
        "capital": 2500000,
    }
    values.update(overrides)
    return values


def _spot(*times):
    return [{"date": datetime(2026, 5, 6, hour, minute), "close": close} for hour, minute, close in times]


def _index(rows):
    base = datetime(2026, 5, 6, 9, 15)
    out = {}
    for strike, opt_type, hour, minute, price in rows:
        idx = int((datetime(2026, 5, 6, hour, minute) - base).total_seconds() / 60)
        out.setdefault((strike, opt_type), {})[idx] = {"price": price}
    return out


async def _patch_common_loaders(monkeypatch, spot_rows, option_index):
    async def contract_spec(_db, _instrument, _trade_date):
        return SimpleNamespace(strike_step=50)

    async def option_loader(_db, _instrument, _trade_date, _expiry, strike_keys, option_price_source="close"):
        assert option_price_source == "close"
        assert (22500, "CE") in strike_keys
        return option_index, []

    async def spot_loader(_db, _instrument, _trade_date):
        return spot_rows

    async def vix_loader(_db, _trade_date):
        return [{"date": row["date"], "vix_close": 12.5} for row in spot_rows]

    monkeypatch.setattr(executor, "get_contract_spec", contract_spec)
    monkeypatch.setattr(executor, "load_option_candles_for_strikes", option_loader)
    monkeypatch.setattr(executor, "load_spot_candles", spot_loader)
    monkeypatch.setattr(executor, "load_vix_candles", vix_loader)
    monkeypatch.setattr(executor, "vix_at_time", lambda rows, ts: 12.5)


@pytest.mark.asyncio
async def test_execute_run_enters_profit_locks_and_time_exits(monkeypatch):
    db = _FakeDb()
    await _patch_common_loaders(
        monkeypatch,
        _spot((9, 50, 22500), (9, 51, 22510), (15, 25, 22525)),
        _index([
            (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
            (22600, "CE", 9, 50, 10), (22400, "PE", 9, 50, 10),
            (22500, "CE", 9, 51, 50), (22500, "PE", 9, 51, 50),
            (22600, "CE", 9, 51, 12), (22400, "PE", 9, 51, 12),
            (22500, "CE", 15, 25, 40), (22500, "PE", 15, 25, 45),
            (22600, "CE", 15, 25, 15), (22400, "PE", 15, 25, 14),
        ]),
    )

    result = await executor.execute_run(db, uuid.uuid4(), _strategy(), _config(), _validation())

    assert result.status == "completed"
    assert result.exit_reason == "TIME_EXIT"
    assert db.commits == 1
    run = next(obj for obj in db.added if isinstance(obj, StrategyRun))
    assert run.result_json["wings_locked"] is True
    assert run.result_json["lock_reason"] == "profit"
    assert run.entry_credit_per_unit == 200
    events = [obj for obj in db.added if isinstance(obj, StrategyRunEvent)]
    assert [event.reason_code for event in events] == ["ENTRY_SCHEDULED", "WINGS_LOCKED", "TIME_EXIT"]
    assert len([obj for obj in db.added if isinstance(obj, StrategyRunLeg)]) == 4
    assert len([obj for obj in db.added if isinstance(obj, StrategyRunMtm)]) == 2


@pytest.mark.asyncio
async def test_execute_run_loss_locks_before_time_exit(monkeypatch):
    db = _FakeDb()
    await _patch_common_loaders(
        monkeypatch,
        _spot((9, 50, 22500), (9, 51, 22490), (15, 25, 22480)),
        _index([
            (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
            (22600, "CE", 9, 50, 10), (22400, "PE", 9, 50, 10),
            (22500, "CE", 9, 51, 130), (22500, "PE", 9, 51, 130),
            (22600, "CE", 9, 51, 11), (22400, "PE", 9, 51, 11),
            (22500, "CE", 15, 25, 125), (22500, "PE", 15, 25, 120),
            (22600, "CE", 15, 25, 13), (22400, "PE", 15, 25, 12),
        ]),
    )

    result = await executor.execute_run(
        db,
        uuid.uuid4(),
        _strategy(),
        _config(stop_capital_pct=0.2),
        _validation(),
    )

    assert result.status == "completed"
    run = next(obj for obj in db.added if isinstance(obj, StrategyRun))
    assert run.result_json["wings_locked"] is True
    assert run.result_json["lock_reason"] == "loss"
    locked = [obj for obj in db.added if isinstance(obj, StrategyRunEvent) and obj.reason_code == "WINGS_LOCKED"]
    assert locked[0].payload_json["lock_reason"] == "loss"


@pytest.mark.asyncio
async def test_execute_run_exits_on_data_gap_after_entry(monkeypatch):
    db = _FakeDb()
    await _patch_common_loaders(
        monkeypatch,
        _spot((9, 50, 22500), (9, 51, 22510), (9, 52, 22515)),
        _index([
            (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
            (22600, "CE", 9, 50, 10), (22400, "PE", 9, 50, 10),
        ]),
    )

    result = await executor.execute_run(db, uuid.uuid4(), _strategy(), _config(), _validation())

    assert result.status == "completed"
    assert result.exit_reason == "DATA_GAP_EXIT"
    events = [obj.reason_code for obj in db.added if isinstance(obj, StrategyRunEvent)]
    assert "DATA_GAP_EXIT" in events


@pytest.mark.asyncio
async def test_execute_run_no_spot_data_returns_no_trade(monkeypatch):
    await _patch_common_loaders(monkeypatch, [], {})

    result = await executor.execute_run(_FakeDb(), uuid.uuid4(), _strategy(), _config(), _validation())

    assert result.status == "no_trade"
    assert result.exit_reason == "NO_SPOT_DATA"
    assert result.realized_net_pnl is None


# ── Delta hedge sizing ────────────────────────────────────────────────────────

def _hedge_config(**overrides):
    """Dual-lock config with the delta hedge armed and locks pushed out of reach,
    so a test isolates hedge behaviour."""
    values = _config(
        delta_hedge_enabled=True,
        delta_threshold=150,
        lock_trigger=10_000_000,
        loss_lock_trigger=10_000_000,
        stop_capital_pct=0.9,
    )
    values.update(overrides)
    return values


def _hedge_market():
    """Spot rallies hard after entry so the short straddle builds negative delta
    and the CE side gets tested."""
    spot = _spot((9, 50, 22500), (9, 55, 22800), (15, 25, 22800))
    index = _index([
        (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
        (22600, "CE", 9, 50, 40),  (22400, "PE", 9, 50, 40),
        (22500, "CE", 9, 55, 320), (22500, "PE", 9, 55, 15),
        (22600, "CE", 9, 55, 240), (22400, "PE", 9, 55, 8),
        (22500, "CE", 15, 25, 300), (22500, "PE", 15, 25, 10),
        (22600, "CE", 15, 25, 210), (22400, "PE", 15, 25, 5),
    ])
    return spot, index


@pytest.mark.asyncio
async def test_delta_hedge_sizes_to_the_imbalance_not_full_position(monkeypatch):
    db = _FakeDb()
    spot, index = _hedge_market()
    await _patch_common_loaders(monkeypatch, spot, index)

    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(), _hedge_config(),
        _validation(approved_lots=13),
    )

    assert result.status == "completed"
    hedge_legs = [
        leg for leg in db.added
        if isinstance(leg, StrategyRunLeg) and leg.side == "BUY" and leg.entry_timestamp is not None
    ]
    assert hedge_legs, "expected the delta hedge to fire on a 300-point rally"
    hedge = hedge_legs[0]
    # The whole point: strictly smaller than the 13-lot straddle it protects.
    assert 0 < hedge.quantity < 13 * 75
    assert hedge.quantity % 75 == 0
    assert hedge.leg_index >= 4


@pytest.mark.asyncio
async def test_full_mode_still_hedges_with_the_whole_position(monkeypatch):
    db = _FakeDb()
    spot, index = _hedge_market()
    await _patch_common_loaders(monkeypatch, spot, index)

    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(), _hedge_config(hedge_qty_mode="FULL"),
        _validation(approved_lots=13),
    )

    assert result.status == "completed"
    hedge_legs = [
        leg for leg in db.added
        if isinstance(leg, StrategyRunLeg) and leg.side == "BUY" and leg.entry_timestamp is not None
    ]
    assert hedge_legs
    assert hedge_legs[0].quantity == 13 * 75


@pytest.mark.asyncio
async def test_delta_hedge_is_ignored_for_other_strategies_on_this_executor(monkeypatch):
    """short_straddle_profit_lock shares this executor but never offers the hedge."""
    db = _FakeDb()
    spot, index = _hedge_market()
    await _patch_common_loaders(monkeypatch, spot, index)

    strategy = {**_strategy(), "id": "short_straddle_profit_lock"}
    result = await executor.execute_run(
        db, uuid.uuid4(), strategy, _hedge_config(), _validation(approved_lots=13),
    )

    assert result.status == "completed"
    assert not [
        leg for leg in db.added
        if isinstance(leg, StrategyRunLeg) and leg.side == "BUY" and leg.entry_timestamp is not None
    ]
    assert not [
        e for e in db.added
        if isinstance(e, StrategyRunEvent) and e.event_type == "DELTA_HEDGE"
    ]
    # ...and the caller is told its config was ignored, rather than the run
    # quietly looking like a normal unhedged result.
    assert any("only supported on short_straddle_dual_lock" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_stop_exit_wins_over_delta_hedge_on_the_same_minute(monkeypatch):
    """Regression: the backtest used to buy a hedge wing on the minute it stopped
    out, because STOP_EXIT was evaluated after the hedge block."""
    db = _FakeDb()
    spot, index = _hedge_market()
    await _patch_common_loaders(monkeypatch, spot, index)

    # 0.1% of 25L = Rs2,500 — the 22500 CE running 100 -> 320 blows through it.
    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(),
        _hedge_config(stop_capital_pct=0.001),
        _validation(approved_lots=13),
    )

    assert result.exit_reason == "STOP_EXIT"
    assert not [
        e for e in db.added
        if isinstance(e, StrategyRunEvent) and e.reason_code == "DELTA_HEDGE_EXECUTED"
    ]


@pytest.mark.asyncio
async def test_low_delta_wing_skip_does_not_consume_a_trigger_or_disarm(monkeypatch):
    """The three properties the spec claims for a skip: no trigger consumed, no
    disarm, and the note logged once per episode rather than once per candle.

    Needs a genuinely far-OTM wing: at the default 2-step width the tested wing
    is at/in the money (delta ~0.73) and always hedgeable, so a wide wing is the
    only way to reach the zero-lot branch at all.
    """
    db = _FakeDb()
    # Spot rallies and stays there, so delta stays breached for many candles.
    spot = _spot((9, 50, 22500), (9, 55, 22800), (10, 0, 22810), (10, 5, 22820),
                 (10, 10, 22830), (15, 25, 22840))
    ce_w, pe_w = 23500, 21500          # ATM +/- 20 steps
    rows = []
    for hh, mm, ce, pe in ((9, 50, 100, 100), (9, 55, 320, 15), (10, 0, 322, 14),
                           (10, 5, 324, 13), (10, 10, 326, 12), (15, 25, 330, 10)):
        rows += [(22500, "CE", hh, mm, ce), (22500, "PE", hh, mm, pe),
                 (ce_w, "CE", hh, mm, 0.05), (pe_w, "PE", hh, mm, 0.05)]
    await _patch_common_loaders(monkeypatch, spot, _index(rows))

    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(), _hedge_config(wing_width_steps=20),
        _validation(approved_lots=13),
    )

    assert result.status == "completed"
    events = [e for e in db.added if isinstance(e, StrategyRunEvent)]
    codes = [e.reason_code for e in events]

    # No hedge leg was ever bought.
    assert not [l for l in db.added
                if isinstance(l, StrategyRunLeg) and l.leg_index >= 4]
    # A skip must not masquerade as a fired trigger.
    assert "DELTA_HEDGE_EXECUTED" not in codes
    assert "DELTA_HEDGE_TRIGGERED" not in codes
    # Logged once, not once per candle, even though the breach persisted.
    assert codes.count("DELTA_HEDGE_SKIPPED_LOW_DELTA") == 1
    # And labelled by cause: near-zero delta, NOT "gap too small".
    assert "DELTA_HEDGE_SKIPPED_UNDERSIZED" not in codes
    assert "near-zero delta" in next(
        e for e in events if e.reason_code == "DELTA_HEDGE_SKIPPED_LOW_DELTA").reason_text
    # Skipping never burned the trigger budget.
    assert "DELTA_HEDGE_EXHAUSTED" not in codes


@pytest.mark.asyncio
async def test_missing_wing_price_is_logged_once_not_every_candle(monkeypatch):
    """The wing-unavailable branch consumes no trigger either, so without a
    throttle it writes a TRIGGERED+FAILED pair on every tick for the rest of the
    session (~720 rows/hour live at a 10s poll)."""
    db = _FakeDb()
    spot = _spot((9, 50, 22500), (9, 55, 22800), (10, 0, 22810), (10, 5, 22820), (15, 25, 22830))
    # Straddle strikes priced; wing strikes entirely absent from the index.
    index = _index([
        (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
        (22500, "CE", 9, 55, 320), (22500, "PE", 9, 55, 15),
        (22500, "CE", 10, 0, 322), (22500, "PE", 10, 0, 14),
        (22500, "CE", 10, 5, 324), (22500, "PE", 10, 5, 13),
        (22500, "CE", 15, 25, 330), (22500, "PE", 15, 25, 10),
    ])
    await _patch_common_loaders(monkeypatch, spot, index)

    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(), _hedge_config(), _validation(approved_lots=13),
    )

    assert result.status == "completed"
    codes = [e.reason_code for e in db.added if isinstance(e, StrategyRunEvent)]
    assert codes.count("DELTA_HEDGE_FAILED") == 1
    # TRIGGERED must not be emitted for a hedge that never happened.
    assert "DELTA_HEDGE_TRIGGERED" not in codes


@pytest.mark.asyncio
async def test_skip_note_is_logged_once_per_episode_not_once_per_session(monkeypatch):
    """Two separate breach episodes must produce two notes.

    This is the test that pins "per episode". The reset has to live outside the
    `not delta_reentry_armed` guard, because a skip deliberately leaves the hedge
    armed -- so if the reset sits inside that guard it never runs and the note
    silently degrades to once per session.
    """
    db = _FakeDb()
    # breach -> back inside the safe band -> breach again
    spot = _spot((9, 50, 22500), (9, 55, 22800), (10, 0, 22500),
                 (10, 5, 22800), (15, 25, 22820))
    ce_w, pe_w = 23500, 21500          # ATM +/- 20 steps, far OTM on purpose
    rows = []
    for hh, mm, ce, pe in ((9, 50, 100, 100), (9, 55, 320, 15), (10, 0, 100, 100),
                           (10, 5, 320, 15), (15, 25, 330, 10)):
        rows += [(22500, "CE", hh, mm, ce), (22500, "PE", hh, mm, pe),
                 (ce_w, "CE", hh, mm, 0.05), (pe_w, "PE", hh, mm, 0.05)]
    await _patch_common_loaders(monkeypatch, spot, _index(rows))

    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(), _hedge_config(wing_width_steps=20),
        _validation(approved_lots=13),
    )

    assert result.status == "completed"
    codes = [e.reason_code for e in db.added if isinstance(e, StrategyRunEvent)]
    assert codes.count("DELTA_HEDGE_SKIPPED_LOW_DELTA") == 2, (
        "expected one note per breach episode, got "
        f"{codes.count('DELTA_HEDGE_SKIPPED_LOW_DELTA')} -- the re-arm reset is "
        "probably trapped inside the `not delta_reentry_armed` guard"
    )


@pytest.mark.asyncio
async def test_persisted_hedge_size_matches_the_sizing_helper(monkeypatch):
    """Behavioural parity: the quantity the executor actually writes must equal
    what `hedge_lots_for_delta` returns for the same inputs, recomputed here
    independently from the events the engine itself recorded.

    Asserting the two modules imported the same symbol is near-tautological; the
    divergence that actually bit us last round was a caller passing different
    arguments, which only a check at this level catches.
    """
    from app.services.delta_hedge import hedge_lots_for_delta

    db = _FakeDb()
    spot, index = _hedge_market()
    await _patch_common_loaders(monkeypatch, spot, index)

    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(), _hedge_config(), _validation(approved_lots=13),
    )
    assert result.status == "completed"

    events = [e for e in db.added if isinstance(e, StrategyRunEvent)]
    triggered = next(e for e in events if e.reason_code == "DELTA_HEDGE_TRIGGERED")
    executed  = next(e for e in events if e.reason_code == "DELTA_HEDGE_EXECUTED")
    hedge_leg = next(l for l in db.added
                     if isinstance(l, StrategyRunLeg) and l.leg_index >= 4)

    # Pre-hedge delta comes from TRIGGERED; EXECUTED carries the post-hedge one.
    expected_lots = hedge_lots_for_delta(
        net_delta=triggered.payload_json["net_delta"],
        option_delta=executed.payload_json["unit_delta"],
        lot_size=75,
        max_lots=13,
        mode="PARTIAL",
    )

    assert expected_lots > 0, "fixture should breach hard enough to need a hedge"
    assert executed.payload_json["lots"] == expected_lots
    assert hedge_leg.quantity == 75 * expected_lots
    # And the sizing genuinely did its job: strictly smaller than the straddle.
    assert hedge_leg.quantity < 13 * 75


# ── Wing netting: downstream invariants ──────────────────────────────────────
# These drive a full hedge -> lock -> MTM -> finalization pass, because the
# netting helper being correct in isolation says nothing about whether the
# quantity survives into delta, charges, per-leg P&L and the MTM stream.

def _netting_config(**overrides):
    """Hedge armed and the loss lock reachable, so the lock fires *after* a
    hedge has already bought the tested wing -- the production pattern."""
    values = _config(
        delta_hedge_enabled=True,
        delta_threshold=150,
        lock_trigger=10_000_000,     # profit lock out of reach
        loss_lock_trigger=50_000,    # loss lock fires once the rally bites
        stop_capital_pct=0.9,
    )
    values.update(overrides)
    return values


async def _run_netting(monkeypatch, mode):
    db = _FakeDb()
    spot, index = _hedge_market()
    await _patch_common_loaders(monkeypatch, spot, index)
    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(), _netting_config(hedge_qty_mode=mode),
        _validation(approved_lots=13),
    )
    legs = [o for o in db.added if isinstance(o, StrategyRunLeg)]
    return db, result, {l.leg_index: l for l in legs}


@pytest.mark.asyncio
async def test_partially_netted_wing_conserves_total_protection(monkeypatch):
    db, result, legs = await _run_netting(monkeypatch, "PARTIAL")
    lot = 75

    hedge = next(l for i, l in legs.items() if i >= 4 and l.option_type == "CE")
    hedge_lots = hedge.quantity // lot
    assert 0 < hedge_lots < 13, "PARTIAL should size below the full position"

    # The lock buys only the balance on the hedged side...
    wing_ce = legs[2]
    assert wing_ce.quantity == lot * (13 - hedge_lots)
    # ...and the untouched side is still bought in full.
    assert legs[3].quantity == lot * 13

    # Total long protection on the tested side is exactly approved_lots.
    assert hedge.quantity + wing_ce.quantity == lot * 13


@pytest.mark.asyncio
async def test_fully_netted_wing_still_reports_net_delta(monkeypatch):
    """A zero-lot wing must not silence delta monitoring.

    signed_position_delta() returns None for quantity <= 0 and the caller bails
    on the first None, so passing a fully netted wing through at zero made every
    post-lock net_delta None -- which stops all later hedging and blanks the
    displayed value. The wing has to be skipped, not passed at zero.
    """
    db, result, legs = await _run_netting(monkeypatch, "FULL")
    assert 2 not in legs, "fixture must actually produce a fully netted wing"

    lock_evt = next(e for e in db.added
                    if isinstance(e, StrategyRunEvent) and e.reason_code == "WINGS_LOCKED")
    post_lock = [o for o in db.added
                 if isinstance(o, StrategyRunMtm) and o.timestamp >= lock_evt.timestamp]
    assert post_lock, "expected MTM rows after the lock"
    assert all(r.net_delta is not None for r in post_lock), (
        "net delta went None after a fully netted lock wing -- "
        "delta monitoring and every later hedge are disabled"
    )
    run = next(o for o in db.added if isinstance(o, StrategyRun))
    assert run.result_json.get("last_net_delta") is not None


@pytest.mark.asyncio
async def test_fully_netted_wing_writes_no_leg_and_no_leg_mtm(monkeypatch):
    db, result, legs = await _run_netting(monkeypatch, "FULL")
    lot = 75

    hedge = next(l for i, l in legs.items() if i >= 4 and l.option_type == "CE")
    assert hedge.quantity == lot * 13, "FULL buys the whole position size"

    # Wing 2 is entirely covered by the hedge, so nothing was bought for it.
    assert 2 not in legs, "a fully netted wing must not persist a leg"
    assert legs[3].quantity == lot * 13, "the other wing is unaffected"

    # And it must not leave per-leg MTM rows behind pointing at no leg:
    # StrategyLegMtm.leg_id has no enforced FK, so orphans commit silently.
    persisted_leg_ids = {l.id for l in legs.values()}
    leg_mtm = [o for o in db.added if isinstance(o, StrategyLegMtm)]
    assert leg_mtm, "expected some per-leg MTM rows"
    orphans = [m for m in leg_mtm if m.leg_id not in persisted_leg_ids]
    assert not orphans, f"{len(orphans)} MTM rows reference no persisted leg"


@pytest.mark.asyncio
async def test_wing_entry_charges_are_not_counted_twice(monkeypatch):
    """total_charges must equal the sum of each leg's own round trip.

    compute_leg_total_charges covers entry *and* exit, so adding
    wing_entry_charges alongside it double-counted every locked wing's entry.
    """
    db, result, legs = await _run_netting(monkeypatch, "PARTIAL")
    run = next(o for o in db.added if isinstance(o, StrategyRun))

    expected = 0.0
    for leg in legs.values():
        if leg.entry_price is None or leg.exit_price is None:
            continue
        expected += charges_service.compute_leg_total_charges(
            leg.quantity // 75, 75,
            [(leg.side, leg.option_type, leg.strike)],
            [float(leg.entry_price)], [float(leg.exit_price)],
        )
    assert float(run.total_charges) == pytest.approx(expected, abs=1.0)


@pytest.mark.asyncio
async def test_netted_wing_is_not_double_counted_in_net_delta(monkeypatch):
    """Post-lock delta must reflect hedge + netted wing, not hedge + full wing.

    Modelling 13 wing lots on top of the 10 they were netted against implies
    protection that was never bought and drives later hedge decisions off it.
    Asserting leg quantities alone would not catch this: the delta calculation
    is a separate code path that reads the same sizes, so this pins it by
    forcing the pre-fix sizing back in and requiring the delta to change.
    """
    db, _result, legs = await _run_netting(monkeypatch, "PARTIAL")
    lot = 75
    hedge_lots = next(l for i, l in legs.items() if i >= 4 and l.option_type == "CE").quantity // lot
    assert hedge_lots + legs[2].quantity // lot == 13

    def _post_lock_deltas(rows):
        return [float(r.net_delta) for r in rows
                if isinstance(r, StrategyRunMtm) and r.net_delta is not None]

    netted = _post_lock_deltas(db.added)
    assert netted, "expected delta to be recorded"

    # Re-run with the netting helper forced back to full-size wings.
    db2 = _FakeDb()
    spot, index = _hedge_market()
    await _patch_common_loaders(monkeypatch, spot, index)
    monkeypatch.setattr(executor, "wing_lots_after_hedges",
                        lambda **kw: kw["approved_lots"])
    await executor.execute_run(
        db2, uuid.uuid4(), _strategy(), _netting_config(hedge_qty_mode="PARTIAL"),
        _validation(approved_lots=13),
    )
    full = _post_lock_deltas(db2.added)

    assert full and len(full) == len(netted)
    assert netted != full, (
        "net delta is identical with full-size wings, so the delta calculation "
        "is not reading the netted quantity"
    )

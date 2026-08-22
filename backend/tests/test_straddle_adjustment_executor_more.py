from datetime import date, datetime
from types import SimpleNamespace
import uuid

import pytest

import app.services.straddle_adjustment_executor as executor
from app.models.strategy_run import StrategyRun, StrategyRunEvent, StrategyRunLeg, StrategyRunMtm
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

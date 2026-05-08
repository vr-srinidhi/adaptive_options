from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
import sys
import types
import uuid

import pytest
from fastapi import HTTPException

_jose = types.ModuleType("jose")
_jose.JWTError = Exception
_jose.jwt = types.SimpleNamespace(
    encode=lambda *args, **kwargs: "token",
    decode=lambda *args, **kwargs: {},
)
sys.modules.setdefault("jose", _jose)

_passlib = types.ModuleType("passlib")
_passlib_context = types.ModuleType("passlib.context")


class _CryptContext:
    def __init__(self, *args, **kwargs):
        pass

    def hash(self, value):
        return value

    def verify(self, plain, hashed):
        return plain == hashed


_passlib_context.CryptContext = _CryptContext
sys.modules.setdefault("passlib", _passlib)
sys.modules.setdefault("passlib.context", _passlib_context)

import app.routers.live_paper as live_paper
import app.routers.paper_trading as paper_trading
from app.models.live_paper import LivePaperConfig, LivePaperSession
from app.models.paper_trade import (
    MinuteDecision,
    PaperCandleSeries,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.models.strategy_run import StrategyRun, StrategyRunEvent, StrategyRunLeg, StrategyRunMtm


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value


class _ExecuteResult:
    def __init__(self, scalar=None, scalars=None, rows=None):
        self._scalar = scalar
        self._scalars = scalars or []
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _ScalarResult(self._scalars)

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeDb:
    def __init__(self, execute_results=None):
        self._execute_results = list(execute_results or [])
        self.commits = 0
        self.refreshes = []
        self.deleted = []
        self.executed_writes = 0

    async def execute(self, *_args, **_kwargs):
        if self._execute_results:
            return self._execute_results.pop(0)
        self.executed_writes += 1
        return _ExecuteResult()

    def add(self, obj):
        self.added = obj

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshes.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)


def test_paper_trading_serializers_convert_decimals_and_optional_fields():
    session_id = uuid.uuid4()
    trade_id = uuid.uuid4()
    ts = datetime(2026, 5, 6, 9, 30)

    session = PaperSession(
        id=session_id,
        instrument="NIFTY",
        session_date=date(2026, 5, 6),
        capital=Decimal("2500000.00"),
        status="COMPLETED",
        decision_count=2,
        final_session_state="CLOSED",
        summary_pnl=Decimal("4500.25"),
    )
    decision = MinuteDecision(
        id=uuid.uuid4(),
        session_id=session_id,
        timestamp=ts,
        spot_close=Decimal("22500.50"),
        action="ENTER",
        reason_code="ENTRY",
        computed_max_loss=Decimal("10000.00"),
        selected_candidate_score=Decimal("99.1234"),
        candidate_ranking_json={"candidates": []},
    )
    trade = PaperTradeHeader(
        id=trade_id,
        session_id=session_id,
        entry_time=ts,
        exit_time=datetime(2026, 5, 6, 15, 20),
        bias="BULLISH",
        expiry=date(2026, 5, 12),
        lot_size=75,
        approved_lots=2,
        entry_debit=Decimal("12.50"),
        realized_net_pnl=Decimal("4500.25"),
        charges=Decimal("500.00"),
        long_strike=22400,
        short_strike=22500,
        option_type="CE",
        selected_candidate_score=Decimal("98.5"),
    )
    leg = PaperTradeLeg(
        trade_id=trade_id,
        leg_side="LONG",
        option_type="CE",
        strike=22400,
        expiry=date(2026, 5, 12),
        entry_price=Decimal("10.0"),
        exit_price=Decimal("20.0"),
    )
    mark = PaperTradeMinuteMark(
        trade_id=trade_id,
        timestamp=ts,
        long_leg_price=Decimal("20.0"),
        total_mtm=Decimal("1500.0"),
        estimated_net_mtm=Decimal("1400.0"),
        price_freshness_json={"long_age_min": 0},
    )
    candles = PaperCandleSeries(session_id=session_id, series_type="SPOT", candles=[{"close": 22500}])

    assert paper_trading._session_dict(session)["summary_pnl"] == 4500.25
    assert paper_trading._decision_dict(decision)["selected_candidate_score"] == 99.1234
    serialized_trade = paper_trading._trade_dict(trade, [leg])
    assert serialized_trade["expiry"] == "2026-05-12"
    assert serialized_trade["legs"][0]["entry_price"] == 10.0
    assert paper_trading._mark_dict(mark)["estimated_net_mtm"] == 1400.0
    assert paper_trading._candle_series_dict(candles) == {"series_type": "SPOT", "candles": [{"close": 22500}]}


def test_live_paper_serializers_include_delta_and_active_state(monkeypatch):
    cfg_id = uuid.uuid4()
    run_id = uuid.uuid4()
    monkeypatch.setattr(live_paper, "is_session_active", lambda sid: True)

    cfg = LivePaperConfig(
        id=cfg_id,
        label=None,
        strategy_id="short_straddle_dual_lock",
        instrument="NIFTY",
        capital=Decimal("2500000.00"),
        entry_time="10:15",
        params_json={"delta_hedge_enabled": True},
        enabled=True,
        execution_mode="paper",
    )
    session = LivePaperSession(
        id=uuid.uuid4(),
        config_id=cfg_id,
        status="entered",
        trade_date=date(2026, 5, 6),
        atm_strike=22500,
        expiry_date=date(2026, 5, 12),
        ce_symbol="NIFTYCE",
        pe_symbol="NIFTYPE",
        strategy_run_id=run_id,
        net_mtm_latest=Decimal("1234.50"),
        spot_latest=Decimal("22501.25"),
        net_delta_latest=Decimal("-75.2500"),
        delta_hedge_status="hedged",
        delta_hedge_count=2,
        waiting_spot_json=[{"spot": 22500}],
    )

    assert live_paper._serialize_config(cfg)["label"] == "10:15"
    serialized = live_paper._serialize_session(session)
    assert serialized["strategy_run_id"] == str(run_id)
    assert serialized["net_mtm_latest"] == 1234.5
    assert serialized["net_delta_latest"] == -75.25
    assert serialized["delta_hedge_status"] == "hedged"
    assert serialized["is_active"] is True


@pytest.mark.asyncio
async def test_live_paper_mtm_and_event_helpers_join_leg_prices():
    run_id = uuid.uuid4()
    leg_id = uuid.uuid4()
    ts = datetime(2026, 5, 6, 9, 31)
    mtm = StrategyRunMtm(
        run_id=run_id,
        timestamp=ts,
        spot_close=Decimal("22500.00"),
        gross_mtm=Decimal("1500.00"),
        net_mtm=Decimal("1200.00"),
        net_delta=Decimal("-25.5000"),
        trail_stop_level=Decimal("500.00"),
        event_code="TIME_EXIT",
    )
    event = StrategyRunEvent(
        run_id=run_id,
        timestamp=ts,
        event_type="ENTRY",
        reason_code="ENTRY_SCHEDULED",
        reason_text="entered",
        payload_json={"spot": 22500},
    )
    db = _FakeDb([
        _ExecuteResult(scalars=[mtm]),
        _ExecuteResult(rows=[("CE", ts, Decimal("100.00"))]),
        _ExecuteResult(scalars=[event]),
    ])

    mtm_series = await live_paper._get_mtm_series(db, run_id)
    events = await live_paper._get_events(db, run_id)

    assert mtm_series[0]["ce_price"] == 100.0
    assert mtm_series[0]["net_delta"] == -25.5
    assert events[0]["payload"] == {"spot": 22500}


@pytest.mark.asyncio
async def test_live_paper_build_slot_includes_run_info(monkeypatch):
    cfg_id = uuid.uuid4()
    run_id = uuid.uuid4()
    cfg = LivePaperConfig(
        id=cfg_id,
        label="slot",
        strategy_id="short_straddle_dual_lock",
        instrument="NIFTY",
        capital=Decimal("2500000.00"),
        entry_time="10:15",
        params_json={},
        enabled=True,
        execution_mode="paper",
    )
    session = LivePaperSession(id=uuid.uuid4(), config_id=cfg_id, trade_date=date(2026, 5, 6), status="entered", strategy_run_id=run_id)
    run = StrategyRun(
        id=run_id,
        strategy_id="short_straddle_dual_lock",
        run_type="single_session_backtest",
        executor="generic_v1",
        instrument="NIFTY",
        trade_date=date(2026, 5, 6),
        status="completed",
        capital=Decimal("2500000.00"),
        lot_size=75,
        approved_lots=2,
        entry_credit_total=Decimal("15000.00"),
        config_json={},
    )
    legs = [
        StrategyRunLeg(run_id=run_id, leg_index=0, side="SELL", option_type="CE", strike=22500, expiry_date=date(2026, 5, 12), quantity=150, entry_price=Decimal("100.0")),
        StrategyRunLeg(run_id=run_id, leg_index=1, side="SELL", option_type="PE", strike=22500, expiry_date=date(2026, 5, 12), quantity=150, entry_price=Decimal("90.0")),
    ]
    async def empty_mtm(*_args):
        return []

    async def empty_events(*_args):
        return []

    monkeypatch.setattr(live_paper, "_get_mtm_series", empty_mtm)
    monkeypatch.setattr(live_paper, "_get_events", empty_events)
    db = _FakeDb([
        _ExecuteResult(scalar=run),
        _ExecuteResult(scalars=legs),
    ])

    slot = await live_paper._build_slot(db, cfg, session)

    assert slot["config"]["label"] == "slot"
    assert slot["session"]["id"] == str(session.id)
    assert slot["run"]["entry_credit_total"] == 15000.0
    assert slot["run"]["ce_entry_price"] == 100.0
    assert slot["run"]["pe_entry_price"] == 90.0


@pytest.mark.asyncio
async def test_live_paper_manual_start_maps_engine_errors(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    db = _FakeDb()

    for engine_error, expected_status in [
        ("no_config", 404),
        ("no_token", 409),
        ("session_exists", 409),
    ]:
        async def start_live_session(*_args, _engine_error=engine_error, **_kwargs):
            return _engine_error

        monkeypatch.setattr(live_paper, "start_live_session", start_live_session)
        with pytest.raises(HTTPException) as exc:
            await live_paper.manual_start(live_paper.StartBody(), db, user)
        assert exc.value.status_code == expected_status

    async def start_ok(*_args, **_kwargs):
        return None

    monkeypatch.setattr(live_paper, "start_live_session", start_ok)
    assert await live_paper.manual_start(live_paper.StartBody(config_id=str(uuid.uuid4())), db, user) == {
        "detail": "Session(s) started.",
    }


@pytest.mark.asyncio
async def test_live_paper_trigger_data_sync_conflict_and_success(monkeypatch):
    db = _FakeDb()
    user = SimpleNamespace(id=uuid.uuid4())
    tasks = SimpleNamespace(calls=[], add_task=lambda fn, *args: tasks.calls.append((fn, args)))

    async def no_started_run(*_args, **_kwargs):
        return None

    monkeypatch.setattr(live_paper, "create_started_live_data_sync_run", no_started_run)
    with pytest.raises(HTTPException) as exc:
        await live_paper.trigger_data_sync_today(tasks, db, user)
    assert exc.value.status_code == 409

    run_id = uuid.uuid4()

    async def started_run(*_args, **_kwargs):
        return SimpleNamespace(id=run_id)

    async def status(*_args, **_kwargs):
        return {"status": "STARTED"}

    monkeypatch.setattr(live_paper, "create_started_live_data_sync_run", started_run)
    monkeypatch.setattr(live_paper, "get_live_data_sync_today", status)
    result = await live_paper.trigger_data_sync_today(tasks, db, user)

    assert result["detail"] == "Live data sync started."
    assert result["status"] == {"status": "STARTED"}
    assert tasks.calls == [(live_paper._run_manual_data_sync_background, (run_id,))]


@pytest.mark.asyncio
async def test_live_paper_config_crud_today_stop_and_streams(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    cfg = LivePaperConfig(
        id=uuid.uuid4(),
        user_id=user.id,
        label="Default",
        strategy_id="short_straddle_dual_lock",
        instrument="NIFTY",
        capital=Decimal("2500000.00"),
        entry_time="10:15",
        params_json={"lock_trigger": 20000},
        enabled=False,
        execution_mode="paper",
    )
    second = LivePaperConfig(
        id=uuid.uuid4(),
        user_id=user.id,
        label="Second",
        strategy_id="short_straddle_dual_lock",
        instrument="NIFTY",
        capital=Decimal("1000000.00"),
        entry_time="09:50",
        params_json={},
        enabled=True,
        execution_mode="paper",
    )
    session = LivePaperSession(id=uuid.uuid4(), config_id=cfg.id, user_id=user.id, trade_date=date.today(), status="entered")

    async def existing_config(_db, _user):
        return cfg

    async def sessions_for_date(_db, _trade_date, user_id=None):
        assert user_id == user.id
        return [session]

    async def token_status(_db, _user):
        return "valid"

    async def build_slot(_db, config, slot_session):
        return {"config": live_paper._serialize_config(config), "session": live_paper._serialize_session(slot_session)}

    monkeypatch.setattr(live_paper, "_get_or_create_config", existing_config)
    monkeypatch.setattr(live_paper, "get_sessions_for_date", sessions_for_date)
    monkeypatch.setattr(live_paper, "_token_status", token_status)
    monkeypatch.setattr(live_paper, "_build_slot", build_slot)

    db = _FakeDb([
        _ExecuteResult(scalars=[cfg, second]),
        _ExecuteResult(scalar=cfg),
        _ExecuteResult(scalar=cfg),
        _ExecuteResult(scalar=second),
        _ExecuteResult(scalars=[cfg, second]),
        _ExecuteResult(scalar=None),
        _ExecuteResult(scalars=[cfg]),
    ])

    assert len(await live_paper.list_configs(db, user)) == 2
    created = await live_paper.create_config(live_paper.ConfigCreate(label="New", entry_time="09:30", params={"trail_trigger": 1}), db, user)
    assert created["label"] == "New"

    updated = await live_paper.update_config_slot(str(cfg.id), live_paper.ConfigUpdate(label="Updated", params={"loss_lock_trigger": 25000}, enabled=True), db, user)
    assert updated["label"] == "Updated"
    assert updated["params"]["loss_lock_trigger"] == 25000

    deleted = await live_paper.delete_config_slot(str(second.id), db, user)
    assert deleted == {"detail": "Config deleted."}
    assert db.deleted == [second]

    today = await live_paper.get_today(db, user)
    assert today["token_status"] == "valid"
    assert today["slots"][0]["session"]["status"] == "entered"

    stopped = await live_paper.manual_stop(live_paper.StopBody(), _FakeDb(), user)
    assert stopped == {"detail": "Stop signal sent to 1 session(s)."}

    stream = await live_paper.stream_session(str(uuid.uuid4()), _FakeDb([_ExecuteResult(scalar=None)]), user)
    chunks = []
    async for chunk in stream.body_iterator:
        chunks.append(chunk)
    assert "NO_SESSION" in chunks[0]


@pytest.mark.asyncio
async def test_live_paper_config_error_and_token_user_paths(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    cfg = LivePaperConfig(
        id=uuid.uuid4(),
        user_id=user.id,
        label="Default",
        strategy_id="short_straddle_dual_lock",
        instrument="NIFTY",
        capital=Decimal("2500000.00"),
        entry_time="10:15",
        params_json={},
        enabled=False,
        execution_mode="paper",
    )

    with pytest.raises(HTTPException) as live_mode:
        await live_paper.update_config_slot(str(cfg.id), live_paper.ConfigUpdate(execution_mode="live"), _FakeDb([_ExecuteResult(scalar=cfg)]), user)
    assert live_mode.value.status_code == 422

    with pytest.raises(HTTPException) as missing:
        await live_paper.delete_config_slot(str(uuid.uuid4()), _FakeDb([_ExecuteResult(scalar=None)]), user)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as last_slot:
        await live_paper.delete_config_slot(str(cfg.id), _FakeDb([_ExecuteResult(scalar=cfg), _ExecuteResult(scalars=[cfg])]), user)
    assert last_slot.value.status_code == 409

    async def existing_config(_db, _user):
        return cfg

    monkeypatch.setattr(live_paper, "_get_or_create_config", existing_config)
    updated = await live_paper.update_config(live_paper.ConfigUpdate(instrument="BANKNIFTY", params={"lock_trigger": 1}), _FakeDb(), user)
    assert updated["instrument"] == "BANKNIFTY"
    assert updated["params"]["lock_trigger"] == 1

    monkeypatch.setattr(live_paper, "decode_access_token", lambda token: {"sub": str(user.id)})
    found = await live_paper._get_user_from_token_param("token", _FakeDb([_ExecuteResult(scalar=user)]))
    assert found.id == user.id

    with pytest.raises(HTTPException) as bad_user:
        await live_paper._get_user_from_token_param("token", _FakeDb([_ExecuteResult(scalar=None)]))
    assert bad_user.value.status_code == 401

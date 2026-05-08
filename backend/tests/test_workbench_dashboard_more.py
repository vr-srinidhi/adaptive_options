from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
import sys
import types
import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

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

import app.routers.workbench as workbench
from app.models.historical import SessionBatch
from app.models.paper_trade import PaperSession
from app.models.strategy_run import StrategyRun


class _ScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, result_sets):
        self.result_sets = [list(rows) for rows in result_sets]

    async def execute(self, *_args, **_kwargs):
        if not self.result_sets:
            raise AssertionError("unexpected query")
        return _ExecuteResult(self.result_sets.pop(0))


class _WriteDb(_FakeDb):
    def __init__(self, result_sets=None):
        super().__init__(result_sets or [])
        self.added = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self.refreshed = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


def _request():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v2/runs",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
    })


def _run(**overrides):
    values = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "strategy_id": "short_straddle_dual_lock",
        "instrument": "NIFTY",
        "trade_date": date(2026, 5, 6),
        "status": "completed",
        "realized_net_pnl": Decimal("1000.00"),
        "gross_pnl": Decimal("1400.00"),
        "total_charges": Decimal("400.00"),
        "capital": Decimal("2500000.00"),
        "approved_lots": 1,
        "lot_size": 75,
        "entry_credit_total": Decimal("15000.00"),
        "entry_time": "09:50",
        "exit_time": "15:25",
        "exit_reason": "TIME_EXIT",
        "result_json": {},
        "created_at": datetime(2026, 5, 6, 16, 0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _mtm(**overrides):
    values = {
        "timestamp": datetime(2026, 5, 6, 9, 51),
        "net_mtm": Decimal("900.00"),
        "trail_stop_level": None,
        "event_code": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_strategy_dashboard_groups_runs_and_computes_lock_stats():
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id)
    rows = [
        _run(
            user_id=user_id,
            trade_date=date(2026, 5, 6),
            realized_net_pnl=Decimal("5000.00"),
            result_json={
                "wings_locked": True,
                "lock_reason": "profit",
                "wing_lock_ts": "2026-05-06T10:15:00",
            },
        ),
        _run(
            user_id=user_id,
            trade_date=date(2026, 5, 7),
            realized_net_pnl=Decimal("-3000.00"),
            exit_reason="STOP_EXIT",
            result_json={
                "wings_locked": True,
                "lock_reason": "loss",
                "wing_lock_ts": "2026-05-07T11:00:00",
            },
        ),
        _run(
            user_id=user_id,
            trade_date=date(2026, 6, 1),
            realized_net_pnl=Decimal("2000.00"),
            result_json={"wings_locked": False},
        ),
    ]

    payload = await workbench.get_strategy_dashboard(_FakeDb([rows]), user)

    strategy = payload["strategies"][0]
    assert strategy["total_runs"] == 3
    assert strategy["wins"] == 2
    assert strategy["losses"] == 1
    assert strategy["net_pnl"] == 4000
    assert strategy["win_rate"] == 66.7
    assert strategy["best_day"] == {"date": "2026-05-06", "pnl": 5000.0}
    assert strategy["worst_day"] == {"date": "2026-05-07", "pnl": -3000.0}
    assert strategy["daily_pnl"][0]["lock_time"] == "10:15"
    assert strategy["monthly_pnl"] == [
        {"month": "2026-05", "pnl": 2000.0, "wins": 1, "losses": 1, "runs": 2},
        {"month": "2026-06", "pnl": 2000.0, "wins": 1, "losses": 0, "runs": 1},
    ]
    assert strategy["lock_stats"]["profit_lock_count"] == 1
    assert strategy["lock_stats"]["loss_lock_count"] == 1
    assert strategy["lock_stats"]["avg_pnl_no_lock"] == 2000


@pytest.mark.asyncio
async def test_workspace_summary_counts_readiness_and_recent_runs():
    user = SimpleNamespace(id=uuid.uuid4())
    paper = PaperSession(
        id=uuid.uuid4(),
        user_id=user.id,
        instrument="NIFTY",
        session_date=date(2026, 5, 6),
        capital=Decimal("2500000.00"),
        status="COMPLETED",
        session_type="paper_replay",
        summary_pnl=None,
        strategy_config_snapshot={"strategy_id": "orb_intraday_spread", "strategy_name": "ORB"},
        created_at=datetime(2026, 5, 6, 16, 0),
    )
    trade = SimpleNamespace(session_id=paper.id, realized_net_pnl=Decimal("1200.00"), strategy_version="v2")
    batch = SessionBatch(
        id=uuid.uuid4(),
        name="Batch",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 6),
        status="completed",
        created_by=user.id,
        strategy_id="orb_intraday_spread",
        strategy_config_snapshot={"instrument": "NIFTY", "capital": 2500000},
        created_at=datetime(2026, 5, 6, 15, 0),
        total_sessions=2,
        completed_sessions=2,
        failed_sessions=0,
        skipped_sessions=0,
        total_pnl=Decimal("2000.00"),
    )
    trading_days = [
        SimpleNamespace(trade_date=date(2026, 5, 6), backtest_ready=True, ingestion_status="completed"),
        SimpleNamespace(trade_date=date(2026, 5, 5), backtest_ready=False, ingestion_status="pending"),
    ]
    db = _FakeDb([
        [1],
        [1],
        [2],
        trading_days,
        [paper],
        [trade],
        [batch],
    ])

    summary = await workbench._build_workspace_summary(user, db)

    assert summary["metrics"]["paper_sessions"] == 1
    assert summary["metrics"]["historical_batches"] == 1
    assert summary["metrics"]["historical_sessions"] == 2
    assert summary["metrics"]["ready_trading_days"] == 1
    assert summary["data_readiness"]["latest_ready_day"] == "2026-05-06"
    assert [item["kind"] for item in summary["recent_runs"]] == ["paper_session", "historical_batch"]
    assert summary["featured_strategies"]


@pytest.mark.asyncio
async def test_day_compare_filters_runs_and_serializes_mtm_series():
    user = SimpleNamespace(id=uuid.uuid4())
    run = _run(
        user_id=user.id,
        strategy_id="unknown_strategy",
        result_json={
            "wings_locked": True,
            "lock_reason": "profit",
            "wing_lock_ts": "2026-05-06T10:15:00",
            "warnings": ["late quote"],
        },
    )
    db = _FakeDb([
        [run],
        [
            _mtm(timestamp=datetime(2026, 5, 6, 9, 51), net_mtm=Decimal("900.00"), event_code="ENTRY"),
            _mtm(timestamp=datetime(2026, 5, 6, 9, 52), net_mtm=None, trail_stop_level=Decimal("500.00")),
        ],
    ])

    payload = await workbench.get_day_compare(
        date="2026-05-06",
        strategy_ids="unknown_strategy",
        db=db,
        current_user=user,
    )

    strategy = payload["strategies"][0]
    assert strategy["strategy_name"] == "unknown_strategy"
    assert strategy["pnl"] == 1000.0
    assert strategy["lock_time"] == "10:15"
    assert strategy["warnings"] == ["late quote"]
    assert strategy["mtm_series"] == [
        {"t": "09:51", "net_mtm": 900.0, "trail_stop": None, "event": "ENTRY"},
        {"t": "09:52", "net_mtm": None, "trail_stop": 500.0, "event": None},
    ]


@pytest.mark.asyncio
async def test_day_compare_returns_empty_for_no_runs():
    payload = await workbench.get_day_compare(
        date="2026-05-06",
        strategy_ids=None,
        db=_FakeDb([[]]),
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert payload == {"date": "2026-05-06", "strategies": []}


@pytest.mark.asyncio
async def test_day_compare_rejects_bad_date():
    with pytest.raises(HTTPException) as exc:
        await workbench.get_day_compare(
            date="bad-date",
            db=_FakeDb([]),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc.value.status_code == 400
    assert "YYYY-MM-DD" in exc.value.detail


def _strategy(modes):
    return {
        "id": "short_straddle_dual_lock",
        "name": "Short Straddle",
        "version": "v1",
        "executor": "generic_v1",
        "modes": modes,
    }


@pytest.mark.asyncio
async def test_create_run_paper_replay_persists_and_returns_navigation(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    strategy = _strategy(["paper_replay"])

    async def stored_token(_db, _user_id):
        return "stored-access-token"

    def engine(session_id, trade_date, instrument, capital, access_token):
        return {
            "decisions": [{
                "id": uuid.uuid4(),
                "session_id": session_id,
                "timestamp": datetime(2026, 5, 6, 9, 30),
                "action": "ENTER",
            }],
            "trade_header": {
                "session_id": session_id,
                "entry_time": datetime(2026, 5, 6, 9, 30),
                "realized_net_pnl": Decimal("1200.00"),
            },
            "trade_legs": [],
            "minute_marks": [],
            "candle_series": [],
            "final_session_state": "CLOSED",
        }

    monkeypatch.setattr(workbench, "get_strategy", lambda sid: strategy)
    monkeypatch.setattr(workbench, "supported_strategy_ids", lambda: [strategy["id"]])
    monkeypatch.setattr(workbench, "get_broker_token", stored_token)
    monkeypatch.setattr(workbench, "run_paper_engine", engine)
    db = _WriteDb()

    response = await workbench.create_run(
        workbench.CreateRunRequest(
            run_type="paper_replay",
            strategy_id=strategy["id"],
            config={"instrument": "nifty", "date": "2026-05-06", "capital": 2500000},
        ),
        _request(),
        BackgroundTasks(),
        db,
        user,
    )

    assert response["navigate_to"].startswith("/workbench/replay/paper_session/")
    session = next(obj for obj in db.added if isinstance(obj, PaperSession))
    assert session.status == "COMPLETED"
    assert session.summary_pnl == Decimal("1200.00")


@pytest.mark.asyncio
async def test_create_run_single_session_and_historical_batch(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    strategy = _strategy(["single_session_backtest", "historical_backtest"])
    run = StrategyRun(
        id=uuid.uuid4(),
        user_id=user.id,
        strategy_id=strategy["id"],
        run_type="single_session_backtest",
        executor="generic_v1",
        instrument="NIFTY",
        trade_date=date(2026, 5, 6),
        status="completed",
        capital=Decimal("2500000.00"),
        config_json={},
        created_at=datetime(2026, 5, 6, 16, 0),
    )

    async def validate_ok(_db, _strategy, config):
        return SimpleNamespace(error=None, warnings=[], instrument="NIFTY")

    async def execute_ok(_db, run_id, _strategy, _config, _validation, _user_id):
        run.id = run_id
        return SimpleNamespace(status="completed", exit_reason="TIME_EXIT")

    monkeypatch.setattr(workbench, "get_strategy", lambda sid: strategy)
    monkeypatch.setattr(workbench, "supported_strategy_ids", lambda: [strategy["id"]])
    monkeypatch.setattr(workbench, "validate_run", validate_ok)
    monkeypatch.setattr(workbench, "execute_run", execute_ok)
    db = _WriteDb([[run]])

    single = await workbench.create_run(
        workbench.CreateRunRequest(run_type="single_session_backtest", strategy_id=strategy["id"], config={}),
        _request(),
        BackgroundTasks(),
        db,
        user,
    )
    assert single["navigate_to"] == f"/workbench/replay/strategy_run/{run.id}"

    batch_db = _WriteDb()
    batch = await workbench.create_run(
        workbench.CreateRunRequest(
            run_type="historical_backtest",
            strategy_id=strategy["id"],
            config={"instrument": "NIFTY", "start_date": "2026-05-01", "end_date": "2026-05-06", "capital": 2500000, "autorun": False},
        ),
        _request(),
        BackgroundTasks(),
        batch_db,
        user,
    )
    assert batch["navigate_to"].startswith("/workbench/history/historical_batch/")
    assert isinstance(batch_db.added[0], SessionBatch)
    assert batch_db.added[0].status == "draft"


@pytest.mark.asyncio
async def test_create_run_and_validate_error_paths(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    strategy = _strategy(["single_session_backtest"])
    monkeypatch.setattr(workbench, "get_strategy", lambda sid: strategy if sid == strategy["id"] else None)
    monkeypatch.setattr(workbench, "supported_strategy_ids", lambda: [strategy["id"]])

    with pytest.raises(HTTPException) as missing:
        await workbench.create_run(workbench.CreateRunRequest(run_type="paper_replay", strategy_id="missing", config={}), _request(), BackgroundTasks(), _WriteDb(), user)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as bad_mode:
        await workbench.create_run(workbench.CreateRunRequest(run_type="paper_replay", strategy_id=strategy["id"], config={}), _request(), BackgroundTasks(), _WriteDb(), user)
    assert bad_mode.value.status_code == 422

    async def validate_bad(_db, _strategy, _config):
        return SimpleNamespace(error="bad config")

    monkeypatch.setattr(workbench, "validate_run", validate_bad)
    with pytest.raises(HTTPException) as invalid:
        await workbench.validate_run_endpoint(
            workbench.CreateRunRequest(run_type="single_session_backtest", strategy_id=strategy["id"], config={}),
            _WriteDb(),
            user,
        )
    assert invalid.value.status_code == 422


@pytest.mark.asyncio
async def test_run_detail_replay_and_compare_paths(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    session = PaperSession(
        id=uuid.uuid4(),
        user_id=user.id,
        instrument="NIFTY",
        session_date=date(2026, 5, 6),
        capital=Decimal("2500000.00"),
        status="COMPLETED",
        session_type="paper_replay",
        strategy_config_snapshot={},
    )
    batch = SessionBatch(
        id=uuid.uuid4(),
        name="Batch",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 6),
        status="completed",
        created_by=user.id,
        strategy_config_snapshot={"instrument": "NIFTY"},
    )
    run = _run(user_id=user.id)

    async def owned_session(_sid, _user, _db, session_type=None):
        return session

    async def owned_batch(_bid, _user, _db):
        return batch

    async def replay_payload(*_args, **_kwargs):
        return {"run": {"id": str(run.id)}, "legs": [], "events": [], "mtm_series": [], "shadow_mtm_series": [], "spot_series_full": [], "vix_series_full": [], "leg_candles": {}}

    monkeypatch.setattr(workbench, "_owned_paper_session", owned_session)
    monkeypatch.setattr(workbench, "_owned_batch", owned_batch)
    monkeypatch.setattr(workbench, "_build_strategy_run_replay_payload", replay_payload)
    db = _FakeDb([
        [],       # paper_session detail trade
        [session],
        [run],
        [run],
        [],       # compare paper trade
        [session],
        [run],
    ])

    assert (await workbench.get_run_detail("paper_session", str(session.id), db, user))["run"]["id"] == str(session.id)
    assert (await workbench.get_run_detail("historical_batch", str(batch.id), db, user))["run"]["id"] == str(batch.id)
    assert (await workbench.get_run_detail("strategy_run", str(run.id), db, user))["run"]["id"] == str(run.id)
    assert (await workbench.get_run_replay("strategy_run", str(run.id), db, user))["run"]["id"] == str(run.id)

    compared = await workbench.compare_runs(
        refs=f"paper_session:{session.id},historical_batch:{batch.id},strategy_run:{run.id}",
        db=db,
        current_user=user,
    )
    assert len(compared["items"]) == 3

    with pytest.raises(HTTPException) as bad_refs:
        await workbench.compare_runs(refs=None, db=db, current_user=user)
    assert bad_refs.value.status_code == 400

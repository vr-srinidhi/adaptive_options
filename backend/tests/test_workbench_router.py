from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from datetime import date, datetime
from types import SimpleNamespace

from fastapi import FastAPI

_jose = types.ModuleType("jose")
_jose.JWTError = Exception
_jose.jwt = types.SimpleNamespace(
    encode=lambda *args, **kwargs: "token",
    decode=lambda *args, **kwargs: {},
)
sys.modules.setdefault("jose", _jose)

_auth = types.ModuleType("app.dependencies.auth")


async def _stub_current_active_user():
    return SimpleNamespace(id=uuid.uuid4(), is_active=True)


_auth.get_current_active_user = _stub_current_active_user
sys.modules.setdefault("app.dependencies.auth", _auth)

from app.database import get_db
from app.routers import workbench


class _ScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _ExecuteResult:
    def __init__(self, *, rows=None):
        self._rows = list(rows or [])

    def scalars(self):
        return _ScalarResult(self._rows)


class FakeDB:
    def __init__(self, *, paper_sessions=None, paper_trades=None, batches=None):
        self.paper_sessions = list(paper_sessions or [])
        self.paper_trades = list(paper_trades or [])
        self.batches = list(batches or [])

    async def execute(self, query):
        sql = str(query)
        limit = getattr(getattr(query, "_limit_clause", None), "value", None)
        offset = getattr(getattr(query, "_offset_clause", None), "value", 0) or 0

        if "FROM paper_sessions" in sql and "paper_sessions.session_type = :session_type_1" in sql:
            rows = sorted(self.paper_sessions, key=lambda item: item.created_at, reverse=True)
            if offset:
                rows = rows[offset:]
            if limit is not None:
                rows = rows[:limit]
            return _ExecuteResult(rows=rows)

        if "FROM paper_trade_headers" in sql:
            return _ExecuteResult(rows=self.paper_trades)

        if "FROM session_batches" in sql:
            rows = sorted(self.batches, key=lambda item: item.created_at, reverse=True)
            if offset:
                rows = rows[offset:]
            if limit is not None:
                rows = rows[:limit]
            return _ExecuteResult(rows=rows)

        if "FROM strategy_runs" in sql:
            return _ExecuteResult(rows=[])

        raise AssertionError(f"Unexpected query in fake DB: {sql}")


def _make_app(fake_db: FakeDB | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(workbench.router)

    async def _override_user():
        return SimpleNamespace(id=uuid.uuid4(), is_active=True)

    if fake_db is not None:
        async def _override_db():
            yield fake_db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[workbench.get_current_active_user] = _override_user

    return app


def _get(app: FastAPI, path: str) -> tuple[int, dict]:
    async def _request():
        status_code = None
        body = b""

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal status_code, body
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                body += message.get("body", b"")

        raw_path, _, raw_query = path.partition("?")
        await app(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": raw_path,
                "raw_path": raw_path.encode(),
                "query_string": raw_query.encode(),
                "headers": [],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
        return status_code, json.loads(body.decode() or "{}")

    return asyncio.run(_request())


def test_get_strategies_exposes_catalog_over_http():
    status_code, payload = _get(_make_app(), "/api/v2/strategies")

    assert status_code == 200
    strategies = payload["strategies"]
    orb = next(item for item in strategies if item["id"] == "orb_intraday_spread")
    assert orb["status"] == "available"
    assert orb["defaults"]["paper_replay"]["date"]
    assert orb["visual_hints"]["shape"] == "adaptive"


def test_workspace_summary_returns_http_payload(monkeypatch):
    async def _fake_summary(user, db):
        return {
            "metrics": {"paper_sessions": 2, "historical_batches": 1},
            "data_readiness": {"ready_days": 10},
            "recent_runs": [],
            "featured_strategies": [],
        }

    monkeypatch.setattr(workbench, "_build_workspace_summary", _fake_summary)
    status_code, payload = _get(_make_app(FakeDB()), "/api/v2/workspace/summary")

    assert status_code == 200
    assert payload["metrics"]["paper_sessions"] == 2


def test_list_runs_supports_offset_pagination_over_http():
    session_a = SimpleNamespace(
        id=uuid.uuid4(),
        instrument="NIFTY",
        session_date=date(2026, 4, 7),
        summary_pnl=1200.0,
        capital=2500000,
        status="COMPLETED",
        final_session_state="TRADE_CLOSED",
        decision_count=12,
        created_at=datetime(2026, 4, 7, 16, 0),
        strategy_config_snapshot={"strategy_id": "orb_intraday_spread", "strategy_name": "Opening Range Spread", "strategy_version": "v2.0"},
        session_type="paper_replay",
    )
    session_b = SimpleNamespace(
        id=uuid.uuid4(),
        instrument="BANKNIFTY",
        session_date=date(2026, 4, 6),
        summary_pnl=-500.0,
        capital=2500000,
        status="COMPLETED",
        final_session_state="TRADE_CLOSED",
        decision_count=9,
        created_at=datetime(2026, 4, 6, 16, 0),
        strategy_config_snapshot={"strategy_id": "orb_intraday_spread", "strategy_name": "Opening Range Spread", "strategy_version": "v2.0"},
        session_type="paper_replay",
    )
    trade_a = SimpleNamespace(session_id=session_a.id, realized_net_pnl=1200.0, strategy_version="v2.0")
    trade_b = SimpleNamespace(session_id=session_b.id, realized_net_pnl=-500.0, strategy_version="v2.0")
    batch_a = SimpleNamespace(
        id=uuid.uuid4(),
        name="Apr replay",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 7),
        status="completed",
        strategy_id="orb_intraday_spread",
        strategy_version="v2.0",
        strategy_config_snapshot={"instrument": "NIFTY", "capital": 2500000, "strategy_id": "orb_intraday_spread", "strategy_name": "Opening Range Spread"},
        created_at=datetime(2026, 4, 7, 15, 0),
        total_pnl=8800.0,
        completed_sessions=5,
        total_sessions=5,
        failed_sessions=0,
        skipped_sessions=0,
    )
    batch_b = SimpleNamespace(
        id=uuid.uuid4(),
        name="Older replay",
        start_date=date(2026, 3, 20),
        end_date=date(2026, 3, 28),
        status="completed",
        strategy_id="orb_intraday_spread",
        strategy_version="v2.0",
        strategy_config_snapshot={"instrument": "NIFTY", "capital": 2500000, "strategy_id": "orb_intraday_spread", "strategy_name": "Opening Range Spread"},
        created_at=datetime(2026, 4, 5, 15, 0),
        total_pnl=2400.0,
        completed_sessions=4,
        total_sessions=4,
        failed_sessions=0,
        skipped_sessions=0,
    )
    fake_db = FakeDB(
        paper_sessions=[session_a, session_b],
        paper_trades=[trade_a, trade_b],
        batches=[batch_a, batch_b],
    )
    status_code, payload = _get(_make_app(fake_db), "/api/v2/runs?limit=2&offset=1")

    assert status_code == 200
    runs = payload["runs"]
    assert [item["id"] for item in runs] == [str(batch_a.id), str(session_b.id)]
    assert runs[0]["strategy_name"] == "Opening Range Spread"

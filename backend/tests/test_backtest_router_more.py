from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
import sys
import types
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

_jose = types.ModuleType("jose")
_jose.JWTError = Exception
_jose.jwt = types.SimpleNamespace(encode=lambda *a, **k: "token", decode=lambda *a, **k: {})
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

import app.routers.backtest as backtest
from app.models.session import BacktestSession


class _ScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalars(self):
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalar(self):
        return self._scalar


class _FakeDb:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.commits = 0
        self.refreshed = []
        self.executed = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def execute(self, stmt):
        self.executed.append(stmt)
        if not self.results:
            return _Result()
        return self.results.pop(0)


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    original = backtest.limiter.enabled
    backtest.limiter.enabled = False
    yield
    backtest.limiter.enabled = original


def _request():
    return Request({"type": "http", "method": "POST", "path": "/backtest/run", "headers": [], "client": ("127.0.0.1", 1)})


def _session(**overrides):
    values = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "instrument": "NIFTY",
        "session_date": date(2026, 5, 6),
        "capital": Decimal("2500000.00"),
        "regime": "BULLISH",
        "iv_rank": 40,
        "strategy": "IRON_CONDOR",
        "pnl": Decimal("1200.00"),
        "pnl_pct": Decimal("0.10"),
        "wl": "WIN",
        "legs": [{"side": "SELL"}],
        "min_data": [{"close": 22500}],
        "created_at": datetime(2026, 5, 6, 16, 0),
    }
    values.update(overrides)
    return BacktestSession(**values)


def _simulation(trade_date, _instrument=None, _capital=None):
    return {
        "instrument": "NIFTY",
        "session_date": trade_date,
        "capital": 2500000,
        "regime": "BULLISH",
        "iv_rank": 40,
        "strategy": "IRON_CONDOR",
        "entry_time": "09:30",
        "exit_time": "15:20",
        "exit_reason": "TIME_EXIT",
        "spot_in": 22500,
        "spot_out": 22550,
        "lots": 1,
        "max_profit": 10000,
        "max_loss": 20000,
        "pnl": 1200,
        "pnl_pct": 0.1,
        "wl": "WIN",
        "ema5": 1,
        "ema20": 1,
        "rsi14": 55,
        "legs": [],
        "min_data": [],
        "expiry_date": trade_date,
        "data_source": "warehouse",
    }


@pytest.mark.asyncio
async def test_run_backtest_validates_inputs():
    user = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(HTTPException):
        await backtest.run_backtest(_request(), backtest.RunBacktestRequest(instrument="NIFTY", startDate="bad", endDate="2026-05-06", capital=100000), _FakeDb(), user)
    with pytest.raises(HTTPException):
        await backtest.run_backtest(_request(), backtest.RunBacktestRequest(instrument="NIFTY", startDate="2026-05-07", endDate="2026-05-06", capital=100000), _FakeDb(), user)
    with pytest.raises(HTTPException):
        await backtest.run_backtest(_request(), backtest.RunBacktestRequest(instrument="SENSEX", startDate="2026-05-06", endDate="2026-05-06", capital=100000), _FakeDb(), user)
    with pytest.raises(HTTPException):
        await backtest.run_backtest(_request(), backtest.RunBacktestRequest(instrument="NIFTY", startDate="2026-05-06", endDate="2026-05-06", capital=100), _FakeDb(), user)


@pytest.mark.asyncio
async def test_run_backtest_persists_trading_and_non_trading_days(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    trade_day = date(2026, 5, 6)
    holiday = date(2026, 5, 7)
    monkeypatch.setattr(backtest, "get_trading_days", lambda start, end: [(trade_day, backtest.TRADING_DAY), (holiday, backtest.HOLIDAY)])
    monkeypatch.setattr(backtest, "run_day_simulation", _simulation)
    db = _FakeDb()

    rows = await backtest.run_backtest(
        _request(),
        backtest.RunBacktestRequest(instrument="nifty", startDate="2026-05-06", endDate="2026-05-07", capital=2500000),
        db,
        user,
    )

    assert len(rows) == 2
    assert any(obj.strategy == "NO_TRADE" and obj.no_trade_reason == "HOLIDAY" for obj in db.added)
    assert any(obj.strategy == "IRON_CONDOR" for obj in db.added)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_result_summary_and_clear_endpoints():
    user = SimpleNamespace(id=uuid.uuid4())
    win = _session(user_id=user.id, pnl=Decimal("1200.00"), wl="WIN")
    loss = _session(user_id=user.id, pnl=Decimal("-500.00"), wl="LOSS")
    no_trade = _session(user_id=user.id, pnl=Decimal("0.00"), wl="NO_TRADE", strategy="NO_TRADE")
    db = _FakeDb([
        _Result(rows=[win, loss]),
        _Result(scalar=win),
        _Result(rows=[win, loss, no_trade]),
        _Result(scalar=3),
        _Result(),
    ])

    results = await backtest.get_results("nifty", 10, 0, db, user)
    assert len(results) == 2
    detail = await backtest.get_session(str(win.id), db, user)
    assert detail["legs"] == [{"side": "SELL"}]
    summary = await backtest.get_summary(None, db, user)
    assert summary["totalPnl"] == 700
    assert summary["winRate"] == 50
    assert summary["totalTrades"] == 2
    assert (await backtest.clear_results(db, user)) == {"deleted": 3}


@pytest.mark.asyncio
async def test_session_detail_errors_and_empty_summary():
    user = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(HTTPException) as bad_id:
        await backtest.get_session("bad", _FakeDb(), user)
    assert bad_id.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        await backtest.get_session(str(uuid.uuid4()), _FakeDb([_Result(scalar=None)]), user)
    assert missing.value.status_code == 404
    assert await backtest.get_summary(None, _FakeDb([_Result(rows=[])]), user) == {
        "totalPnl": 0,
        "winRate": 0,
        "totalTrades": 0,
        "bestDay": None,
        "worstDay": None,
        "totalSessions": 0,
    }

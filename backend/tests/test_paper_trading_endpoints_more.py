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

import app.routers.paper_trading as paper_trading
from app.models.paper_trade import (
    MinuteDecision,
    PaperCandleSeries,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.services.zerodha_client import DataUnavailableError


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    original = paper_trading.limiter.enabled
    paper_trading.limiter.enabled = False
    yield
    paper_trading.limiter.enabled = original


class _ScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows=None, scalar=None, all_rows=None):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._all_rows = list(all_rows or [])

    def scalars(self):
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._all_rows


class _FakeDb:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.commits = 0
        self.flushes = 0
        self.refreshed = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushes += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def execute(self, *_args, **_kwargs):
        if not self.results:
            raise AssertionError("unexpected query")
        return self.results.pop(0)


def _session(user_id=None, summary_pnl=None):
    return PaperSession(
        id=uuid.uuid4(),
        user_id=user_id,
        instrument="NIFTY",
        session_date=date(2026, 5, 6),
        capital=Decimal("2500000.00"),
        status="COMPLETED",
        decision_count=2,
        final_session_state="CLOSED",
        summary_pnl=summary_pnl,
    )


def _trade(session_id, trade_id=None):
    return PaperTradeHeader(
        id=trade_id or uuid.uuid4(),
        session_id=session_id,
        entry_time=datetime(2026, 5, 6, 9, 30),
        exit_time=datetime(2026, 5, 6, 15, 20),
        expiry=date(2026, 5, 12),
        realized_net_pnl=Decimal("1200.00"),
        status="CLOSED",
        option_type="CE",
        long_strike=22400,
        short_strike=22500,
    )


def _decision(session_id):
    return MinuteDecision(
        id=uuid.uuid4(),
        session_id=session_id,
        timestamp=datetime(2026, 5, 6, 9, 30),
        action="ENTER",
        reason_code="ENTRY",
        spot_close=Decimal("22500.00"),
    )


def _request():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/paper/session/run",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
    })


def _engine_result(session_id):
    trade_id = uuid.uuid4()
    return {
        "decisions": [{
            "id": uuid.uuid4(),
            "session_id": session_id,
            "timestamp": datetime(2026, 5, 6, 9, 30),
            "action": "ENTER",
            "reason_code": "ENTRY",
        }],
        "trade_header": {
            "id": trade_id,
            "session_id": session_id,
            "entry_time": datetime(2026, 5, 6, 9, 30),
            "realized_net_pnl": Decimal("1200.00"),
        },
        "trade_legs": [{"leg_side": "LONG", "option_type": "CE", "strike": 22400, "expiry": date(2026, 5, 12)}],
        "minute_marks": [{"timestamp": datetime(2026, 5, 6, 9, 31), "estimated_net_mtm": Decimal("100.00")}],
        "candle_series": [{"session_id": session_id, "series_type": "SPOT", "candles": [{"close": 22500}]}],
        "final_session_state": "CLOSED",
    }


@pytest.mark.asyncio
async def test_run_session_validates_inputs():
    user = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(HTTPException) as bad_date:
        await paper_trading.run_session(_request(), paper_trading.RunSessionRequest(instrument="NIFTY", date="bad", capital=100000, access_token="1234567890"), _FakeDb(), user)
    assert bad_date.value.status_code == 400

    with pytest.raises(HTTPException) as bad_instrument:
        await paper_trading.run_session(_request(), paper_trading.RunSessionRequest(instrument="SENSEX", date="2026-05-06", capital=100000, access_token="1234567890"), _FakeDb(), user)
    assert "NIFTY" in bad_instrument.value.detail

    with pytest.raises(HTTPException) as bad_capital:
        await paper_trading.run_session(_request(), paper_trading.RunSessionRequest(instrument="NIFTY", date="2026-05-06", capital=100, access_token="1234567890"), _FakeDb(), user)
    assert "Capital" in bad_capital.value.detail


@pytest.mark.asyncio
async def test_run_session_uses_stored_token_and_persists_engine_output(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    captured = {}

    async def stored_token(_db, uid):
        assert uid == user.id
        return "stored-access-token"

    def engine(session_id, trade_date, instrument, capital, token):
        captured.update(session_id=session_id, trade_date=trade_date, instrument=instrument, capital=capital, token=token)
        return _engine_result(session_id)

    monkeypatch.setattr(paper_trading, "get_broker_token", stored_token)
    monkeypatch.setattr(paper_trading, "run_paper_engine", engine)

    db = _FakeDb()
    response = await paper_trading.run_session(
        _request(),
        paper_trading.RunSessionRequest(instrument=" nifty ", date="2026-05-06", capital=2500000),
        db,
        user,
    )

    assert response["status"] == "COMPLETED"
    assert response["trade_opened"] is True
    assert captured["instrument"] == "NIFTY"
    assert captured["token"] == "stored-access-token"
    assert any(isinstance(obj, PaperSession) and obj.status == "COMPLETED" for obj in db.added)
    assert any(isinstance(obj, PaperTradeHeader) for obj in db.added)
    assert any(isinstance(obj, PaperTradeLeg) for obj in db.added)
    assert any(isinstance(obj, PaperTradeMinuteMark) for obj in db.added)
    assert any(isinstance(obj, PaperCandleSeries) for obj in db.added)


@pytest.mark.asyncio
async def test_run_session_maps_token_and_engine_failures(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())

    async def no_token(_db, _uid):
        return None

    monkeypatch.setattr(paper_trading, "get_broker_token", no_token)
    with pytest.raises(HTTPException) as missing:
        await paper_trading.run_session(_request(), paper_trading.RunSessionRequest(instrument="NIFTY", date="2026-05-06", capital=2500000), _FakeDb(), user)
    assert missing.value.status_code == 400

    def unavailable(*_args):
        raise DataUnavailableError("no candles")

    monkeypatch.setattr(paper_trading, "run_paper_engine", unavailable)
    with pytest.raises(HTTPException) as data_error:
        await paper_trading.run_session(_request(), paper_trading.RunSessionRequest(instrument="NIFTY", date="2026-05-06", capital=2500000, access_token="1234567890"), _FakeDb(), user)
    assert data_error.value.status_code == 422


@pytest.mark.asyncio
async def test_list_sessions_and_detail_endpoints():
    user = SimpleNamespace(id=uuid.uuid4())
    session = _session(user.id)
    trade = _trade(session.id)
    decision = _decision(session.id)
    mark = PaperTradeMinuteMark(trade_id=trade.id, timestamp=datetime(2026, 5, 6, 9, 31), estimated_net_mtm=Decimal("100.00"))
    candle = PaperCandleSeries(session_id=session.id, series_type="SPOT", candles=[{"close": 22500}])
    db = _FakeDb([
        _Result(rows=[session]),
        _Result(all_rows=[(session.id, Decimal("1200.00"))]),
        _Result(scalar=session),
        _Result(all_rows=[SimpleNamespace(action="ENTER", cnt=1)]),
        _Result(scalar=Decimal("1200.00")),
        _Result(scalar=session),
        _Result(rows=[decision]),
        _Result(scalar=session),
        _Result(scalar=trade),
        _Result(rows=[PaperTradeLeg(trade_id=trade.id, leg_side="LONG", option_type="CE", strike=22400, expiry=date(2026, 5, 12))]),
        _Result(scalar=session),
        _Result(scalar=trade),
        _Result(rows=[mark]),
        _Result(scalar=session),
        _Result(rows=[candle]),
    ])

    sessions = await paper_trading.list_sessions(instrument="nifty", db=db, user=user)
    assert sessions[0]["summary_pnl"] == 1200.0
    detail = await paper_trading.get_session(str(session.id), db, user)
    assert detail["action_summary"] == {"ENTER": 1}
    assert (await paper_trading.get_decisions(str(session.id), "enter", 10, 0, db, user))[0]["action"] == "ENTER"
    assert (await paper_trading.get_trade(str(session.id), db, user))["trade"]["legs"][0]["strike"] == 22400
    assert (await paper_trading.get_marks(str(session.id), db, user))[0]["estimated_net_mtm"] == 100.0
    assert (await paper_trading.get_candles(str(session.id), db, user))[0]["series_type"] == "SPOT"


@pytest.mark.asyncio
async def test_export_bundle_dedupes_ids_and_collects_related_rows():
    user = SimpleNamespace(id=uuid.uuid4())
    session = _session(user.id)
    trade = _trade(session.id)
    decision = _decision(session.id)
    leg = PaperTradeLeg(trade_id=trade.id, leg_side="LONG", option_type="CE", strike=22400, expiry=date(2026, 5, 12))
    mark = PaperTradeMinuteMark(trade_id=trade.id, timestamp=datetime(2026, 5, 6, 9, 31), estimated_net_mtm=Decimal("100.00"))
    candle = PaperCandleSeries(session_id=session.id, series_type="SPOT", candles=[{"close": 22500}])
    db = _FakeDb([
        _Result(rows=[session]),
        _Result(rows=[decision]),
        _Result(rows=[trade]),
        _Result(rows=[leg]),
        _Result(rows=[mark]),
        _Result(rows=[candle]),
    ])

    payload = await paper_trading.export_sessions_bundle(
        paper_trading.SessionExportBundleRequest(session_ids=[str(session.id), str(session.id)]),
        db,
        user,
    )

    bundle = payload["sessions"][0]
    assert bundle["session"]["action_summary"] == {"ENTER": 1}
    assert bundle["trade"]["realized_net_pnl"] == 1200.0
    assert bundle["marks"][0]["estimated_net_mtm"] == 100.0
    assert bundle["candle_series"][0]["candles"] == [{"close": 22500}]


@pytest.mark.asyncio
async def test_export_bundle_rejects_empty_invalid_and_missing_ids():
    user = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(HTTPException) as empty:
        await paper_trading.export_sessions_bundle(paper_trading.SessionExportBundleRequest(session_ids=[]), _FakeDb(), user)
    assert empty.value.status_code == 400

    with pytest.raises(HTTPException) as invalid:
        await paper_trading.export_sessions_bundle(paper_trading.SessionExportBundleRequest(session_ids=["bad"]), _FakeDb(), user)
    assert invalid.value.status_code == 400

    missing_id = uuid.uuid4()
    with pytest.raises(HTTPException) as missing:
        await paper_trading.export_sessions_bundle(paper_trading.SessionExportBundleRequest(session_ids=[str(missing_id)]), _FakeDb([_Result(rows=[])]), user)
    assert missing.value.status_code == 404

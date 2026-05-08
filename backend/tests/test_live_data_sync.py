from datetime import date, datetime
from types import SimpleNamespace
import sys
import types

import pandas as pd
import pytest

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

from app.services.live_data_sync import (
    STATUS_FAILED,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SUCCESS,
    _sync_status_from_result,
)
import app.services.live_data_sync as live_data_sync
from app.services.live_ingestion import (
    _existing_spot_median,
    _last_thursday_of_month,
    _missing_option_contracts,
    _select_nearest_nifty_future,
    _to_futures_df,
    _to_option_df,
    _to_spot_df,
    _to_vix_df,
)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value


class _ExecuteResult:
    def __init__(self, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = scalars or []

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _ScalarResult(self._scalars)

    def all(self):
        return self._scalars


class _FakeDb:
    def __init__(self, execute_results=None):
        self.added = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self.merged = []
        self._execute_results = list(execute_results or [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        return None

    async def merge(self, obj):
        self.merged.append(obj)
        return obj

    async def execute(self, *_args, **_kwargs):
        if not self._execute_results:
            raise AssertionError("unexpected db.execute call")
        return self._execute_results.pop(0)


def test_select_nearest_nifty_future_uses_next_valid_expiry():
    trade_date = date(2026, 5, 6)
    instruments = [
        {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 4, 30), "instrument_token": 1},
        {"name": "BANKNIFTY", "instrument_type": "FUT", "expiry": date(2026, 5, 7), "instrument_token": 2},
        {"name": "NIFTY", "instrument_type": "CE", "expiry": date(2026, 5, 7), "instrument_token": 3},
        {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 5, 28), "instrument_token": 4},
        {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 5, 7), "instrument_token": 5},
    ]

    selected = _select_nearest_nifty_future(instruments, trade_date)

    assert selected["instrument_token"] == 5


def test_to_futures_df_matches_warehouse_columns():
    records = [{
        "date": datetime(2026, 5, 6, 9, 15),
        "open": 22500.0,
        "high": 22510.0,
        "low": 22490.0,
        "close": 22505.0,
        "volume": 1000,
        "oi": 50000,
    }]

    df = _to_futures_df(records, date(2026, 5, 6), date(2026, 5, 28))

    assert list(df.columns) == [
        "trade_date", "timestamp", "symbol", "expiry_date",
        "open", "high", "low", "close", "volume", "open_interest", "source_file",
    ]
    assert df.iloc[0]["symbol"] == "NIFTY"
    assert df.iloc[0]["open_interest"] == 50000
    assert df.iloc[0]["source_file"] == "zerodha_live"


def test_live_ingestion_dataframe_helpers_strip_tz_and_fill_defaults():
    trade_date = date(2026, 5, 6)
    records = [{
        "date": pd.Timestamp("2026-05-06T09:15:00+05:30"),
        "open": 22500.0,
        "high": 22510.0,
        "low": 22490.0,
        "close": 22505.0,
    }]

    spot = _to_spot_df(records, trade_date, "NIFTY")
    vix = _to_vix_df(records, trade_date)
    option = _to_option_df(records, trade_date, date(2026, 5, 12), "CE", 22500)

    assert spot.iloc[0]["timestamp"].tzinfo is None
    assert spot.iloc[0]["volume"] == 0
    assert vix.iloc[0]["symbol"] == "INDIA VIX"
    assert option.iloc[0]["open_interest"] == 0
    assert option.iloc[0]["ltp"] == 22505.0
    assert option.iloc[0]["source_file"] == "zerodha_live"


def test_last_thursday_of_month_handles_regular_and_leap_months():
    assert _last_thursday_of_month(date(2026, 5, 1)) == date(2026, 5, 28)
    assert _last_thursday_of_month(date(2024, 2, 1)) == date(2024, 2, 29)


def test_sync_status_requires_spot_and_options_for_success():
    assert _sync_status_from_result(
        {"status": "completed", "failed_items": []},
        {"spot_rows": 376, "options_rows": 10000},
    ) == STATUS_SUCCESS
    assert _sync_status_from_result(
        {"status": "completed_with_warnings", "failed_items": ["futures"]},
        {"spot_rows": 376, "options_rows": 10000},
    ) == STATUS_PARTIAL_SUCCESS
    assert _sync_status_from_result(
        {"status": "completed_with_warnings", "failed_items": ["spot"]},
        {"spot_rows": 0, "options_rows": 10000},
    ) == STATUS_FAILED


def test_missing_option_contracts_filters_contracts_already_in_warehouse():
    exp = date(2026, 5, 12)
    contracts = [
        (101, exp, "CE", 22500),
        (102, exp, "PE", 22500),
        (103, exp, "CE", 22550),
    ]
    existing = {
        (exp, "CE", 22500),
        (exp, "PE", 22500),
    }

    assert _missing_option_contracts(contracts, existing) == [
        (103, exp, "CE", 22550),
    ]


@pytest.mark.asyncio
async def test_existing_spot_median_returns_none_for_empty_warehouse():
    db = _FakeDb([_ExecuteResult(scalars=[])])

    assert await _existing_spot_median(db, date(2026, 5, 6)) is None


@pytest.mark.asyncio
async def test_existing_spot_median_returns_middle_or_average():
    odd_db = _FakeDb([_ExecuteResult(scalars=[22500, 22400, 22600])])
    even_db = _FakeDb([_ExecuteResult(scalars=[22400, 22600])])

    assert await _existing_spot_median(odd_db, date(2026, 5, 6)) == 22500
    assert await _existing_spot_median(even_db, date(2026, 5, 6)) == 22500


@pytest.mark.asyncio
async def test_run_daily_live_data_sync_skips_when_token_missing(monkeypatch):
    async def latest_token(_db):
        return None

    monkeypatch.setattr(live_data_sync, "_latest_zerodha_token", latest_token)
    db = _FakeDb()

    run = await live_data_sync.run_daily_live_data_sync(db, date(2026, 5, 6))

    assert run.status == live_data_sync.STATUS_SKIPPED_TOKEN_MISSING
    assert run.token_status == live_data_sync.TOKEN_MISSING
    assert "No Zerodha token" in run.notes
    assert db.commits == 1


@pytest.mark.asyncio
async def test_run_daily_live_data_sync_skips_expired_token(monkeypatch):
    async def latest_token(_db):
        return SimpleNamespace(token_date=date(2026, 5, 5), encrypted_token="old")

    monkeypatch.setattr(live_data_sync, "_latest_zerodha_token", latest_token)
    db = _FakeDb()

    run = await live_data_sync.run_daily_live_data_sync(db, date(2026, 5, 6))

    assert run.status == live_data_sync.STATUS_SKIPPED_TOKEN_EXPIRED
    assert run.token_status == live_data_sync.TOKEN_EXPIRED
    assert "2026-05-05" in run.notes
    assert db.commits == 1


@pytest.mark.asyncio
async def test_run_daily_live_data_sync_records_token_decryption_failure(monkeypatch):
    async def latest_token(_db):
        return SimpleNamespace(token_date=date(2026, 5, 6), encrypted_token="bad")

    def decrypt(_token):
        raise ValueError("ciphertext invalid")

    monkeypatch.setattr(live_data_sync, "_latest_zerodha_token", latest_token)
    monkeypatch.setattr(live_data_sync, "decrypt_token", decrypt)
    db = _FakeDb()

    run = await live_data_sync.run_daily_live_data_sync(db, date(2026, 5, 6))

    assert run.status == live_data_sync.STATUS_FAILED_TOKEN_DECRYPTION
    assert run.token_status == live_data_sync.TOKEN_DECRYPTION_FAILED
    assert run.error_message == "ciphertext invalid"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_run_daily_live_data_sync_records_token_validation_failure(monkeypatch):
    async def latest_token(_db):
        return SimpleNamespace(token_date=date(2026, 5, 6), encrypted_token="ok")

    async def to_thread(_func, *_args):
        raise RuntimeError("token rejected")

    monkeypatch.setattr(live_data_sync, "_latest_zerodha_token", latest_token)
    monkeypatch.setattr(live_data_sync, "decrypt_token", lambda _token: "access-token")
    monkeypatch.setattr(live_data_sync.asyncio, "to_thread", to_thread)
    db = _FakeDb()

    run = await live_data_sync.run_daily_live_data_sync(db, date(2026, 5, 6))

    assert run.status == live_data_sync.STATUS_FAILED_TOKEN_VALIDATION
    assert run.token_status == live_data_sync.TOKEN_VALIDATION_FAILED
    assert run.error_message == "token rejected"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_run_daily_live_data_sync_persists_partial_success_summary(monkeypatch):
    trade_date = date(2026, 5, 6)

    async def latest_token(_db):
        return SimpleNamespace(token_date=trade_date, encrypted_token="ok")

    async def to_thread(func, *args):
        return func(*args)

    async def ingest(_db, access_token, requested_date, force=False):
        assert access_token == "access-token"
        assert requested_date == trade_date
        assert force is True
        return {
            "status": "completed_with_warnings",
            "failed_items": ["futures"],
            "option_contracts": 3,
            "expiries": ["2026-05-12"],
            "notes": "futures unavailable",
        }

    async def counts(_db, requested_date):
        assert requested_date == trade_date
        return {
            "spot_rows": 376,
            "vix_rows": 375,
            "futures_rows": 0,
            "options_rows": 12000,
            "option_contracts": 3,
            "expiries": ["2026-05-12"],
        }

    monkeypatch.setattr(live_data_sync, "_latest_zerodha_token", latest_token)
    monkeypatch.setattr(live_data_sync, "decrypt_token", lambda _token: "access-token")
    monkeypatch.setattr(live_data_sync, "validate_access_token_with_token", lambda _token: True)
    monkeypatch.setattr(live_data_sync.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(live_data_sync, "ingest_live_day", ingest)
    monkeypatch.setattr(live_data_sync, "_warehouse_counts", counts)
    db = _FakeDb()

    run = await live_data_sync.run_daily_live_data_sync(
        db,
        trade_date,
        triggered_by="manual",
        force=True,
    )

    assert run.status == live_data_sync.STATUS_PARTIAL_SUCCESS
    assert run.token_status == live_data_sync.TOKEN_VALID
    assert run.spot_rows == 376
    assert run.futures_rows == 0
    assert run.options_rows == 12000
    assert run.failed_items_json == ["futures"]
    assert run.notes == "futures unavailable"
    assert db.flushes == 2
    assert db.commits == 1


@pytest.mark.asyncio
async def test_started_run_creation_status_and_today_payload(monkeypatch):
    trade_date = date(2026, 5, 6)
    started = SimpleNamespace(
        id=SimpleNamespace(),
        trade_date=trade_date,
        started_at=datetime(2026, 5, 6, 16, 0),
        completed_at=datetime(2026, 5, 6, 16, 5),
        triggered_by="manual",
        token_status=live_data_sync.TOKEN_VALID,
        status=live_data_sync.STATUS_SUCCESS,
        spot_rows=376,
        vix_rows=375,
        futures_rows=376,
        options_rows=12000,
        option_contracts=4,
        expiries_json=["2026-05-12"],
        notes="ok",
        error_message=None,
    )
    db = _FakeDb([
        _ExecuteResult(scalar=None),
        _ExecuteResult(scalar=started),
        _ExecuteResult(scalar=started),
        _ExecuteResult(scalar=SimpleNamespace(backtest_ready=True)),
    ])

    created = await live_data_sync.create_started_live_data_sync_run(db, trade_date, "manual")
    assert created.status == live_data_sync.STATUS_STARTED
    assert db.commits == 1
    assert await live_data_sync.get_started_live_data_sync_run(db, trade_date) is started
    payload = await live_data_sync.get_live_data_sync_today(db, trade_date)
    assert payload["status"] == live_data_sync.STATUS_SUCCESS
    assert payload["rows"] == {"spot": 376, "vix": 375, "futures": 376, "options": 12000}
    assert payload["backtest_ready"] is True


@pytest.mark.asyncio
async def test_today_payload_without_run_uses_current_token_status(monkeypatch):
    trade_date = date(2026, 5, 6)

    async def token_status(_db, requested_date):
        assert requested_date == trade_date
        return live_data_sync.TOKEN_EXPIRED

    monkeypatch.setattr(live_data_sync, "current_system_token_status", token_status)
    db = _FakeDb([_ExecuteResult(scalar=None)])

    payload = await live_data_sync.get_live_data_sync_today(db, trade_date)

    assert payload["status"] == live_data_sync.STATUS_NOT_RUN
    assert payload["token_status"] == live_data_sync.TOKEN_EXPIRED
    assert payload["rows"]["spot"] == 0


@pytest.mark.asyncio
async def test_warehouse_counts_and_current_token_status(monkeypatch):
    trade_date = date(2026, 5, 6)
    db = _FakeDb([
        _ExecuteResult(scalars=[date(2026, 5, 12), date(2026, 5, 19)]),
        _ExecuteResult(scalars=[("2026-05-12", "CE", 22500), ("2026-05-12", "PE", 22500)]),
        _ExecuteResult(scalar=376),
        _ExecuteResult(scalar=375),
        _ExecuteResult(scalar=100),
        _ExecuteResult(scalar=12000),
    ])

    counts = await live_data_sync._warehouse_counts(db, trade_date)
    assert counts["option_contracts"] == 2
    assert counts["expiries"] == ["2026-05-12", "2026-05-19"]
    assert counts["options_rows"] == 12000

    async def latest_token(_db):
        return SimpleNamespace(token_date=trade_date, encrypted_token="encrypted")

    monkeypatch.setattr(live_data_sync, "_latest_zerodha_token", latest_token)
    monkeypatch.setattr(live_data_sync, "decrypt_token", lambda token: "access-token")
    assert await live_data_sync.current_system_token_status(_FakeDb(), trade_date) == live_data_sync.TOKEN_VALID


@pytest.mark.asyncio
async def test_run_daily_live_data_sync_records_ingestion_exception(monkeypatch):
    trade_date = date(2026, 5, 6)

    async def latest_token(_db):
        return SimpleNamespace(token_date=trade_date, encrypted_token="ok")

    async def to_thread(func, *args):
        return func(*args)

    async def ingest(*_args, **_kwargs):
        raise RuntimeError("warehouse unavailable")

    monkeypatch.setattr(live_data_sync, "_latest_zerodha_token", latest_token)
    monkeypatch.setattr(live_data_sync, "decrypt_token", lambda _token: "access-token")
    monkeypatch.setattr(live_data_sync, "validate_access_token_with_token", lambda _token: True)
    monkeypatch.setattr(live_data_sync.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(live_data_sync, "ingest_live_day", ingest)

    db = _FakeDb()
    run = await live_data_sync.run_daily_live_data_sync(db, trade_date)

    assert run.status == live_data_sync.STATUS_FAILED
    assert run.error_message == "warehouse unavailable"
    assert db.rollbacks == 1
    assert db.merged == [run]

from datetime import date, datetime

import pytest

import app.services.live_ingestion as live_ingestion
from app.models.historical import TradingDay
from app.services.zerodha_client import DataUnavailableError


class _Result:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDb:
    def __init__(self, trading_day=None):
        self.trading_day = trading_day
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.executed = []

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, TradingDay):
            self.trading_day = obj

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        if "SELECT trading_days" in str(stmt):
            return _Result(self.trading_day)
        return _Result()


def _records(close=22500.0):
    return [{
        "date": datetime(2026, 5, 6, 9, 15),
        "open": close,
        "high": close + 10,
        "low": close - 10,
        "close": close,
        "volume": 100,
        "oi": 1000,
    }]


@pytest.mark.asyncio
async def test_ingest_live_day_fetches_missing_market_segments(monkeypatch):
    trade_date = date(2026, 5, 6)
    tokens_seen = []

    async def fake_to_thread(func, *args):
        return func(*args)

    async def fake_sleep(_delay):
        return None

    async def existing_count(_db, table, _trade_date):
        return 2 if table == "options_candles" else 0

    async def existing_options(_db, _trade_date):
        return set()

    async def existing_spot_median(_db, _trade_date):
        return None

    async def bulk_insert(_db, df, _table, _chunk, _conflict_cols):
        return len(df)

    def fetch(token, _trade_date, _access_token):
        tokens_seen.append(token)
        return _records(22500.0 if token in (live_ingestion.UNDERLYING_TOKENS["NIFTY"], live_ingestion.VIX_TOKEN) else 100.0)

    def instruments(_access_token, exchange="NFO"):
        assert exchange == "NFO"
        return [
            {"name": "NIFTY", "instrument_type": "FUT", "instrument_token": 11, "expiry": date(2026, 5, 28), "strike": 0},
            {"name": "NIFTY", "instrument_type": "CE", "instrument_token": 21, "expiry": date(2026, 5, 12), "strike": 22500},
            {"name": "NIFTY", "instrument_type": "PE", "instrument_token": 22, "expiry": date(2026, 5, 12), "strike": 22500},
        ]

    monkeypatch.setattr(live_ingestion.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(live_ingestion.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(live_ingestion, "_existing_row_count", existing_count)
    monkeypatch.setattr(live_ingestion, "_existing_option_contract_keys", existing_options)
    monkeypatch.setattr(live_ingestion, "_existing_spot_median", lambda _db, _trade_date: None)
    monkeypatch.setattr(live_ingestion, "_bulk_insert", bulk_insert)
    monkeypatch.setattr(live_ingestion, "fetch_candles_with_token", fetch)
    monkeypatch.setattr(live_ingestion, "get_instruments_with_token", instruments)

    db = _FakeDb()
    summary = await live_ingestion.ingest_live_day(db, "token", trade_date)

    assert summary["status"] == "completed"
    assert summary["spot_rows"] == 1
    assert summary["vix_rows"] == 1
    assert summary["futures_rows"] == 1
    assert summary["options_rows"] == 2
    assert summary["option_contracts"] == 2
    assert summary["futures_expiry"] == "2026-05-28"
    assert summary["expiries"] == ["2026-05-12"]
    assert summary["failed_items"] == []
    assert {21, 22}.issubset(set(tokens_seen))
    assert db.trading_day.backtest_ready is True
    assert db.trading_day.options_available is True


@pytest.mark.asyncio
async def test_ingest_live_day_records_partial_failures_and_wide_option_range(monkeypatch):
    trade_date = date(2026, 5, 6)

    async def fake_to_thread(func, *args):
        return func(*args)

    async def fake_sleep(_delay):
        return None

    async def existing_count(_db, table, _trade_date):
        return 1 if table == "options_candles" else 0

    async def existing_options(_db, _trade_date):
        return set()

    async def existing_spot_median(_db, _trade_date):
        return None

    async def bulk_insert(_db, df, _table, _chunk, _conflict_cols):
        return len(df)

    def fetch(token, _trade_date, _access_token):
        if token == live_ingestion.UNDERLYING_TOKENS["NIFTY"]:
            raise DataUnavailableError("spot closed")
        if token == live_ingestion.VIX_TOKEN:
            return _records(12.5)
        raise DataUnavailableError("contract missing")

    def instruments(_access_token, exchange="NFO"):
        return [
            {"name": "NIFTY", "instrument_type": "FUT", "instrument_token": 11, "expiry": date(2026, 5, 28), "strike": 0},
            {"name": "NIFTY", "instrument_type": "CE", "instrument_token": 21, "expiry": date(2026, 5, 12), "strike": 22500},
        ]

    monkeypatch.setattr(live_ingestion.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(live_ingestion.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(live_ingestion, "_existing_row_count", existing_count)
    monkeypatch.setattr(live_ingestion, "_existing_option_contract_keys", existing_options)
    monkeypatch.setattr(live_ingestion, "_existing_spot_median", existing_spot_median)
    monkeypatch.setattr(live_ingestion, "_bulk_insert", bulk_insert)
    monkeypatch.setattr(live_ingestion, "fetch_candles_with_token", fetch)
    monkeypatch.setattr(live_ingestion, "get_instruments_with_token", instruments)

    summary = await live_ingestion.ingest_live_day(_FakeDb(), "token", trade_date)

    assert summary["status"] == "failed"
    assert "spot" in summary["failed_items"]
    assert "options_partial" in summary["failed_items"]
    assert "no spot data" in summary["notes"]

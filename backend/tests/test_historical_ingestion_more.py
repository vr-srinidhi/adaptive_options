from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import app.services.historical_ingestion as ingestion
from app.models.historical import TradingDay


class _Result:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = list(rows or [])

    def scalar_one_or_none(self):
        return self._scalar

    def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self, first_scalar=None, fetch_rows=None):
        self.first_scalar = first_scalar
        self.fetch_rows = list(fetch_rows or [])
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.executed = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        if "SELECT trading_days.trade_date" in str(stmt):
            return _Result(rows=self.fetch_rows)
        if self.first_scalar is not Ellipsis:
            scalar = self.first_scalar
            self.first_scalar = Ellipsis
            return _Result(scalar=scalar)
        return _Result()


def _write_inputs(root: Path, trade_date: date):
    for folder in ("spot", "vix", "futures", "options"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    stamp = f"{trade_date} 09:15:00"
    (root / "spot" / f"NIFTY_{trade_date}.csv").write_text(
        "timestamp,symbol,open,high,low,close\n"
        f"{stamp},NIFTY,22500,22510,22490,22505\n"
    )
    (root / "vix" / f"INDIA_VIX_{trade_date}.csv").write_text(
        "timestamp,symbol,open,high,low,close\n"
        f"{stamp},INDIA VIX,12,13,11,12.5\n"
    )
    (root / "futures" / f"NIFTY_FUT_{trade_date}.csv").write_text(
        "timestamp,symbol,expiry_date,open,high,low,close\n"
        f"{stamp},NIFTY26MAYFUT,2026-05-28,22510,22520,22500,22515\n"
    )
    (root / "options" / f"NIFTY_OPTIONS_{trade_date}.csv").write_text(
        "timestamp,symbol,expiry_date,option_type,strike,open,high,low,close\n"
        f"{stamp},NIFTY26MAY22500CE,2026-05-28,CE,22500,100,110,90,105\n"
    )


def test_csv_readers_fill_optional_columns_and_preserve_source(tmp_path, monkeypatch):
    trade_date = date(2026, 5, 6)
    _write_inputs(tmp_path, trade_date)
    monkeypatch.setattr(ingestion, "DATA_SOURCE_PATH", tmp_path)

    assert ingestion.available_trading_dates() == [trade_date]
    spot = ingestion._read_spot_csv(ingestion._spot_path(trade_date), trade_date)
    futures = ingestion._read_futures_csv(ingestion._futures_path(trade_date), trade_date)
    options = ingestion._read_options_csv(ingestion._options_path(trade_date), trade_date)
    vix = ingestion._read_vix_csv(ingestion._vix_path(trade_date), trade_date)

    assert spot.iloc[0]["volume"] == 0
    assert futures.iloc[0]["open_interest"] == 0
    assert pd.isna(options.iloc[0]["ltp"])
    assert vix.iloc[0]["source_file"] == f"INDIA_VIX_{trade_date}.csv"


@pytest.mark.asyncio
async def test_bulk_insert_chunks_and_normalizes_missing_values():
    df = pd.DataFrame([
        {"trade_date": date(2026, 5, 6), "value": 1.0},
        {"trade_date": date(2026, 5, 6), "value": float("nan")},
        {"trade_date": date(2026, 5, 6), "value": 3.0},
    ])
    db = _FakeDb()

    inserted = await ingestion._bulk_insert(db, df, "spot_candles", 2, [])

    assert inserted == 3
    assert len(db.executed) == 2
    assert "ON CONFLICT ON CONSTRAINT uq_spot_candles_date_sym_ts" in db.executed[0][0]
    assert db.executed[0][1][1]["value"] is None


@pytest.mark.asyncio
async def test_ingest_day_reads_all_files_and_sets_backtest_ready(tmp_path, monkeypatch):
    trade_date = date(2026, 5, 6)
    _write_inputs(tmp_path, trade_date)
    monkeypatch.setattr(ingestion, "DATA_SOURCE_PATH", tmp_path)
    db = _FakeDb(first_scalar=None)

    summary = await ingestion.ingest_day(db, trade_date)

    assert summary == {
        "trade_date": "2026-05-06",
        "status": "completed",
        "notes": None,
        "spot_rows": 1,
        "vix_rows": 1,
        "futures_rows": 1,
        "options_rows": 1,
    }
    td = db.added[0]
    assert isinstance(td, TradingDay)
    assert td.backtest_ready is True
    assert td.spot_file_name == "NIFTY_2026-05-06.csv"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_ingest_day_skips_completed_without_force():
    td = TradingDay(trade_date=date(2026, 5, 6), ingestion_status="completed")
    db = _FakeDb(first_scalar=td)

    summary = await ingestion.ingest_day(db, date(2026, 5, 6), force=False)

    assert summary["status"] == "skipped"
    assert "Already ingested" in summary["notes"]
    assert db.commits == 0


@pytest.mark.asyncio
async def test_ingest_bulk_records_failed_days(monkeypatch):
    async def fake_ingest(_db, trade_date, force=False):
        if trade_date.day == 7:
            raise RuntimeError("broken csv")
        return {"trade_date": str(trade_date), "status": "completed"}

    monkeypatch.setattr(ingestion, "ingest_day", fake_ingest)

    results = await ingestion.ingest_bulk(object(), [date(2026, 5, 7), date(2026, 5, 6)])

    assert [row["trade_date"] for row in results] == ["2026-05-06", "2026-05-07"]
    assert results[1]["status"] == "failed"
    assert results[1]["notes"] == "broken csv"


@pytest.mark.asyncio
async def test_sync_catalogue_adds_only_new_dates(tmp_path, monkeypatch):
    trade_date = date(2026, 5, 6)
    _write_inputs(tmp_path, trade_date)
    _write_inputs(tmp_path, date(2026, 5, 7))
    monkeypatch.setattr(ingestion, "DATA_SOURCE_PATH", tmp_path)
    db = _FakeDb(fetch_rows=[(date(2026, 5, 6),)])

    added = await ingestion.sync_catalogue(db)

    assert added == 1
    assert db.added[0].trade_date == date(2026, 5, 7)
    assert db.added[0].options_available is True
    assert db.commits == 1

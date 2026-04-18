"""
Historical data ingestion service.

Reads CSV files from DATA_SOURCE_PATH and bulk-inserts into the warehouse tables:
  spot_candles, vix_candles, futures_candles, options_candles

Also maintains the trading_days catalogue (availability flags + ingestion status).

Directory layout expected under DATA_SOURCE_PATH:
  spot/     NIFTY_<YYYY-MM-DD>.csv
  vix/      INDIA_VIX_<YYYY-MM-DD>.csv
  futures/  NIFTY_FUT_<YYYY-MM-DD>.csv          (available from 2026-01-28)
  options/  NIFTY_OPTIONS_<YYYY-MM-DD>.csv
"""
import logging
import os
from datetime import date as date_type
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.historical import TradingDay

log = logging.getLogger(__name__)

DATA_SOURCE_PATH = Path(os.getenv("DATA_SOURCE_PATH", "/data/historical"))

# Chunk size for options bulk inserts (largest table)
OPTIONS_CHUNK = 10_000
SPOT_CHUNK = 5_000


# ── Path helpers ──────────────────────────────────────────────────────────────

def _spot_path(trade_date: date_type) -> Optional[Path]:
    p = DATA_SOURCE_PATH / "spot" / f"NIFTY_{trade_date}.csv"
    return p if p.exists() else None


def _vix_path(trade_date: date_type) -> Optional[Path]:
    p = DATA_SOURCE_PATH / "vix" / f"INDIA_VIX_{trade_date}.csv"
    return p if p.exists() else None


def _futures_path(trade_date: date_type) -> Optional[Path]:
    p = DATA_SOURCE_PATH / "futures" / f"NIFTY_FUT_{trade_date}.csv"
    return p if p.exists() else None


def _options_path(trade_date: date_type) -> Optional[Path]:
    p = DATA_SOURCE_PATH / "options" / f"NIFTY_OPTIONS_{trade_date}.csv"
    return p if p.exists() else None


def available_trading_dates() -> List[date_type]:
    """Return sorted list of dates for which at least a spot CSV exists."""
    spot_dir = DATA_SOURCE_PATH / "spot"
    if not spot_dir.exists():
        return []
    dates = []
    for f in spot_dir.glob("NIFTY_*.csv"):
        try:
            d = date_type.fromisoformat(f.stem.replace("NIFTY_", ""))
            dates.append(d)
        except ValueError:
            pass
    return sorted(dates)


# ── Low-level CSV readers ─────────────────────────────────────────────────────

def _read_spot_csv(path: Path, trade_date: date_type) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["trade_date"] = trade_date
    df["source_file"] = path.name
    # Ensure volume is present (some files have 0 volume as int)
    if "volume" not in df.columns:
        df["volume"] = 0
    return df[["trade_date", "timestamp", "symbol", "open", "high", "low", "close", "volume", "source_file"]]


def _read_vix_csv(path: Path, trade_date: date_type) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["trade_date"] = trade_date
    df["source_file"] = path.name
    return df[["trade_date", "timestamp", "symbol", "open", "high", "low", "close", "source_file"]]


def _read_futures_csv(path: Path, trade_date: date_type) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp", "expiry_date"])
    df["trade_date"] = trade_date
    df["source_file"] = path.name
    if "volume" not in df.columns:
        df["volume"] = 0
    if "open_interest" not in df.columns:
        df["open_interest"] = 0
    return df[["trade_date", "timestamp", "symbol", "expiry_date", "open", "high", "low", "close", "volume", "open_interest", "source_file"]]


def _read_options_csv(path: Path, trade_date: date_type) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        parse_dates=["timestamp", "expiry_date"],
        dtype={"strike": "Int64", "volume": "Int64", "open_interest": "Int64"},
    )
    df["trade_date"] = trade_date
    df["source_file"] = path.name
    if "ltp" not in df.columns:
        df["ltp"] = None
    if "volume" not in df.columns:
        df["volume"] = 0
    if "open_interest" not in df.columns:
        df["open_interest"] = 0
    return df[["trade_date", "timestamp", "symbol", "expiry_date", "option_type", "strike", "open", "high", "low", "close", "volume", "open_interest", "ltp", "source_file"]]


# ── Bulk insert helpers ───────────────────────────────────────────────────────

# Natural unique constraint names per table — must match models/historical.py
_CONFLICT_CONSTRAINT = {
    "spot_candles":     "uq_spot_candles_date_sym_ts",
    "vix_candles":      "uq_vix_candles_date_sym_ts",
    "futures_candles":  "uq_futures_candles_date_sym_exp_ts",
    "options_candles":  "uq_options_candles_natural",
}


async def _bulk_insert(
    session: AsyncSession,
    df: pd.DataFrame,
    table: str,
    chunk: int,
    conflict_cols: List[str],  # kept for API compat; constraint name is looked up internally
) -> int:
    """
    Insert df rows into table using INSERT ... ON CONFLICT ON CONSTRAINT ... DO NOTHING.
    Returns number of rows inserted.
    """
    if df.empty:
        return 0

    constraint = _CONFLICT_CONSTRAINT.get(table)
    conflict_clause = (
        f"ON CONFLICT ON CONSTRAINT {constraint} DO NOTHING"
        if constraint
        else "ON CONFLICT DO NOTHING"
    )

    cols = list(df.columns)
    col_names = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    stmt = text(
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) {conflict_clause}"
    )

    total = 0
    for start in range(0, len(df), chunk):
        chunk_df = df.iloc[start : start + chunk]
        records = chunk_df.where(pd.notna(chunk_df), None).to_dict("records")
        # Normalise pandas NA / NaT → None so asyncpg is happy
        cleaned = []
        for row in records:
            cleaned.append({k: (None if pd.isna(v) else v) for k, v in row.items()})
        await session.execute(stmt, cleaned)
        total += len(chunk_df)
    return total


# ── Per-day ingestion ─────────────────────────────────────────────────────────

async def ingest_day(
    db: AsyncSession,
    trade_date: date_type,
    force: bool = False,
) -> dict:
    """
    Ingest all available CSV files for a single trading day.

    Returns a summary dict with keys:
      trade_date, spot_rows, vix_rows, futures_rows, options_rows, status, notes
    """
    from sqlalchemy import select

    # ── Load or create TradingDay record ─────────────────────────────────────
    result = await db.execute(
        select(TradingDay).where(TradingDay.trade_date == trade_date)
    )
    td: Optional[TradingDay] = result.scalar_one_or_none()

    if td is None:
        td = TradingDay(trade_date=trade_date, ingestion_status="pending")
        db.add(td)
        await db.flush()

    if td.ingestion_status == "completed" and not force:
        return {
            "trade_date": str(trade_date),
            "status": "skipped",
            "notes": "Already ingested (use force=True to re-ingest)",
            "spot_rows": 0, "vix_rows": 0, "futures_rows": 0, "options_rows": 0,
        }

    td.ingestion_status = "in_progress"
    await db.flush()

    notes = []
    spot_rows = vix_rows = futures_rows = options_rows = 0

    try:
        # ── Spot ─────────────────────────────────────────────────────────────
        spot_p = _spot_path(trade_date)
        if spot_p:
            td.spot_available = True
            td.spot_file_name = spot_p.name
            df = _read_spot_csv(spot_p, trade_date)
            # Delete existing rows for idempotency on force re-ingest
            if force:
                await db.execute(
                    text("DELETE FROM spot_candles WHERE trade_date = :d"),
                    {"d": trade_date},
                )
            spot_rows = await _bulk_insert(db, df, "spot_candles", SPOT_CHUNK, [])
            td.spot_row_count = spot_rows
            log.info("spot %s: %d rows", trade_date, spot_rows)
        else:
            notes.append("spot CSV missing")

        # ── VIX ──────────────────────────────────────────────────────────────
        vix_p = _vix_path(trade_date)
        if vix_p:
            td.vix_available = True
            td.vix_file_name = vix_p.name
            df = _read_vix_csv(vix_p, trade_date)
            if force:
                await db.execute(
                    text("DELETE FROM vix_candles WHERE trade_date = :d"),
                    {"d": trade_date},
                )
            vix_rows = await _bulk_insert(db, df, "vix_candles", SPOT_CHUNK, [])
            log.info("vix %s: %d rows", trade_date, vix_rows)
        else:
            notes.append("vix CSV missing")

        # ── Futures ───────────────────────────────────────────────────────────
        fut_p = _futures_path(trade_date)
        if fut_p:
            td.futures_available = True
            td.futures_file_name = fut_p.name
            df = _read_futures_csv(fut_p, trade_date)
            if force:
                await db.execute(
                    text("DELETE FROM futures_candles WHERE trade_date = :d"),
                    {"d": trade_date},
                )
            futures_rows = await _bulk_insert(db, df, "futures_candles", SPOT_CHUNK, [])
            log.info("futures %s: %d rows", trade_date, futures_rows)
        else:
            notes.append("futures CSV missing (expected for dates before 2026-01-28)")

        # ── Options ───────────────────────────────────────────────────────────
        opt_p = _options_path(trade_date)
        if opt_p:
            td.options_available = True
            td.options_file_name = opt_p.name
            df = _read_options_csv(opt_p, trade_date)
            if force:
                await db.execute(
                    text("DELETE FROM options_candles WHERE trade_date = :d"),
                    {"d": trade_date},
                )
            options_rows = await _bulk_insert(db, df, "options_candles", OPTIONS_CHUNK, [])
            td.options_row_count = options_rows
            log.info("options %s: %d rows", trade_date, options_rows)
        else:
            notes.append("options CSV missing")

        # ── Mark readiness ────────────────────────────────────────────────────
        # A day is backtest-ready when spot + options are present
        td.backtest_ready = td.spot_available and td.options_available
        td.ingestion_status = (
            "completed" if not notes else "completed_with_warnings"
        )
        td.ingestion_notes = "; ".join(notes) if notes else None

    except Exception as exc:
        td.ingestion_status = "failed"
        td.ingestion_notes = str(exc)
        await db.commit()
        log.exception("Ingestion failed for %s: %s", trade_date, exc)
        raise

    await db.commit()

    return {
        "trade_date": str(trade_date),
        "status": td.ingestion_status,
        "notes": td.ingestion_notes,
        "spot_rows": spot_rows,
        "vix_rows": vix_rows,
        "futures_rows": futures_rows,
        "options_rows": options_rows,
    }


async def ingest_bulk(
    db: AsyncSession,
    dates: Optional[List[date_type]] = None,
    force: bool = False,
) -> List[dict]:
    """
    Ingest multiple days. If dates is None, scans DATA_SOURCE_PATH for all
    available spot CSVs and ingests all of them.

    Returns list of per-day summary dicts.
    """
    if dates is None:
        dates = available_trading_dates()
        log.info("Bulk ingest: discovered %d dates", len(dates))

    results = []
    for d in sorted(dates):
        try:
            summary = await ingest_day(db, d, force=force)
        except Exception as exc:
            summary = {
                "trade_date": str(d),
                "status": "failed",
                "notes": str(exc),
                "spot_rows": 0, "vix_rows": 0, "futures_rows": 0, "options_rows": 0,
            }
        results.append(summary)

    return results


# ── Trading-day catalogue helpers ─────────────────────────────────────────────

async def sync_catalogue(db: AsyncSession) -> int:
    """
    Scan DATA_SOURCE_PATH and ensure a TradingDay row exists for every date
    that has at least a spot file. Does NOT ingest data — just registers
    availability flags so the UI can show what's on disk vs. what's in DB.

    Returns the number of new rows created.
    """
    from sqlalchemy import select

    all_dates = available_trading_dates()
    result = await db.execute(select(TradingDay.trade_date))
    known = {row[0] for row in result.fetchall()}

    new_count = 0
    for d in all_dates:
        if d in known:
            continue
        td = TradingDay(
            trade_date=d,
            spot_available=_spot_path(d) is not None,
            vix_available=_vix_path(d) is not None,
            futures_available=_futures_path(d) is not None,
            options_available=_options_path(d) is not None,
            spot_file_name=(p := _spot_path(d)) and p.name,
            vix_file_name=(p := _vix_path(d)) and p.name,
            futures_file_name=(p := _futures_path(d)) and p.name,
            options_file_name=(p := _options_path(d)) and p.name,
            ingestion_status="pending",
        )
        db.add(td)
        new_count += 1

    if new_count:
        await db.commit()
    log.info("sync_catalogue: added %d new trading_days rows", new_count)
    return new_count

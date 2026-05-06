"""
Live-day ingestion from Zerodha.

Fetches today's (or any given) 1-min candle data directly from the Zerodha
historical data API and inserts it into the warehouse tables so that a
single_session_backtest can be run without a CSV file.

Fetches:
  - NIFTY spot              → spot_candles
  - India VIX               → vix_candles
  - NIFTY near-month futures → futures_candles
  - NIFTY options (3 expiries, ATM ± STRIKE_WINDOW_STEPS) → options_candles

Updates trading_days with backtest_ready=True when spot + options are present.
"""
import asyncio
import logging
from calendar import monthrange
from datetime import date as date_type, timedelta
from typing import List, Optional, Tuple

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.historical import TradingDay
from app.services.historical_ingestion import OPTIONS_CHUNK, SPOT_CHUNK, _bulk_insert
from app.services.option_resolver import UNDERLYING_TOKENS, nearest_weekly_expiry
from app.services.zerodha_client import (
    DataUnavailableError,
    fetch_candles_with_token,
    get_instruments_with_token,
)

log = logging.getLogger(__name__)

# Zerodha NSE instrument token for India VIX (stable constant)
VIX_TOKEN: int = 264969

# Strike steps either side of ATM to fetch (40 × 50 = ±2000 from ATM)
STRIKE_WINDOW_STEPS: int = 40

# Pause between API calls (Zerodha allows 3 historical data req/s)
_API_CALL_DELAY: float = 0.38

# Batch size for intermediate DB flushes during options ingestion
_OPTIONS_BATCH: int = 100


# ── Expiry helpers ────────────────────────────────────────────────────────────

def _last_thursday_of_month(d: date_type) -> date_type:
    """Return the last Thursday in d's calendar month."""
    last = date_type(d.year, d.month, monthrange(d.year, d.month)[1])
    while last.weekday() != 3:  # 3 = Thursday
        last -= timedelta(days=1)
    return last


def get_target_expiries(trade_date: date_type) -> List[date_type]:
    """
    Return up to 3 distinct NIFTY expiry dates for trade_date:
    [this_week, next_week, monthly_expiry].

    Monthly expiry is the last Thursday of the month.
    If it coincides with a weekly already in the list it is de-duplicated.
    If trade_date is past this month's last Thursday, next month is used.
    """
    this_week = nearest_weekly_expiry("NIFTY", trade_date)
    next_week = this_week + timedelta(days=7)

    monthly = _last_thursday_of_month(trade_date)
    if monthly < trade_date:
        if trade_date.month == 12:
            monthly = _last_thursday_of_month(date_type(trade_date.year + 1, 1, 1))
        else:
            monthly = _last_thursday_of_month(date_type(trade_date.year, trade_date.month + 1, 1))

    seen: set = set()
    result: List[date_type] = []
    for exp in (this_week, next_week, monthly):
        if exp not in seen:
            seen.add(exp)
            result.append(exp)
    return result


# ── Record → DataFrame helpers ────────────────────────────────────────────────

def _strip_tz(series: "pd.Series") -> "pd.Series":
    """Strip timezone from a tz-aware datetime series, keeping the IST wall-clock time."""
    return series.apply(lambda ts: ts.replace(tzinfo=None) if pd.notna(ts) else ts)


def _to_spot_df(records: list, trade_date: date_type, symbol: str) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df.rename(columns={"date": "timestamp"}, inplace=True)
    df["timestamp"]   = _strip_tz(df["timestamp"])
    df["trade_date"]  = trade_date
    df["symbol"]      = symbol
    df["source_file"] = "zerodha_live"
    if "volume" not in df.columns:
        df["volume"] = 0
    return df[["trade_date", "timestamp", "symbol", "open", "high", "low", "close", "volume", "source_file"]]


def _to_vix_df(records: list, trade_date: date_type) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df.rename(columns={"date": "timestamp"}, inplace=True)
    df["timestamp"]   = _strip_tz(df["timestamp"])
    df["trade_date"]  = trade_date
    df["symbol"]      = "INDIA VIX"
    df["source_file"] = "zerodha_live"
    return df[["trade_date", "timestamp", "symbol", "open", "high", "low", "close", "source_file"]]


def _to_futures_df(records: list, trade_date: date_type, expiry: date_type) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df.rename(columns={"date": "timestamp", "oi": "open_interest"}, inplace=True)
    df["timestamp"]     = _strip_tz(df["timestamp"])
    df["trade_date"]    = trade_date
    df["symbol"]        = "NIFTY"
    df["expiry_date"]   = expiry
    df["source_file"]   = "zerodha_live"
    if "volume" not in df.columns:
        df["volume"] = 0
    if "open_interest" not in df.columns:
        df["open_interest"] = 0
    return df[[
        "trade_date", "timestamp", "symbol", "expiry_date",
        "open", "high", "low", "close", "volume", "open_interest", "source_file",
    ]]


def _to_option_df(
    records: list,
    trade_date: date_type,
    expiry: date_type,
    option_type: str,
    strike: int,
) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df.rename(columns={"date": "timestamp", "oi": "open_interest"}, inplace=True)
    df["timestamp"]     = _strip_tz(df["timestamp"])
    df["trade_date"]    = trade_date
    df["symbol"]        = "NIFTY"
    df["expiry_date"]   = expiry
    df["option_type"]   = option_type
    df["strike"]        = strike
    df["ltp"]           = df["close"]
    df["source_file"]   = "zerodha_live"
    if "volume" not in df.columns:
        df["volume"] = 0
    if "open_interest" not in df.columns:
        df["open_interest"] = 0
    return df[[
        "trade_date", "timestamp", "symbol", "expiry_date", "option_type", "strike",
        "open", "high", "low", "close", "volume", "open_interest", "ltp", "source_file",
    ]]


def _instrument_expiry_date(inst: dict) -> Optional[date_type]:
    expiry = inst.get("expiry")
    if expiry is None:
        return None
    return expiry.date() if hasattr(expiry, "date") else expiry


def _select_nearest_nifty_future(instruments: list, trade_date: date_type) -> Optional[dict]:
    futures = []
    for inst in instruments:
        expiry = _instrument_expiry_date(inst)
        if (
            inst.get("name") == "NIFTY"
            and inst.get("instrument_type") == "FUT"
            and expiry is not None
            and expiry >= trade_date
        ):
            futures.append((expiry, inst))
    if not futures:
        return None
    return sorted(futures, key=lambda item: item[0])[0][1]


async def _existing_row_count(db: AsyncSession, table: str, trade_date: date_type) -> int:
    return int((await db.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE trade_date = :d"),
        {"d": trade_date},
    )).scalar_one() or 0)


async def _existing_option_contract_keys(
    db: AsyncSession,
    trade_date: date_type,
) -> set[Tuple[date_type, str, int]]:
    rows = (await db.execute(text("""
        SELECT DISTINCT expiry_date, option_type, strike
        FROM options_candles
        WHERE trade_date = :d
    """), {"d": trade_date})).all()
    return {(row[0], row[1], int(row[2])) for row in rows}


def _missing_option_contracts(
    contracts: List[Tuple[int, date_type, str, int]],
    existing_keys: set[Tuple[date_type, str, int]],
) -> List[Tuple[int, date_type, str, int]]:
    return [
        contract for contract in contracts
        if (contract[1], contract[2], contract[3]) not in existing_keys
    ]


async def _existing_spot_median(db: AsyncSession, trade_date: date_type) -> Optional[float]:
    rows = (await db.execute(text("""
        SELECT close
        FROM spot_candles
        WHERE trade_date = :d AND symbol = 'NIFTY' AND close IS NOT NULL
    """), {"d": trade_date})).scalars().all()
    if not rows:
        return None
    values = sorted(float(row) for row in rows)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


# ── Main function ─────────────────────────────────────────────────────────────

async def ingest_live_day(
    db: AsyncSession,
    access_token: str,
    trade_date: date_type,
    force: bool = False,
) -> dict:
    """
    Pull live candles from Zerodha for trade_date and insert into warehouse.

    Returns a summary dict with status and row counts.
    """
    # ── Load or create TradingDay record ─────────────────────────────────────
    td: Optional[TradingDay] = (await db.execute(
        select(TradingDay).where(TradingDay.trade_date == trade_date)
    )).scalar_one_or_none()

    if td is None:
        td = TradingDay(trade_date=trade_date, ingestion_status="pending")
        db.add(td)
        await db.flush()

    td.ingestion_status = "in_progress"
    await db.flush()
    await db.commit()

    notes: List[str] = []
    failed_items: List[str] = []
    spot_rows = vix_rows = futures_rows = options_rows = 0
    futures_expiry: Optional[date_type] = None
    expiries: List[date_type] = []
    contracts: List[Tuple[int, date_type, str, int]] = []
    df_spot: Optional[pd.DataFrame] = None

    try:
        # ── Spot ─────────────────────────────────────────────────────────────
        log.info("live_ingest %s: fetching NIFTY spot (token=%d)", trade_date, UNDERLYING_TOKENS["NIFTY"])
        try:
            if force:
                await db.execute(text("DELETE FROM spot_candles WHERE trade_date = :d"), {"d": trade_date})
            existing_spot_rows = 0 if force else await _existing_row_count(db, "spot_candles", trade_date)
            if existing_spot_rows > 0:
                spot_rows = existing_spot_rows
                td.spot_available = True
                td.spot_row_count = spot_rows
                td.spot_file_name = td.spot_file_name or "zerodha_live"
                log.info("live_ingest %s: spot already present (%d rows)", trade_date, spot_rows)
            else:
                records = await asyncio.to_thread(
                    fetch_candles_with_token,
                    UNDERLYING_TOKENS["NIFTY"], trade_date, access_token,
                )
                df_spot = _to_spot_df(records, trade_date, "NIFTY")
                spot_rows = await _bulk_insert(db, df_spot, "spot_candles", SPOT_CHUNK, [])
                td.spot_available = True
                td.spot_row_count = spot_rows
                td.spot_file_name = "zerodha_live"
                log.info("live_ingest %s: spot %d rows", trade_date, spot_rows)
            await db.commit()
        except DataUnavailableError as exc:
            notes.append(f"spot unavailable: {exc}")
            failed_items.append("spot")
            log.warning("live_ingest %s: spot failed: %s", trade_date, exc)
        await asyncio.sleep(_API_CALL_DELAY)

        # ── VIX ──────────────────────────────────────────────────────────────
        log.info("live_ingest %s: fetching India VIX (token=%d)", trade_date, VIX_TOKEN)
        try:
            if force:
                await db.execute(text("DELETE FROM vix_candles WHERE trade_date = :d"), {"d": trade_date})
            existing_vix_rows = 0 if force else await _existing_row_count(db, "vix_candles", trade_date)
            if existing_vix_rows > 0:
                vix_rows = existing_vix_rows
                td.vix_available = True
                td.vix_file_name = td.vix_file_name or "zerodha_live"
                log.info("live_ingest %s: vix already present (%d rows)", trade_date, vix_rows)
            else:
                records = await asyncio.to_thread(
                    fetch_candles_with_token,
                    VIX_TOKEN, trade_date, access_token,
                )
                df_vix = _to_vix_df(records, trade_date)
                vix_rows = await _bulk_insert(db, df_vix, "vix_candles", SPOT_CHUNK, [])
                td.vix_available = True
                td.vix_file_name = "zerodha_live"
                log.info("live_ingest %s: vix %d rows", trade_date, vix_rows)
            await db.commit()
        except DataUnavailableError as exc:
            notes.append(f"vix unavailable: {exc}")
            failed_items.append("vix")
            log.warning("live_ingest %s: vix failed: %s", trade_date, exc)
        await asyncio.sleep(_API_CALL_DELAY)

        # Fetch NFO instrument master once and reuse it for futures + options.
        log.info("live_ingest %s: fetching NFO instrument master", trade_date)
        instruments = await asyncio.to_thread(get_instruments_with_token, access_token, "NFO")
        await asyncio.sleep(_API_CALL_DELAY)

        # ── Futures ──────────────────────────────────────────────────────────
        try:
            future = _select_nearest_nifty_future(instruments, trade_date)
            if not future:
                raise DataUnavailableError(f"No NIFTY future found on or after {trade_date}")
            futures_expiry = _instrument_expiry_date(future)
            log.info(
                "live_ingest %s: fetching NIFTY futures token=%s expiry=%s",
                trade_date, future.get("instrument_token"), futures_expiry,
            )
            if force:
                await db.execute(text("DELETE FROM futures_candles WHERE trade_date = :d"), {"d": trade_date})
            existing_futures_rows = 0 if force else await _existing_row_count(db, "futures_candles", trade_date)
            if existing_futures_rows > 0:
                futures_rows = existing_futures_rows
                td.futures_available = True
                td.futures_file_name = td.futures_file_name or "zerodha_live"
                log.info("live_ingest %s: futures already present (%d rows)", trade_date, futures_rows)
            else:
                records = await asyncio.to_thread(
                    fetch_candles_with_token,
                    int(future["instrument_token"]), trade_date, access_token,
                )
                df_futures = _to_futures_df(records, trade_date, futures_expiry)
                futures_rows = await _bulk_insert(db, df_futures, "futures_candles", SPOT_CHUNK, [])
                td.futures_available = True
                td.futures_file_name = "zerodha_live"
                log.info("live_ingest %s: futures %d rows", trade_date, futures_rows)
            await db.commit()
        except DataUnavailableError as exc:
            notes.append(f"futures unavailable: {exc}")
            failed_items.append("futures")
            log.warning("live_ingest %s: futures failed: %s", trade_date, exc)
        await asyncio.sleep(_API_CALL_DELAY)

        # ── Options ───────────────────────────────────────────────────────────
        # Determine strike range from spot data (fallback: broad range)
        existing_spot_median = None if df_spot is not None else await _existing_spot_median(db, trade_date)
        if df_spot is not None and not df_spot.empty:
            atm_spot  = float(df_spot["close"].median())
        elif existing_spot_median is not None:
            atm_spot = existing_spot_median
        else:
            atm_spot = None

        if atm_spot is not None:
            step      = 50   # NIFTY strike step
            atm       = int(round(atm_spot / step) * step)
            lo        = atm - STRIKE_WINDOW_STEPS * step
            hi        = atm + STRIKE_WINDOW_STEPS * step
            log.info("live_ingest %s: ATM=%d  range=[%d,%d]", trade_date, atm, lo, hi)
        else:
            lo, hi = 18_000, 28_000
            notes.append("no spot data — using wide strike range 18000–28000")
            log.warning("live_ingest %s: no spot data; using wide strike range", trade_date)

        # Derive target expiries from the instruments master itself — avoids
        # hardcoded weekday assumptions (NSE changed NIFTY expiry day to Tuesday).
        all_expiries = sorted({
            _instrument_expiry_date(i)
            for i in instruments
            if i.get("name") == "NIFTY"
            and i.get("instrument_type") in ("CE", "PE")
            and _instrument_expiry_date(i) is not None
            and _instrument_expiry_date(i) >= trade_date
        })
        expiries = all_expiries[:3]   # this week, next week, monthly
        expiry_set = set(expiries)
        log.info("live_ingest %s: target expiries=%s", trade_date, expiries)

        # Build list of (token, expiry, opt_type, strike) to fetch
        contracts: List[Tuple[int, date_type, str, int]] = []
        for inst in instruments:
            inst_expiry = inst.get("expiry")
            if hasattr(inst_expiry, "date"):
                inst_expiry = inst_expiry.date()
            inst_type = inst.get("instrument_type")
            inst_strike = inst.get("strike", -1)
            if (
                inst.get("name") == "NIFTY"
                and inst_type in ("CE", "PE")
                and inst_expiry in expiry_set
                and lo <= int(inst_strike) <= hi
            ):
                contracts.append((
                    int(inst["instrument_token"]),
                    inst_expiry,
                    inst_type,
                    int(inst_strike),
                ))

        total_option_contracts = len(contracts)
        if not force:
            existing_contracts = await _existing_option_contract_keys(db, trade_date)
            contracts = _missing_option_contracts(contracts, existing_contracts)

        log.info(
            "live_ingest %s: %d/%d option contracts to fetch",
            trade_date, len(contracts), total_option_contracts,
        )

        if force:
            await db.execute(text("DELETE FROM options_candles WHERE trade_date = :d"), {"d": trade_date})
            await db.flush()

        # Fetch candles for each contract, insert in batches
        pending_dfs: List[pd.DataFrame] = []
        failed_contracts = 0

        for i, (token, expiry, opt_type, strike) in enumerate(contracts):
            try:
                records = await asyncio.to_thread(
                    fetch_candles_with_token, token, trade_date, access_token
                )
                pending_dfs.append(_to_option_df(records, trade_date, expiry, opt_type, strike))
            except DataUnavailableError:
                failed_contracts += 1
                log.debug(
                    "live_ingest %s: no data for %s %s strike=%d",
                    trade_date, opt_type, expiry, strike,
                )

            await asyncio.sleep(_API_CALL_DELAY)

            # Flush every _OPTIONS_BATCH contracts to keep memory usage bounded
            if len(pending_dfs) >= _OPTIONS_BATCH:
                combined = pd.concat(pending_dfs, ignore_index=True)
                options_rows += await _bulk_insert(db, combined, "options_candles", OPTIONS_CHUNK, [])
                pending_dfs = []
                await db.commit()
                log.info(
                    "live_ingest %s: options progress %d/%d contracts",
                    trade_date, i + 1, len(contracts),
                )

        # Final flush
        if pending_dfs:
            combined = pd.concat(pending_dfs, ignore_index=True)
            options_rows += await _bulk_insert(db, combined, "options_candles", OPTIONS_CHUNK, [])

        if failed_contracts:
            notes.append(f"{failed_contracts}/{len(contracts)} fetched option contracts had no data")
            failed_items.append("options_partial")
        if total_option_contracts == 0:
            notes.append("no option contracts matched the target expiries/strike range")
            failed_items.append("options")

        total_options_rows = await _existing_row_count(db, "options_candles", trade_date)
        td.options_available = total_options_rows > 0
        td.options_row_count = total_options_rows
        td.options_file_name = td.options_file_name or "zerodha_live"
        log.info(
            "live_ingest %s: options %d new rows, %d total rows (%d contracts failed)",
            trade_date, options_rows, total_options_rows, failed_contracts,
        )

        # ── Mark readiness ────────────────────────────────────────────────────
        td.backtest_ready = td.spot_available and td.options_available
        if not td.backtest_ready:
            td.ingestion_status = "failed"
        else:
            td.ingestion_status = "completed" if not notes else "completed_with_warnings"
        td.ingestion_notes = "; ".join(notes) if notes else None

    except Exception as exc:
        td.ingestion_status = "failed"
        td.ingestion_notes  = str(exc)
        await db.commit()
        log.exception("live_ingest failed for %s: %s", trade_date, exc)
        raise

    await db.commit()

    return {
        "trade_date":       str(trade_date),
        "status":           td.ingestion_status,
        "notes":            td.ingestion_notes,
        "spot_rows":        spot_rows,
        "vix_rows":         vix_rows,
        "futures_rows":     futures_rows,
        "options_rows":     options_rows,
        "option_contracts": total_option_contracts if "total_option_contracts" in dir() else len(contracts),
        "futures_expiry":   str(futures_expiry) if futures_expiry else None,
        "expiries":         [str(expiry) for expiry in expiries],
        "failed_items":     failed_items,
    }

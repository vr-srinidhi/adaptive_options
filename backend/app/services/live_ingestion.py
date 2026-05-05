"""
Live-day ingestion from Zerodha.

Fetches today's (or any given) 1-min candle data directly from the Zerodha
historical data API and inserts it into the warehouse tables so that a
single_session_backtest can be run without a CSV file.

Fetches:
  - NIFTY spot              → spot_candles
  - India VIX               → vix_candles
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

    if td.ingestion_status in ("completed", "completed_with_warnings") and not force:
        return {
            "trade_date":       str(trade_date),
            "status":           "skipped",
            "notes":            "Already ingested (pass force=true to re-ingest)",
            "spot_rows":        0,
            "vix_rows":         0,
            "options_rows":     0,
            "option_contracts": 0,
        }

    td.ingestion_status = "in_progress"
    await db.flush()
    await db.commit()

    notes: List[str] = []
    spot_rows = vix_rows = options_rows = 0
    df_spot: Optional[pd.DataFrame] = None

    try:
        # ── Spot ─────────────────────────────────────────────────────────────
        log.info("live_ingest %s: fetching NIFTY spot (token=%d)", trade_date, UNDERLYING_TOKENS["NIFTY"])
        try:
            records = await asyncio.to_thread(
                fetch_candles_with_token,
                UNDERLYING_TOKENS["NIFTY"], trade_date, access_token,
            )
            df_spot = _to_spot_df(records, trade_date, "NIFTY")
            if force:
                await db.execute(text("DELETE FROM spot_candles WHERE trade_date = :d"), {"d": trade_date})
            spot_rows = await _bulk_insert(db, df_spot, "spot_candles", SPOT_CHUNK, [])
            td.spot_available = True
            td.spot_row_count = spot_rows
            td.spot_file_name = "zerodha_live"
            await db.commit()
            log.info("live_ingest %s: spot %d rows", trade_date, spot_rows)
        except DataUnavailableError as exc:
            notes.append(f"spot unavailable: {exc}")
            log.warning("live_ingest %s: spot failed: %s", trade_date, exc)
        await asyncio.sleep(_API_CALL_DELAY)

        # ── VIX ──────────────────────────────────────────────────────────────
        log.info("live_ingest %s: fetching India VIX (token=%d)", trade_date, VIX_TOKEN)
        try:
            records = await asyncio.to_thread(
                fetch_candles_with_token,
                VIX_TOKEN, trade_date, access_token,
            )
            df_vix = _to_vix_df(records, trade_date)
            if force:
                await db.execute(text("DELETE FROM vix_candles WHERE trade_date = :d"), {"d": trade_date})
            vix_rows = await _bulk_insert(db, df_vix, "vix_candles", SPOT_CHUNK, [])
            td.vix_available = True
            td.vix_file_name = "zerodha_live"
            await db.commit()
            log.info("live_ingest %s: vix %d rows", trade_date, vix_rows)
        except DataUnavailableError as exc:
            notes.append(f"vix unavailable: {exc}")
            log.warning("live_ingest %s: vix failed: %s", trade_date, exc)
        await asyncio.sleep(_API_CALL_DELAY)

        # ── Options ───────────────────────────────────────────────────────────
        # Determine strike range from spot data (fallback: broad range)
        if df_spot is not None and not df_spot.empty:
            atm_spot  = float(df_spot["close"].median())
            step      = 50   # NIFTY strike step
            atm       = int(round(atm_spot / step) * step)
            lo        = atm - STRIKE_WINDOW_STEPS * step
            hi        = atm + STRIKE_WINDOW_STEPS * step
            log.info("live_ingest %s: ATM=%d  range=[%d,%d]", trade_date, atm, lo, hi)
        else:
            lo, hi = 18_000, 28_000
            notes.append("no spot data — using wide strike range 18000–28000")
            log.warning("live_ingest %s: no spot data; using wide strike range", trade_date)

        # Fetch NFO instrument master
        log.info("live_ingest %s: fetching NFO instrument master", trade_date)
        instruments = await asyncio.to_thread(get_instruments_with_token, access_token, "NFO")
        await asyncio.sleep(_API_CALL_DELAY)

        # Derive target expiries from the instruments master itself — avoids
        # hardcoded weekday assumptions (NSE changed NIFTY expiry day to Tuesday).
        all_expiries = sorted({
            (i["expiry"].date() if hasattr(i["expiry"], "date") else i["expiry"])
            for i in instruments
            if i.get("name") == "NIFTY"
            and i.get("instrument_type") in ("CE", "PE")
            and (i["expiry"].date() if hasattr(i["expiry"], "date") else i["expiry"]) >= trade_date
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

        log.info("live_ingest %s: %d option contracts to fetch", trade_date, len(contracts))

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
            notes.append(f"{failed_contracts}/{len(contracts)} option contracts had no data")

        td.options_available = options_rows > 0
        td.options_row_count = options_rows
        td.options_file_name = "zerodha_live"
        log.info(
            "live_ingest %s: options %d rows  (%d contracts failed)",
            trade_date, options_rows, failed_contracts,
        )

        # ── Mark readiness ────────────────────────────────────────────────────
        td.backtest_ready   = td.spot_available and td.options_available
        td.ingestion_status = "completed" if not notes else "completed_with_warnings"
        td.ingestion_notes  = "; ".join(notes) if notes else None

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
        "options_rows":     options_rows,
        "option_contracts": len(contracts) if "contracts" in dir() else 0,
    }

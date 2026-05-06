"""Scheduled live market data warehouse sync orchestration."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date as date_type, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token
from app.models.broker_token import BrokerToken
from app.models.historical import FuturesCandle, OptionsCandle, SpotCandle, TradingDay, VixCandle
from app.models.live_data_sync import LiveDataSyncRun
from app.services.live_ingestion import ingest_live_day
from app.services.zerodha_client import validate_access_token_with_token

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SCHEDULED_TIME_LABEL = "16:00 IST"

STATUS_STARTED = "STARTED"
STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_SKIPPED_TOKEN_MISSING = "SKIPPED_TOKEN_MISSING"
STATUS_SKIPPED_TOKEN_EXPIRED = "SKIPPED_TOKEN_EXPIRED"
STATUS_FAILED_TOKEN_DECRYPTION = "FAILED_TOKEN_DECRYPTION"
STATUS_FAILED_TOKEN_VALIDATION = "FAILED_TOKEN_VALIDATION"

TOKEN_VALID = "VALID"
TOKEN_MISSING = "MISSING"
TOKEN_EXPIRED = "EXPIRED"
TOKEN_DECRYPTION_FAILED = "DECRYPTION_FAILED"
TOKEN_VALIDATION_FAILED = "VALIDATION_FAILED"

# Single-process lock: serializes concurrent calls within one uvicorn worker.
# If scaling to --workers > 1 or multiple replicas, replace with a DB advisory
# lock or a partial unique constraint on (trade_date) WHERE status = 'STARTED'.
_sync_start_lock = asyncio.Lock()


def today_ist() -> date_type:
    return datetime.now(IST).date()


def now_ist() -> datetime:
    return datetime.now(IST)


async def _latest_zerodha_token(db: AsyncSession) -> Optional[BrokerToken]:
    return (await db.execute(
        select(BrokerToken)
        .where(BrokerToken.broker == "ZERODHA")
        .order_by(desc(BrokerToken.token_date), desc(BrokerToken.updated_at))
        .limit(1)
    )).scalar_one_or_none()


async def get_started_live_data_sync_run(
    db: AsyncSession,
    trade_date: Optional[date_type] = None,
) -> Optional[LiveDataSyncRun]:
    trade_date = trade_date or today_ist()
    return (await db.execute(
        select(LiveDataSyncRun)
        .where(
            LiveDataSyncRun.trade_date == trade_date,
            LiveDataSyncRun.status == STATUS_STARTED,
        )
        .order_by(desc(LiveDataSyncRun.started_at))
        .limit(1)
    )).scalar_one_or_none()


async def create_started_live_data_sync_run(
    db: AsyncSession,
    trade_date: Optional[date_type] = None,
    triggered_by: str = "manual",
) -> Optional[LiveDataSyncRun]:
    trade_date = trade_date or today_ist()
    async with _sync_start_lock:
        if await get_started_live_data_sync_run(db, trade_date):
            return None
        run = LiveDataSyncRun(
            trade_date=trade_date,
            started_at=now_ist(),
            triggered_by=triggered_by,
            token_status=TOKEN_MISSING,
            status=STATUS_STARTED,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run


async def current_system_token_status(db: AsyncSession, trade_date: Optional[date_type] = None) -> str:
    trade_date = trade_date or today_ist()
    row = await _latest_zerodha_token(db)
    if row is None:
        return TOKEN_MISSING
    if row.token_date < trade_date:
        return TOKEN_EXPIRED
    try:
        decrypt_token(row.encrypted_token)
    except Exception:
        return TOKEN_DECRYPTION_FAILED
    return TOKEN_VALID


async def _warehouse_counts(db: AsyncSession, trade_date: date_type) -> dict:
    async def count(model):
        return (await db.execute(
            select(func.count()).select_from(model).where(model.trade_date == trade_date)
        )).scalar_one()

    expiries = (await db.execute(
        select(OptionsCandle.expiry_date)
        .where(OptionsCandle.trade_date == trade_date)
        .distinct()
        .order_by(OptionsCandle.expiry_date)
    )).scalars().all()
    contracts = (await db.execute(
        select(OptionsCandle.expiry_date, OptionsCandle.option_type, OptionsCandle.strike)
        .where(OptionsCandle.trade_date == trade_date)
        .distinct()
    )).all()

    return {
        "spot_rows": await count(SpotCandle),
        "vix_rows": await count(VixCandle),
        "futures_rows": await count(FuturesCandle),
        "options_rows": await count(OptionsCandle),
        "option_contracts": len(contracts),
        "expiries": [d.isoformat() for d in expiries],
    }


async def _trading_day_ready(db: AsyncSession, trade_date: date_type) -> bool:
    td = (await db.execute(
        select(TradingDay).where(TradingDay.trade_date == trade_date)
    )).scalar_one_or_none()
    return bool(td and td.backtest_ready)


def _sync_status_from_result(result: dict, counts: dict) -> str:
    spot_rows = counts.get("spot_rows") or result.get("spot_rows") or 0
    options_rows = counts.get("options_rows") or result.get("options_rows") or 0
    failed_items = set(result.get("failed_items") or [])
    ingestion_status = result.get("status")

    if spot_rows <= 0 or options_rows <= 0 or ingestion_status == "failed":
        return STATUS_FAILED
    if failed_items or ingestion_status == "completed_with_warnings":
        return STATUS_PARTIAL_SUCCESS
    return STATUS_SUCCESS


async def run_daily_live_data_sync(
    db: AsyncSession,
    trade_date: Optional[date_type] = None,
    triggered_by: str = "scheduler",
    force: bool = False,
    run_id: Optional[uuid.UUID] = None,
) -> LiveDataSyncRun:
    """Validate the current-day token, run live ingestion, and audit the attempt."""
    if run_id is not None:
        run = (await db.execute(
            select(LiveDataSyncRun).where(LiveDataSyncRun.id == run_id)
        )).scalar_one_or_none()
        if run is None:
            raise RuntimeError(f"Live data sync run {run_id} was not found.")
        trade_date = run.trade_date
    else:
        trade_date = trade_date or today_ist()
        run = LiveDataSyncRun(
            trade_date=trade_date,
            started_at=now_ist(),
            triggered_by=triggered_by,
            token_status=TOKEN_MISSING,
            status=STATUS_STARTED,
        )
        db.add(run)
        await db.flush()

    token_row = await _latest_zerodha_token(db)
    if token_row is None:
        run.status = STATUS_SKIPPED_TOKEN_MISSING
        run.token_status = TOKEN_MISSING
        run.completed_at = now_ist()
        run.notes = "No Zerodha token is available for live data sync."
        await db.commit()
        return run

    if token_row.token_date < trade_date:
        run.status = STATUS_SKIPPED_TOKEN_EXPIRED
        run.token_status = TOKEN_EXPIRED
        run.completed_at = now_ist()
        run.notes = f"Latest Zerodha token is for {token_row.token_date}, not {trade_date}."
        await db.commit()
        return run

    try:
        access_token = decrypt_token(token_row.encrypted_token)
    except Exception as exc:
        run.status = STATUS_FAILED_TOKEN_DECRYPTION
        run.token_status = TOKEN_DECRYPTION_FAILED
        run.completed_at = now_ist()
        run.error_message = str(exc)
        await db.commit()
        log.exception("live data sync %s: token decryption failed", trade_date)
        return run

    try:
        await asyncio.to_thread(validate_access_token_with_token, access_token)
    except Exception as exc:
        run.status = STATUS_FAILED_TOKEN_VALIDATION
        run.token_status = TOKEN_VALIDATION_FAILED
        run.completed_at = now_ist()
        run.error_message = str(exc)
        await db.commit()
        log.warning("live data sync %s: token validation failed: %s", trade_date, exc)
        return run

    run.token_status = TOKEN_VALID
    await db.flush()

    try:
        result = await ingest_live_day(db, access_token, trade_date, force=force)
        counts = await _warehouse_counts(db, trade_date)
        run.status = _sync_status_from_result(result, counts)
        run.spot_rows = counts.get("spot_rows", 0)
        run.vix_rows = counts.get("vix_rows", 0)
        run.futures_rows = counts.get("futures_rows", 0)
        run.options_rows = counts.get("options_rows", 0)
        run.option_contracts = counts.get("option_contracts", result.get("option_contracts", 0))
        run.expiries_json = counts.get("expiries") or result.get("expiries") or []
        run.failed_items_json = result.get("failed_items") or []
        run.notes = result.get("notes")
        run.completed_at = now_ist()
        await db.commit()
        return run
    except Exception as exc:
        await db.rollback()
        run.status = STATUS_FAILED
        run.token_status = TOKEN_VALID
        run.completed_at = now_ist()
        run.error_message = str(exc)
        log.exception("live data sync failed for %s: %s", trade_date, exc)
        try:
            await db.merge(run)
            await db.commit()
        except Exception:
            log.exception("live data sync: failed to save audit row after failure")
        return run


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


async def get_live_data_sync_today(db: AsyncSession, trade_date: Optional[date_type] = None) -> dict:
    trade_date = trade_date or today_ist()
    run = (await db.execute(
        select(LiveDataSyncRun)
        .where(LiveDataSyncRun.trade_date == trade_date)
        .order_by(desc(LiveDataSyncRun.started_at))
        .limit(1)
    )).scalar_one_or_none()

    if run is None:
        return {
            "trade_date": trade_date.isoformat(),
            "scheduled_time": SCHEDULED_TIME_LABEL,
            "status": STATUS_NOT_RUN,
            "token_status": await current_system_token_status(db, trade_date),
            "backtest_ready": False,
            "last_attempt_at": None,
            "completed_at": None,
            "rows": {"spot": 0, "vix": 0, "futures": 0, "options": 0},
            "option_contracts": 0,
            "expiries": [],
            "notes": None,
            "error_message": None,
        }

    return {
        "trade_date": run.trade_date.isoformat(),
        "scheduled_time": SCHEDULED_TIME_LABEL,
        "status": run.status,
        "token_status": run.token_status,
        "backtest_ready": await _trading_day_ready(db, trade_date),
        "last_attempt_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "rows": {
            "spot": run.spot_rows or 0,
            "vix": run.vix_rows or 0,
            "futures": run.futures_rows or 0,
            "options": run.options_rows or 0,
        },
        "option_contracts": run.option_contracts or 0,
        "expiries": list(run.expiries_json or []),
        "notes": run.notes,
        "error_message": run.error_message,
    }

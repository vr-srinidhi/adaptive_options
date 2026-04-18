"""
Historical data management endpoints.

All paths registered under /api/historical/ in main.py.

POST /historical/ingest/day/{date}    — ingest one day from CSV files
POST /historical/ingest/bulk          — ingest all available dates (background)
POST /historical/catalogue/sync       — scan disk → populate trading_days rows
GET  /historical/trading-days         — list trading_days catalogue
"""
import asyncio
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models.historical import TradingDay
from app.models.user import User
from app.services.historical_ingestion import (
    available_trading_dates,
    ingest_bulk,
    ingest_day,
    sync_catalogue,
)

router = APIRouter()


# ── Schema ────────────────────────────────────────────────────────────────────

class TradingDayOut(BaseModel):
    trade_date: date
    spot_available: bool
    futures_available: bool
    options_available: bool
    vix_available: bool
    ingestion_status: str
    backtest_ready: bool
    spot_row_count: Optional[int] = None
    options_row_count: Optional[int] = None
    ingestion_notes: Optional[str] = None

    class Config:
        from_attributes = True


class IngestDayRequest(BaseModel):
    force: bool = False


class BulkIngestRequest(BaseModel):
    force: bool = False
    dates: Optional[List[date]] = None   # None = all available


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/ingest/day/{trade_date}")
async def ingest_single_day(
    trade_date: date,
    body: IngestDayRequest = IngestDayRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Ingest CSV data for a single trading day."""
    try:
        result = await ingest_day(db, trade_date, force=body.force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result


async def _bulk_ingest_background(dates: Optional[List[date]], force: bool) -> None:
    """Run bulk ingestion in the background using its own DB session."""
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await ingest_bulk(db, dates, force=force)


@router.post("/ingest/bulk")
async def ingest_bulk_endpoint(
    body: BulkIngestRequest = BulkIngestRequest(),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_active_user),
):
    """
    Queue bulk ingestion of all (or specified) trading days.
    Runs in the background — returns immediately with the count of dates queued.
    """
    dates = body.dates
    if dates is None:
        dates = available_trading_dates()
    background_tasks.add_task(_bulk_ingest_background, dates, body.force)
    return {"queued": len(dates), "force": body.force}


@router.post("/catalogue/sync")
async def sync_catalogue_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Scan data directory and create trading_days rows for any new dates found."""
    new_count = await sync_catalogue(db)
    return {"new_rows_created": new_count}


@router.get("/trading-days", response_model=List[TradingDayOut])
async def list_trading_days(
    backtest_ready: Optional[bool] = Query(None),
    ingestion_status: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the trading_days catalogue, newest first."""
    q = select(TradingDay)
    if backtest_ready is not None:
        q = q.where(TradingDay.backtest_ready == backtest_ready)
    if ingestion_status:
        q = q.where(TradingDay.ingestion_status == ingestion_status)
    q = q.order_by(TradingDay.trade_date.desc()).offset(offset).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()

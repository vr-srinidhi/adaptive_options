"""
Historical backtest endpoints.

All paths registered under /api/backtests/ in main.py.

POST /backtests/batches                  — create + optionally launch a batch
GET  /backtests/batches                  — list all batches
GET  /backtests/batches/{id}             — batch detail + progress
POST /backtests/batches/{id}/run         — (re-)trigger batch execution
DELETE /backtests/batches/{id}           — cancel / delete batch
GET  /backtests/batches/{id}/sessions    — list sessions belonging to a batch
GET  /backtests/sessions/{id}            — historical session detail (mirrors /paper/session)
GET  /backtests/sessions/{id}/decisions  — minute audit log (paginated)
GET  /backtests/sessions/{id}/trade      — trade header + legs
GET  /backtests/sessions/{id}/trade/marks — per-minute MTM
"""
import asyncio
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models.historical import SessionBatch
from app.models.paper_trade import (
    MinuteDecision,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.models.user import User
from app.services.batch_runner import run_batch
from app.services.strategy_config import STRATEGY_CONFIG as _CFG

router = APIRouter()


# ── Request / response schemas ────────────────────────────────────────────────

class CreateBatchRequest(BaseModel):
    name: str
    instrument: str = "NIFTY"
    capital: float = 2_500_000.0
    start_date: date
    end_date: date
    execution_order: str = "latest_first"   # latest_first | oldest_first
    autorun: bool = True                    # start execution immediately


class BatchOut(BaseModel):
    id: uuid.UUID
    name: str
    batch_type: str
    status: str
    strategy_id: str
    strategy_version: Optional[str]
    start_date: date
    end_date: date
    execution_order: str
    total_sessions: int
    completed_sessions: int
    failed_sessions: int
    skipped_sessions: int
    total_pnl: Optional[float]

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_strategy_snapshot(instrument: str, capital: float) -> Dict[str, Any]:
    """Freeze the current strategy config at batch-creation time."""
    return {
        "instrument": instrument,
        "capital": capital,
        "strategy_id": _CFG["strategy_name"],
        "strategy_version": _CFG["strategy_version"],
        "or_window_minutes": _CFG["or_window_minutes"],
        "max_risk_pct": _CFG["max_risk_pct"],
        "target_pct": _CFG["target_profit_pct"],
        "n_candidate_spreads": _CFG["n_candidate_spreads"],
        "max_price_staleness_min": _CFG["max_price_staleness_min"],
    }


async def _get_owned_batch(
    batch_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> SessionBatch:
    result = await db.execute(
        select(SessionBatch).where(SessionBatch.id == batch_id)
    )
    b = result.scalar_one_or_none()
    if b is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    # Ownership: batches without created_by are accessible to any authenticated user
    if b.created_by is not None and b.created_by != user.id:
        raise HTTPException(status_code=404, detail="Batch not found")
    return b


# ── Batch CRUD ────────────────────────────────────────────────────────────────

@router.post("/batches", response_model=BatchOut, status_code=201)
async def create_batch(
    body: CreateBatchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new historical backtest batch and optionally start it."""
    if body.start_date > body.end_date:
        raise HTTPException(status_code=422, detail="start_date must be ≤ end_date")

    snapshot = _build_strategy_snapshot(body.instrument, body.capital)
    batch = SessionBatch(
        name=body.name,
        strategy_id=snapshot["strategy_id"],   # stored as strategy_name value
        strategy_version=snapshot.get("strategy_version"),
        strategy_config_snapshot=snapshot,
        start_date=body.start_date,
        end_date=body.end_date,
        execution_order=body.execution_order,
        status="queued" if body.autorun else "draft",
        created_by=current_user.id,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)

    if body.autorun:
        background_tasks.add_task(run_batch, batch.id)

    return batch


@router.get("/batches", response_model=List[BatchOut])
async def list_batches(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    q = (
        select(SessionBatch)
        .where(
            (SessionBatch.created_by == current_user.id)
            | (SessionBatch.created_by.is_(None))
        )
        .order_by(SessionBatch.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if status:
        q = q.where(SessionBatch.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/batches/{batch_id}", response_model=BatchOut)
async def get_batch(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await _get_owned_batch(batch_id, current_user, db)


@router.post("/batches/{batch_id}/run")
async def trigger_batch_run(
    batch_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """(Re-)trigger execution of a batch that is in draft or failed status."""
    batch = await _get_owned_batch(batch_id, current_user, db)
    if batch.status == "running":
        raise HTTPException(status_code=409, detail="Batch is already running")
    batch.status = "queued"
    await db.commit()
    background_tasks.add_task(run_batch, batch_id)
    return {"status": "queued", "batch_id": str(batch_id)}


@router.delete("/batches/{batch_id}", status_code=204)
async def delete_batch(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    batch = await _get_owned_batch(batch_id, current_user, db)
    if batch.status == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running batch; cancel first")
    await db.delete(batch)
    await db.commit()


# ── Batch sessions ────────────────────────────────────────────────────────────

@router.get("/batches/{batch_id}/sessions")
async def list_batch_sessions(
    batch_id: uuid.UUID,
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _get_owned_batch(batch_id, current_user, db)
    result = await db.execute(
        select(PaperSession)
        .where(PaperSession.batch_id == batch_id)
        .order_by(PaperSession.session_date.desc())
        .offset(offset)
        .limit(limit)
    )
    sessions = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "session_date": s.session_date.isoformat(),
            "status": s.status,
            "final_session_state": s.final_session_state,
            "summary_pnl": float(s.summary_pnl) if s.summary_pnl is not None else None,
            "decision_count": s.decision_count,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        }
        for s in sessions
    ]


# ── Individual historical session detail ─────────────────────────────────────

async def _get_hist_session(
    session_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> PaperSession:
    result = await db.execute(
        select(PaperSession).where(
            PaperSession.id == session_id,
            PaperSession.session_type == "historical_backtest",
            (PaperSession.user_id == user.id) | (PaperSession.user_id.is_(None)),
        )
    )
    s = result.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return s


@router.get("/sessions/{session_id}")
async def get_historical_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    s = await _get_hist_session(session_id, current_user, db)
    return {
        "id": str(s.id),
        "instrument": s.instrument,
        "session_date": s.session_date.isoformat(),
        "capital": float(s.capital),
        "status": s.status,
        "session_type": s.session_type,
        "execution_mode": s.execution_mode,
        "source_mode": s.source_mode,
        "final_session_state": s.final_session_state,
        "summary_pnl": float(s.summary_pnl) if s.summary_pnl is not None else None,
        "decision_count": s.decision_count,
        "batch_id": str(s.batch_id) if s.batch_id else None,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "strategy_config_snapshot": s.strategy_config_snapshot,
        "error_message": s.error_message,
    }


@router.get("/sessions/{session_id}/decisions")
async def get_session_decisions(
    session_id: uuid.UUID,
    limit: int = Query(400, le=400),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _get_hist_session(session_id, current_user, db)
    result = await db.execute(
        select(MinuteDecision)
        .where(MinuteDecision.session_id == session_id)
        .order_by(MinuteDecision.timestamp)
        .offset(offset)
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "timestamp": r.timestamp.isoformat(),
            "spot_close": float(r.spot_close) if r.spot_close is not None else None,
            "opening_range_high": float(r.opening_range_high) if r.opening_range_high is not None else None,
            "opening_range_low": float(r.opening_range_low) if r.opening_range_low is not None else None,
            "trade_state": r.trade_state,
            "signal_state": r.signal_state,
            "action": r.action,
            "reason_code": r.reason_code,
            "reason_text": r.reason_text,
            "session_state": r.session_state,
            "signal_substate": r.signal_substate,
            "rejection_gate": r.rejection_gate,
            "candidate_ranking_json": r.candidate_ranking_json,
            "selected_candidate_rank": r.selected_candidate_rank,
            "selected_candidate_score": float(r.selected_candidate_score) if r.selected_candidate_score is not None else None,
        }
        for r in rows
    ]


@router.get("/sessions/{session_id}/trade")
async def get_session_trade(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _get_hist_session(session_id, current_user, db)
    result = await db.execute(
        select(PaperTradeHeader).where(PaperTradeHeader.session_id == session_id)
    )
    header = result.scalar_one_or_none()
    if header is None:
        return {"trade": None, "legs": []}

    legs_result = await db.execute(
        select(PaperTradeLeg).where(PaperTradeLeg.trade_id == header.id)
    )
    legs = legs_result.scalars().all()

    def _f(v):
        return float(v) if v is not None else None

    return {
        "trade": {
            "id": str(header.id),
            "entry_time": header.entry_time.isoformat() if header.entry_time else None,
            "exit_time": header.exit_time.isoformat() if header.exit_time else None,
            "bias": header.bias,
            "expiry": header.expiry.isoformat() if header.expiry else None,
            "lot_size": header.lot_size,
            "approved_lots": header.approved_lots,
            "entry_debit": _f(header.entry_debit),
            "total_max_loss": _f(header.total_max_loss),
            "target_profit": _f(header.target_profit),
            "realized_gross_pnl": _f(header.realized_gross_pnl),
            "realized_net_pnl": _f(header.realized_net_pnl),
            "charges": _f(header.charges),
            "status": header.status,
            "exit_reason": header.exit_reason,
            "long_strike": header.long_strike,
            "short_strike": header.short_strike,
            "option_type": header.option_type,
        },
        "legs": [
            {
                "leg_side": l.leg_side,
                "option_type": l.option_type,
                "strike": l.strike,
                "expiry": l.expiry.isoformat() if l.expiry else None,
                "entry_price": _f(l.entry_price),
                "exit_price": _f(l.exit_price),
            }
            for l in legs
        ],
    }


@router.get("/sessions/{session_id}/trade/marks")
async def get_session_marks(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _get_hist_session(session_id, current_user, db)
    result = await db.execute(
        select(PaperTradeHeader).where(PaperTradeHeader.session_id == session_id)
    )
    header = result.scalar_one_or_none()
    if header is None:
        return []

    marks_result = await db.execute(
        select(PaperTradeMinuteMark)
        .where(PaperTradeMinuteMark.trade_id == header.id)
        .order_by(PaperTradeMinuteMark.timestamp)
    )
    marks = marks_result.scalars().all()

    def _f(v):
        return float(v) if v is not None else None

    return [
        {
            "timestamp": m.timestamp.isoformat(),
            "total_mtm": _f(m.total_mtm),
            "gross_mtm": _f(m.gross_mtm),
            "estimated_net_mtm": _f(m.estimated_net_mtm),
            "long_leg_price": _f(m.long_leg_price),
            "short_leg_price": _f(m.short_leg_price),
            "action": m.action,
            "reason": m.reason,
        }
        for m in marks
    ]

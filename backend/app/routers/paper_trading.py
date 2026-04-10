"""
Paper Trading REST endpoints.

All paths are under /api/paper/ (registered in main.py).

POST /paper/session/run          — replay one day, bulk-insert results
GET  /paper/sessions             — list all sessions
GET  /paper/session/{id}         — session detail + summary stats
GET  /paper/session/{id}/decisions — full minute audit log (paginated)
GET  /paper/session/{id}/trade   — trade header + legs
GET  /paper/session/{id}/trade/marks — per-minute MTM array
"""
import asyncio
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.paper_trade import (
    MinuteDecision,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.services.paper_engine import run_paper_engine
from app.services.zerodha_client import DataUnavailableError

router = APIRouter()


# ── Request schema ────────────────────────────────────────────────────────────

class RunSessionRequest(BaseModel):
    instrument: str
    date: str
    capital: float
    access_token: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_dict(s: PaperSession) -> dict:
    return {
        "id": str(s.id),
        "instrument": s.instrument,
        "session_date": str(s.session_date),
        "capital": float(s.capital),
        "status": s.status,
        "error_message": s.error_message,
        "decision_count": s.decision_count,
        "created_at": str(s.created_at) if s.created_at else None,
    }


def _decision_dict(d: MinuteDecision) -> dict:
    return {
        "id": str(d.id),
        "timestamp": str(d.timestamp),
        "spot_close": float(d.spot_close) if d.spot_close is not None else None,
        "opening_range_high": float(d.opening_range_high) if d.opening_range_high else None,
        "opening_range_low": float(d.opening_range_low) if d.opening_range_low else None,
        "trade_state": d.trade_state,
        "signal_state": d.signal_state,
        "action": d.action,
        "reason_code": d.reason_code,
        "reason_text": d.reason_text,
        "candidate_structure": d.candidate_structure,
        "computed_max_loss": float(d.computed_max_loss) if d.computed_max_loss is not None else None,
        "computed_target": float(d.computed_target) if d.computed_target is not None else None,
    }


def _trade_dict(t: PaperTradeHeader, legs=None) -> dict:
    d = {
        "id": str(t.id),
        "session_id": str(t.session_id),
        "entry_time": str(t.entry_time) if t.entry_time else None,
        "exit_time": str(t.exit_time) if t.exit_time else None,
        "bias": t.bias,
        "expiry": str(t.expiry) if t.expiry else None,
        "lot_size": t.lot_size,
        "approved_lots": t.approved_lots,
        "entry_debit": float(t.entry_debit) if t.entry_debit is not None else None,
        "total_max_loss": float(t.total_max_loss) if t.total_max_loss is not None else None,
        "target_profit": float(t.target_profit) if t.target_profit is not None else None,
        "realized_gross_pnl": float(t.realized_gross_pnl) if t.realized_gross_pnl is not None else None,
        "realized_net_pnl": float(t.realized_net_pnl) if t.realized_net_pnl is not None else None,
        "status": t.status,
        "exit_reason": t.exit_reason,
        "long_strike": t.long_strike,
        "short_strike": t.short_strike,
        "option_type": t.option_type,
    }
    if legs is not None:
        d["legs"] = [
            {
                "leg_side": l.leg_side,
                "option_type": l.option_type,
                "strike": l.strike,
                "expiry": str(l.expiry) if l.expiry else None,
                "entry_price": float(l.entry_price) if l.entry_price is not None else None,
                "exit_price": float(l.exit_price) if l.exit_price is not None else None,
            }
            for l in legs
        ]
    return d


def _mark_dict(m: PaperTradeMinuteMark) -> dict:
    return {
        "timestamp": str(m.timestamp),
        "long_leg_price": float(m.long_leg_price) if m.long_leg_price is not None else None,
        "short_leg_price": float(m.short_leg_price) if m.short_leg_price is not None else None,
        "current_spread_value": float(m.current_spread_value) if m.current_spread_value is not None else None,
        "mtm_per_lot": float(m.mtm_per_lot) if m.mtm_per_lot is not None else None,
        "total_mtm": float(m.total_mtm) if m.total_mtm is not None else None,
        "distance_to_target": float(m.distance_to_target) if m.distance_to_target is not None else None,
        "distance_to_stop": float(m.distance_to_stop) if m.distance_to_stop is not None else None,
        "action": m.action,
        "reason": m.reason,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/paper/session/run")
async def run_session(req: RunSessionRequest, db: AsyncSession = Depends(get_db)):
    # Validate inputs
    try:
        trade_date = date.fromisoformat(req.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    instrument = req.instrument.strip().upper()
    if instrument not in ("NIFTY", "BANKNIFTY"):
        raise HTTPException(status_code=400, detail="instrument must be NIFTY or BANKNIFTY.")
    if not (50_000 <= req.capital <= 10_000_000):
        raise HTTPException(status_code=400, detail="Capital must be ₹50,000 – ₹10,000,000.")
    if not req.access_token or len(req.access_token) < 10:
        raise HTTPException(status_code=400, detail="A valid Zerodha access token is required.")

    # Create session row (status=RUNNING)
    session_id = uuid.uuid4()
    ps = PaperSession(
        id=session_id,
        instrument=instrument,
        session_date=trade_date,
        capital=req.capital,
        status="RUNNING",
    )
    db.add(ps)
    await db.commit()

    # Run the engine in a thread pool (Zerodha HTTP calls are blocking)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            run_paper_engine,
            session_id, trade_date, instrument, req.capital, req.access_token,
        )
    except DataUnavailableError as exc:
        ps.status = "ERROR"
        ps.error_message = str(exc)
        await db.commit()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        ps.status = "ERROR"
        ps.error_message = str(exc)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Engine error: {exc}")

    # Bulk insert decisions
    for d in result["decisions"]:
        db.add(MinuteDecision(**d))

    # Insert trade header and related rows if a trade was opened
    if result["trade_header"]:
        db.add(PaperTradeHeader(**result["trade_header"]))
        for leg in result["trade_legs"]:
            db.add(PaperTradeLeg(**leg))
        for mark in result["minute_marks"]:
            db.add(PaperTradeMinuteMark(**mark))

    # Update session to COMPLETED
    ps.status = "COMPLETED"
    ps.decision_count = len(result["decisions"])
    await db.commit()
    await db.refresh(ps)

    return {
        "session_id": str(session_id),
        "status": "COMPLETED",
        "decision_count": ps.decision_count,
        "trade_opened": result["trade_header"] is not None,
        "minute_marks_count": len(result["minute_marks"]),
    }


@router.get("/paper/sessions")
async def list_sessions(
    instrument: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    q = select(PaperSession).order_by(PaperSession.created_at.desc())
    if instrument:
        q = q.where(PaperSession.instrument == instrument.strip().upper())
    q = q.limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return [_session_dict(s) for s in rows]


@router.get("/paper/session/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    s = (await db.execute(
        select(PaperSession).where(PaperSession.id == sid)
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found.")

    # Quick stats: count decisions by action
    from sqlalchemy import func
    action_rows = (await db.execute(
        select(MinuteDecision.action, func.count().label("cnt"))
        .where(MinuteDecision.session_id == sid)
        .group_by(MinuteDecision.action)
    )).all()
    action_summary = {row.action: row.cnt for row in action_rows}

    return {**_session_dict(s), "action_summary": action_summary}


@router.get("/paper/session/{session_id}/decisions")
async def get_decisions(
    session_id: str,
    action: Optional[str] = None,
    limit: int = 400,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    q = (
        select(MinuteDecision)
        .where(MinuteDecision.session_id == sid)
        .order_by(MinuteDecision.timestamp)
    )
    if action:
        q = q.where(MinuteDecision.action == action.upper())
    q = q.limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [_decision_dict(d) for d in rows]


@router.get("/paper/session/{session_id}/trade")
async def get_trade(session_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    trade = (await db.execute(
        select(PaperTradeHeader).where(PaperTradeHeader.session_id == sid)
    )).scalar_one_or_none()

    if not trade:
        return {"trade": None, "reason": "No trade was opened this session."}

    legs = (await db.execute(
        select(PaperTradeLeg).where(PaperTradeLeg.trade_id == trade.id)
    )).scalars().all()

    return {"trade": _trade_dict(trade, legs=legs)}


@router.get("/paper/session/{session_id}/trade/marks")
async def get_marks(session_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    trade = (await db.execute(
        select(PaperTradeHeader).where(PaperTradeHeader.session_id == sid)
    )).scalar_one_or_none()

    if not trade:
        return []

    marks = (await db.execute(
        select(PaperTradeMinuteMark)
        .where(PaperTradeMinuteMark.trade_id == trade.id)
        .order_by(PaperTradeMinuteMark.timestamp)
    )).scalars().all()

    return [_mark_dict(m) for m in marks]

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
from collections import defaultdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter
from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models.paper_trade import (
    MinuteDecision,
    PaperCandleSeries,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.models.user import User
from app.services import zerodha_client
from app.services.audit import log_event
from app.services.paper_engine import run_paper_engine
from app.services.token_store import get_broker_token, store_broker_token
from app.services.zerodha_client import DataUnavailableError

router = APIRouter()


# ── Ownership helper ──────────────────────────────────────────────────────────

async def _get_owned_session(
    sid: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> PaperSession:
    """Load a PaperSession and enforce strict ownership."""
    s = (await db.execute(
        select(PaperSession).where(
            PaperSession.id == sid,
            PaperSession.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found.")
    return s


# ── Request schema ────────────────────────────────────────────────────────────

class RunSessionRequest(BaseModel):
    instrument: str
    date: str
    capital: float
    request_token: Optional[str] = None  # preferred: backend exchanges → access token
    access_token: Optional[str] = None   # legacy fallback: caller already exchanged


class SessionExportBundleRequest(BaseModel):
    session_ids: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_dict(s: PaperSession, *, summary_pnl=None) -> dict:
    pnl_value = s.summary_pnl if s.summary_pnl is not None else summary_pnl
    return {
        "id": str(s.id),
        "instrument": s.instrument,
        "session_date": str(s.session_date),
        "capital": float(s.capital),
        "status": s.status,
        "error_message": s.error_message,
        "decision_count": s.decision_count,
        "created_at": str(s.created_at) if s.created_at else None,
        "final_session_state": s.final_session_state,
        "summary_pnl": float(pnl_value) if pnl_value is not None else None,
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
        "session_state": d.session_state,
        "signal_substate": d.signal_substate,
        "rejection_gate": d.rejection_gate,
        "price_freshness_json": d.price_freshness_json,
        "candidate_ranking_json": d.candidate_ranking_json,
        "selected_candidate_rank": d.selected_candidate_rank,
        "selected_candidate_score": float(d.selected_candidate_score) if d.selected_candidate_score is not None else None,
        "selected_candidate_score_breakdown_json": d.selected_candidate_score_breakdown_json,
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
        "charges": float(t.charges) if t.charges is not None else None,
        "charges_breakdown_json": t.charges_breakdown_json,
        "strategy_name": t.strategy_name,
        "strategy_version": t.strategy_version,
        "strategy_params_json": t.strategy_params_json,
        "risk_cap": float(t.risk_cap) if t.risk_cap is not None else None,
        "entry_reason_code": t.entry_reason_code,
        "entry_reason_text": t.entry_reason_text,
        "selection_method": t.selection_method,
        "selected_candidate_rank": t.selected_candidate_rank,
        "selected_candidate_score": float(t.selected_candidate_score) if t.selected_candidate_score is not None else None,
        "selected_candidate_score_breakdown_json": t.selected_candidate_score_breakdown_json,
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
        "gross_mtm": float(m.gross_mtm) if m.gross_mtm is not None else None,
        "estimated_exit_charges": float(m.estimated_exit_charges) if m.estimated_exit_charges is not None else None,
        "estimated_net_mtm": float(m.estimated_net_mtm) if m.estimated_net_mtm is not None else None,
        "price_freshness_json": m.price_freshness_json,
    }


def _candle_series_dict(c: PaperCandleSeries) -> dict:
    return {
        "series_type": c.series_type,
        "candles": c.candles,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/paper/session/run")
@limiter.limit("5/minute")
async def run_session(
    request: Request,
    req: RunSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
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
    # ── Resolve Zerodha access token (3-path) ─────────────────────────────────
    access_token: str = ""

    if req.request_token and req.request_token.strip():
        # Path A: exchange request token → access token, store encrypted in DB
        try:
            session_data = await asyncio.get_event_loop().run_in_executor(
                None,
                zerodha_client.generate_session,
                req.request_token.strip(),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Zerodha token exchange failed: {exc}",
            )
        access_token = session_data.get("access_token", "")
        await store_broker_token(db, user.id, access_token)
        asyncio.ensure_future(
            log_event(
                "ZERODHA_CONNECT",
                user_id=user.id,
                ip_address=request.client.host if request.client else "unknown",
            )
        )

    elif req.access_token and len(req.access_token.strip()) >= 10:
        # Path B: legacy — caller already exchanged the token, use directly
        access_token = req.access_token.strip()

    else:
        # Path C: no token in request — use today's stored token from DB
        stored = await get_broker_token(db, user.id)
        if not stored:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No Zerodha token available. Supply a request_token "
                    "or call POST /auth/zerodha/session first."
                ),
            )
        access_token = stored

    if not access_token or len(access_token) < 10:
        raise HTTPException(
            status_code=400, detail="Could not obtain a valid Zerodha access token."
        )

    # Create session row (status=RUNNING)
    session_id = uuid.uuid4()
    ps = PaperSession(
        id=session_id,
        instrument=instrument,
        session_date=trade_date,
        capital=req.capital,
        status="RUNNING",
        user_id=user.id,
    )
    db.add(ps)
    await db.commit()

    # Run the engine in a thread pool (Zerodha HTTP calls are blocking)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            run_paper_engine,
            session_id, trade_date, instrument, req.capital, access_token,
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

    if result["trade_header"]:
        # Strip the engine-generated id; let the ORM model generate a fresh one.
        # Flush immediately so SQLAlchemy materialises header.id before we use it
        # for the FK on legs/marks.
        header_data = {k: v for k, v in result["trade_header"].items() if k != "id"}
        header = PaperTradeHeader(**header_data)
        db.add(header)
        await db.flush()            # header.id is now populated by the ORM default
        trade_db_id = header.id     # capture before commit expires the object
        await db.commit()           # decisions + header committed

        # Legs and marks reference the actual DB id, not the engine's ephemeral id
        for leg in result["trade_legs"]:
            db.add(PaperTradeLeg(**{**leg, "trade_id": trade_db_id}))
        for mark in result["minute_marks"]:
            db.add(PaperTradeMinuteMark(**{**mark, "trade_id": trade_db_id}))
        await db.commit()
    else:
        await db.commit()           # decisions only

    # Candle series — always stored regardless of whether a trade was opened
    for cs in result.get("candle_series", []):
        db.add(PaperCandleSeries(**cs))
    await db.commit()

    # Update session to COMPLETED
    ps.status = "COMPLETED"
    ps.decision_count = len(result["decisions"])
    ps.final_session_state = result.get("final_session_state")
    if result["trade_header"] and result["trade_header"].get("realized_net_pnl") is not None:
        ps.summary_pnl = result["trade_header"]["realized_net_pnl"]
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
    user: User = Depends(get_current_active_user),
):
    q = select(PaperSession).order_by(PaperSession.created_at.desc())
    q = q.where(PaperSession.user_id == user.id)
    if instrument:
        q = q.where(PaperSession.instrument == instrument.strip().upper())
    q = q.limit(limit)
    rows = (await db.execute(q)).scalars().all()

    pnl_by_session = {}
    unresolved_ids = [s.id for s in rows if s.summary_pnl is None]
    if unresolved_ids:
        pnl_rows = (await db.execute(
            select(PaperTradeHeader.session_id, PaperTradeHeader.realized_net_pnl).where(
                PaperTradeHeader.session_id.in_(unresolved_ids)
            )
        )).all()
        pnl_by_session = {
            session_id: realized_net_pnl
            for session_id, realized_net_pnl in pnl_rows
            if realized_net_pnl is not None
        }

    return [_session_dict(s, summary_pnl=pnl_by_session.get(s.id)) for s in rows]


@router.post("/paper/sessions/export-bundle")
async def export_sessions_bundle(
    body: SessionExportBundleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    ordered_ids: list[str] = []
    seen_ids = set()
    for raw_id in body.session_ids:
        value = (raw_id or "").strip()
        if value and value not in seen_ids:
            ordered_ids.append(value)
            seen_ids.add(value)

    if not ordered_ids:
        raise HTTPException(status_code=400, detail="Provide at least one session ID.")

    session_ids: list[uuid.UUID] = []
    for raw_id in ordered_ids:
        try:
            session_ids.append(uuid.UUID(raw_id))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid session ID: {raw_id}")

    sessions = (await db.execute(
        select(PaperSession).where(
            PaperSession.id.in_(session_ids),
            PaperSession.user_id == user.id,
        )
    )).scalars().all()

    session_by_id = {session.id: session for session in sessions}
    missing_ids = [str(session_id) for session_id in session_ids if session_id not in session_by_id]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Sessions not found or not accessible: {', '.join(missing_ids)}",
        )

    decision_rows = (await db.execute(
        select(MinuteDecision)
        .where(MinuteDecision.session_id.in_(session_ids))
        .order_by(MinuteDecision.session_id, MinuteDecision.timestamp)
    )).scalars().all()
    decisions_by_session = defaultdict(list)
    for row in decision_rows:
        decisions_by_session[row.session_id].append(row)

    trade_rows = (await db.execute(
        select(PaperTradeHeader)
        .where(PaperTradeHeader.session_id.in_(session_ids))
        .order_by(PaperTradeHeader.entry_time)
    )).scalars().all()
    trade_by_session = {trade.session_id: trade for trade in trade_rows}

    trade_ids = [trade.id for trade in trade_rows]
    legs_by_trade = defaultdict(list)
    marks_by_trade = defaultdict(list)
    if trade_ids:
        leg_rows = (await db.execute(
            select(PaperTradeLeg)
            .where(PaperTradeLeg.trade_id.in_(trade_ids))
            .order_by(PaperTradeLeg.trade_id, PaperTradeLeg.leg_side)
        )).scalars().all()
        for row in leg_rows:
            legs_by_trade[row.trade_id].append(row)

        mark_rows = (await db.execute(
            select(PaperTradeMinuteMark)
            .where(PaperTradeMinuteMark.trade_id.in_(trade_ids))
            .order_by(PaperTradeMinuteMark.trade_id, PaperTradeMinuteMark.timestamp)
        )).scalars().all()
        for row in mark_rows:
            marks_by_trade[row.trade_id].append(row)

    candle_rows = (await db.execute(
        select(PaperCandleSeries)
        .where(PaperCandleSeries.session_id.in_(session_ids))
        .order_by(PaperCandleSeries.session_id, PaperCandleSeries.series_type)
    )).scalars().all()
    candles_by_session = defaultdict(list)
    for row in candle_rows:
        candles_by_session[row.session_id].append(row)

    payload = []
    for session_id in session_ids:
        session = session_by_id[session_id]
        decisions = decisions_by_session.get(session_id, [])
        action_summary = defaultdict(int)
        for decision in decisions:
            if decision.action:
                action_summary[decision.action] += 1

        trade = trade_by_session.get(session_id)
        fallback_pnl = None
        if session.summary_pnl is None and trade and trade.realized_net_pnl is not None:
            fallback_pnl = trade.realized_net_pnl

        payload.append({
            "session": {
                **_session_dict(session, summary_pnl=fallback_pnl),
                "action_summary": dict(action_summary),
            },
            "trade": _trade_dict(trade, legs_by_trade.get(trade.id, [])) if trade else None,
            "decisions": [_decision_dict(decision) for decision in decisions],
            "marks": [_mark_dict(mark) for mark in marks_by_trade.get(trade.id, [])] if trade else [],
            "candle_series": [_candle_series_dict(candle) for candle in candles_by_session.get(session_id, [])],
        })

    return {"sessions": payload}


@router.get("/paper/session/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    s = await _get_owned_session(sid, user, db)

    # Quick stats: count decisions by action
    from sqlalchemy import func
    action_rows = (await db.execute(
        select(MinuteDecision.action, func.count().label("cnt"))
        .where(MinuteDecision.session_id == sid)
        .group_by(MinuteDecision.action)
    )).all()
    action_summary = {row.action: row.cnt for row in action_rows}

    fallback_pnl = None
    if s.summary_pnl is None:
        fallback_pnl = (await db.execute(
            select(PaperTradeHeader.realized_net_pnl).where(PaperTradeHeader.session_id == sid)
        )).scalar_one_or_none()

    return {
        **_session_dict(s, summary_pnl=fallback_pnl),
        "action_summary": action_summary,
    }


@router.get("/paper/session/{session_id}/decisions")
async def get_decisions(
    session_id: str,
    action: Optional[str] = None,
    limit: int = 400,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    await _get_owned_session(sid, user, db)  # ownership check

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
async def get_trade(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    await _get_owned_session(sid, user, db)  # ownership check

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
async def get_marks(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    await _get_owned_session(sid, user, db)  # ownership check

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


@router.get("/paper/session/{session_id}/candles")
async def get_candles(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Return all stored candle series for a session.
    Each item: {series_type: str, candles: [{time,open,high,low,close,volume},...]}
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")

    await _get_owned_session(sid, user, db)  # ownership check

    rows = (await db.execute(
        select(PaperCandleSeries)
        .where(PaperCandleSeries.session_id == sid)
        .order_by(PaperCandleSeries.series_type)
    )).scalars().all()

    return [
        {"series_type": r.series_type, "candles": r.candles}
        for r in rows
    ]

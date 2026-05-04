"""
Live Paper Trading API

All endpoints under /api/v2/live-paper/.

Auth
----
Standard Bearer token for all REST endpoints.
SSE stream (/today/stream) accepts token via query param because EventSource
does not support custom headers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token as decode_access_token
from app.database import AsyncSessionLocal, get_db
from app.dependencies.auth import get_current_active_user
from app.models.live_paper import LivePaperConfig, LivePaperSession
from app.models.strategy_run import (
    StrategyLegMtm, StrategyRun, StrategyRunEvent, StrategyRunLeg, StrategyRunMtm,
)
from app.models.user import User
from app.models.broker_token import BrokerToken
from app.services.live_paper_engine import (
    check_and_resume_sessions,
    get_active_session_id,
    get_or_create_sse_queue,
    start_live_session,
)

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

router = APIRouter(prefix="/api/v2/live-paper", tags=["live-paper"])

_DEFAULT_PARAMS = {
    "lock_trigger":          20_000,
    "loss_lock_trigger":     25_000,
    "wing_width_steps":      2,
    "trail_trigger":         12_000,
    "trail_pct":             0.50,
    "stop_capital_pct":      0.015,
    "time_exit":             "15:25",
    "poll_interval_seconds": 60,     # 3 | 5 | 10 | 15 | 30 | 60
}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    strategy_id:    Optional[str]   = None
    instrument:     Optional[str]   = None
    capital:        Optional[float] = None
    entry_time:     Optional[str]   = None
    params:         Optional[Dict[str, Any]] = None
    enabled:        Optional[bool]  = None
    execution_mode: Optional[str]   = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_config(db: AsyncSession, user: User) -> LivePaperConfig:
    """Return the user's config row, creating a default one if it doesn't exist."""
    row = (await db.execute(
        select(LivePaperConfig).where(LivePaperConfig.user_id == user.id).limit(1)
    )).scalar_one_or_none()

    if not row:
        row = LivePaperConfig(
            user_id=user.id,
            strategy_id="short_straddle_dual_lock",
            instrument="NIFTY",
            capital=2_500_000,
            entry_time="09:50",
            params_json=_DEFAULT_PARAMS,
            enabled=False,
            execution_mode="paper",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _get_today_session(db: AsyncSession, user: User) -> Optional[LivePaperSession]:
    today = date.today()
    return (await db.execute(
        select(LivePaperSession)
        .where(
            LivePaperSession.trade_date == today,
            LivePaperSession.user_id == user.id,
        )
        .limit(1)
    )).scalar_one_or_none()


async def _token_status(db: AsyncSession, user: User) -> str:
    """Returns 'valid', 'expired', or 'missing'."""
    row = (await db.execute(
        select(BrokerToken).where(BrokerToken.user_id == user.id).limit(1)
    )).scalar_one_or_none()
    if not row:
        return "missing"
    if row.token_date and row.token_date < date.today():
        return "expired"
    return "valid"


def _serialize_config(cfg: LivePaperConfig) -> Dict:
    return {
        "id":             str(cfg.id),
        "strategy_id":    cfg.strategy_id,
        "instrument":     cfg.instrument,
        "capital":        float(cfg.capital),
        "entry_time":     cfg.entry_time,
        "params":         dict(cfg.params_json or {}),
        "enabled":        cfg.enabled,
        "execution_mode": cfg.execution_mode,
    }


def _serialize_session(s: LivePaperSession) -> Dict:
    return {
        "id":                str(s.id),
        "status":            s.status,
        "trade_date":        s.trade_date.isoformat() if s.trade_date else None,
        "atm_strike":        s.atm_strike,
        "expiry_date":       s.expiry_date.isoformat() if s.expiry_date else None,
        "ce_symbol":         s.ce_symbol,
        "pe_symbol":         s.pe_symbol,
        "wing_ce_symbol":    s.wing_ce_symbol,
        "wing_pe_symbol":    s.wing_pe_symbol,
        "approved_lots":     s.approved_lots,
        "strategy_run_id":   str(s.strategy_run_id) if s.strategy_run_id else None,
        "net_mtm_latest":    float(s.net_mtm_latest) if s.net_mtm_latest is not None else None,
        "spot_latest":       float(s.spot_latest) if s.spot_latest is not None else None,
        "lock_status":       s.lock_status,
        "exit_reason":       s.exit_reason,
        "realized_net_pnl":  float(s.realized_net_pnl) if s.realized_net_pnl is not None else None,
        "waiting_spot_json": list(s.waiting_spot_json or []),
        "error_message":     s.error_message,
    }


async def _get_mtm_series(db: AsyncSession, run_id: uuid.UUID):
    rows = (await db.execute(
        select(StrategyRunMtm)
        .where(StrategyRunMtm.run_id == run_id)
        .order_by(StrategyRunMtm.timestamp)
    )).scalars().all()

    # Build per-timestamp CE/PE price lookup from strategy_leg_mtm (SELL legs only)
    leg_rows = (await db.execute(
        select(StrategyRunLeg.option_type, StrategyLegMtm.timestamp, StrategyLegMtm.price)
        .join(StrategyLegMtm, StrategyLegMtm.leg_id == StrategyRunLeg.id)
        .where(StrategyRunLeg.run_id == run_id, StrategyRunLeg.side == "SELL")
    )).all()
    leg_lookup: dict = {}
    for opt_type, ts, price in leg_rows:
        key = ts.isoformat()
        if price is not None:
            leg_lookup.setdefault(key, {})[opt_type] = float(price)

    return [
        {
            "timestamp":        r.timestamp.isoformat(),
            "spot":             float(r.spot_close) if r.spot_close else None,
            "gross_mtm":        float(r.gross_mtm) if r.gross_mtm is not None else None,
            "net_mtm":          float(r.net_mtm) if r.net_mtm is not None else None,
            "trail_stop_level": float(r.trail_stop_level) if r.trail_stop_level is not None else None,
            "event_code":       r.event_code,
            "ce_price":         leg_lookup.get(r.timestamp.isoformat(), {}).get("CE"),
            "pe_price":         leg_lookup.get(r.timestamp.isoformat(), {}).get("PE"),
        }
        for r in rows
    ]


async def _get_events(db: AsyncSession, run_id: uuid.UUID):
    rows = (await db.execute(
        select(StrategyRunEvent)
        .where(StrategyRunEvent.run_id == run_id)
        .order_by(StrategyRunEvent.timestamp)
    )).scalars().all()
    return [
        {
            "timestamp":   r.timestamp.isoformat(),
            "event_type":  r.event_type,
            "reason_code": r.reason_code,
            "reason_text": r.reason_text,
            "payload":     r.payload_json,
        }
        for r in rows
    ]


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/config")
async def get_config(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    cfg = await _get_or_create_config(db, user)
    return _serialize_config(cfg)


@router.put("/config")
async def update_config(
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    cfg = await _get_or_create_config(db, user)

    if body.execution_mode == "live":
        raise HTTPException(
            status_code=422,
            detail=(
                "execution_mode='live' is disabled — live order placement, "
                "kill-switch, and position reconciliation are not yet implemented."
            ),
        )

    if body.strategy_id    is not None: cfg.strategy_id    = body.strategy_id
    if body.instrument     is not None: cfg.instrument     = body.instrument
    if body.capital        is not None: cfg.capital        = body.capital
    if body.entry_time     is not None: cfg.entry_time     = body.entry_time
    if body.enabled        is not None: cfg.enabled        = body.enabled
    if body.execution_mode is not None: cfg.execution_mode = body.execution_mode
    if body.params         is not None:
        merged = dict(cfg.params_json or {})
        merged.update(body.params)
        cfg.params_json = merged

    cfg.updated_at = datetime.now(IST)
    await db.commit()
    await db.refresh(cfg)
    return _serialize_config(cfg)


@router.get("/today")
async def get_today(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Full status snapshot for today's session.  Polled by the frontend on mount."""
    cfg     = await _get_or_create_config(db, user)
    session = await _get_today_session(db, user)
    tk_st   = await _token_status(db, user)

    mtm_series = []
    events     = []
    run        = None

    leg_entry_prices: dict = {}
    if session and session.strategy_run_id:
        run = (await db.execute(
            select(StrategyRun).where(StrategyRun.id == session.strategy_run_id)
        )).scalar_one_or_none()
        mtm_series = await _get_mtm_series(db, session.strategy_run_id)
        events     = await _get_events(db, session.strategy_run_id)

        # Fetch entry prices for CE/PE SELL legs
        legs = (await db.execute(
            select(StrategyRunLeg)
            .where(StrategyRunLeg.run_id == session.strategy_run_id, StrategyRunLeg.side == "SELL")
        )).scalars().all()
        for leg in legs:
            if leg.entry_price:
                leg_entry_prices[leg.option_type] = float(leg.entry_price)

    return {
        "config":        _serialize_config(cfg),
        "session":       _serialize_session(session) if session else None,
        "mtm_series":    mtm_series,
        "events":        events,
        "token_status":  tk_st,
        "is_live":       get_active_session_id() is not None,
        "run": {
            "entry_credit_total": float(run.entry_credit_total) if run and run.entry_credit_total else None,
            "lot_size":           run.lot_size if run else None,
            "approved_lots":      run.approved_lots if run else None,
            "ce_entry_price":     leg_entry_prices.get("CE"),
            "pe_entry_price":     leg_entry_prices.get("PE"),
        } if run else None,
    }


@router.get("/history")
async def get_history(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Past live paper sessions, newest first."""
    rows = (await db.execute(
        select(LivePaperSession)
        .where(LivePaperSession.user_id == user.id)
        .order_by(LivePaperSession.trade_date.desc())
        .limit(limit)
        .offset(offset)
    )).scalars().all()
    return [_serialize_session(r) for r in rows]


@router.post("/start")
async def manual_start(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Manually trigger today's live paper session (bypasses 09:14 scheduler)."""
    if get_active_session_id() is not None:
        raise HTTPException(status_code=409, detail="A session is already running.")
    await start_live_session(db, user_id=user.id)
    return {"detail": "Session started."}


@router.post("/stop")
async def manual_stop(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Emergency stop — marks today's session as error so the engine exits on next tick."""
    session = await _get_today_session(db, user)
    if not session:
        raise HTTPException(status_code=404, detail="No session found for today.")
    if session.status in ("exited", "no_trade", "error"):
        raise HTTPException(status_code=409, detail=f"Session already in terminal state: {session.status}.")
    await db.execute(
        update(LivePaperSession)
        .where(LivePaperSession.id == session.id)
        .values(status="error", error_message="Manually stopped by user.")
    )
    await db.commit()
    return {"detail": "Stop signal sent. Session will exit on next minute tick."}


# ── SSE stream ────────────────────────────────────────────────────────────────

async def _get_user_from_token_param(
    token: str = Query(..., description="Access token (Bearer value)"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Auth dependency for SSE — token passed as query param because EventSource
    does not support custom request headers."""
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token has no subject.")
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID in token.")
    user = (await db.execute(
        select(User).where(User.id == uid)
    )).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")
    return user


@router.get("/today/stream")
async def stream_today(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_get_user_from_token_param),
):
    """
    SSE endpoint — pushes live minute updates to the browser.

    The frontend connects with:
        new EventSource(`/api/v2/live-paper/today/stream?token=${accessToken}`)

    Format: standard SSE text/event-stream.
    Each data payload is a JSON object.
    A keepalive `: ping` comment is sent every 25 s to prevent proxy timeouts.
    A `type: DONE` message signals session end; the client should close the connection.
    """
    session = await _get_today_session(db, user)
    if not session:
        async def _empty():
            yield "data: {\"type\": \"NO_SESSION\"}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    q = get_or_create_sse_queue(session.id)

    async def _event_stream():
        # Send current session state immediately on connect
        snap = _serialize_session(session)
        yield f"data: {json.dumps({'type': 'SNAPSHOT', 'session': snap})}\n\n"

        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=25)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("type") == "DONE":
                    break
            except asyncio.TimeoutError:
                yield ": ping\n\n"   # keepalive — prevents Railway proxy timeout

    return StreamingResponse(_event_stream(), media_type="text/event-stream")

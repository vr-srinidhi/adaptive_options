"""
Live Paper Trading API

All endpoints under /api/v2/live-paper/.

Auth
----
Standard Bearer token for all REST endpoints.
SSE stream accepts token via query param because EventSource
does not support custom headers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional
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
    is_session_active,
    start_live_session,
    get_sessions_for_date,
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
    "poll_interval_seconds": 10,
    "expiry_offset":         0,
}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ConfigCreate(BaseModel):
    label:          Optional[str]   = None
    strategy_id:    Optional[str]   = None
    instrument:     Optional[str]   = None
    capital:        Optional[float] = None
    entry_time:     Optional[str]   = None
    params:         Optional[Dict[str, Any]] = None
    enabled:        Optional[bool]  = None
    execution_mode: Optional[str]   = None


class ConfigUpdate(BaseModel):
    label:          Optional[str]   = None
    strategy_id:    Optional[str]   = None
    instrument:     Optional[str]   = None
    capital:        Optional[float] = None
    entry_time:     Optional[str]   = None
    params:         Optional[Dict[str, Any]] = None
    enabled:        Optional[bool]  = None
    execution_mode: Optional[str]   = None


class StartBody(BaseModel):
    config_id: Optional[str] = None   # start specific slot; omit = start all enabled


class StopBody(BaseModel):
    session_id: Optional[str] = None  # stop specific session; omit = stop all today


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_config(db: AsyncSession, user: User) -> LivePaperConfig:
    """Return the user's first config, creating a default if none exists."""
    row = (await db.execute(
        select(LivePaperConfig)
        .where(LivePaperConfig.user_id == user.id)
        .order_by(LivePaperConfig.created_at)
        .limit(1)
    )).scalar_one_or_none()

    if not row:
        row = LivePaperConfig(
            user_id=user.id,
            label="Default",
            strategy_id="short_straddle_dual_lock",
            instrument="NIFTY",
            capital=2_500_000,
            entry_time="10:15",
            params_json=_DEFAULT_PARAMS,
            enabled=False,
            execution_mode="paper",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _token_status(db: AsyncSession, user: User) -> str:
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
        "label":          cfg.label or cfg.entry_time,
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
        "config_id":         str(s.config_id) if s.config_id else None,
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
        "is_active":         is_session_active(s.id),
    }


async def _get_mtm_series(db: AsyncSession, run_id: uuid.UUID):
    rows = (await db.execute(
        select(StrategyRunMtm)
        .where(StrategyRunMtm.run_id == run_id)
        .order_by(StrategyRunMtm.timestamp)
    )).scalars().all()

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


async def _build_slot(db: AsyncSession, cfg: LivePaperConfig, session: Optional[LivePaperSession]) -> Dict:
    """Build the full today-snapshot for one config slot."""
    mtm_series: list = []
    events: list     = []
    run_info         = None

    if session and session.strategy_run_id:
        run = (await db.execute(
            select(StrategyRun).where(StrategyRun.id == session.strategy_run_id)
        )).scalar_one_or_none()
        mtm_series = await _get_mtm_series(db, session.strategy_run_id)
        events     = await _get_events(db, session.strategy_run_id)

        legs = (await db.execute(
            select(StrategyRunLeg)
            .where(StrategyRunLeg.run_id == session.strategy_run_id, StrategyRunLeg.side == "SELL")
        )).scalars().all()
        leg_entry = {l.option_type: float(l.entry_price) for l in legs if l.entry_price}

        if run:
            run_info = {
                "entry_credit_total": float(run.entry_credit_total) if run.entry_credit_total else None,
                "lot_size":           run.lot_size,
                "approved_lots":      run.approved_lots,
                "ce_entry_price":     leg_entry.get("CE"),
                "pe_entry_price":     leg_entry.get("PE"),
            }

    return {
        "config":     _serialize_config(cfg),
        "session":    _serialize_session(session) if session else None,
        "mtm_series": mtm_series,
        "events":     events,
        "run":        run_info,
    }


# ── Multi-config REST endpoints ───────────────────────────────────────────────

@router.get("/configs")
async def list_configs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """List all config slots for the user."""
    await _get_or_create_config(db, user)   # ensure at least one exists
    rows = (await db.execute(
        select(LivePaperConfig)
        .where(LivePaperConfig.user_id == user.id)
        .order_by(LivePaperConfig.created_at)
    )).scalars().all()
    return [_serialize_config(r) for r in rows]


@router.post("/configs")
async def create_config(
    body: ConfigCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Add a new config slot (clone defaults, override with body fields)."""
    # Base on existing first config defaults if available
    base = (await db.execute(
        select(LivePaperConfig).where(LivePaperConfig.user_id == user.id).limit(1)
    )).scalar_one_or_none()

    params = dict(base.params_json if base else _DEFAULT_PARAMS)
    if body.params:
        params.update(body.params)

    cfg = LivePaperConfig(
        user_id=user.id,
        label=body.label or (body.entry_time or "New slot"),
        strategy_id=body.strategy_id or (base.strategy_id if base else "short_straddle_dual_lock"),
        instrument=body.instrument or (base.instrument if base else "NIFTY"),
        capital=body.capital or (float(base.capital) if base else 2_500_000),
        entry_time=body.entry_time or "10:15",
        params_json=params,
        enabled=body.enabled if body.enabled is not None else False,
        execution_mode=body.execution_mode or "paper",
    )
    db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    return _serialize_config(cfg)


@router.put("/configs/{config_id}")
async def update_config_slot(
    config_id: str,
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    cfg = (await db.execute(
        select(LivePaperConfig).where(
            LivePaperConfig.id == uuid.UUID(config_id),
            LivePaperConfig.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found.")

    if body.execution_mode == "live":
        raise HTTPException(status_code=422, detail="execution_mode='live' is not yet enabled.")

    if body.label          is not None: cfg.label          = body.label
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


@router.delete("/configs/{config_id}")
async def delete_config_slot(
    config_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    cfg = (await db.execute(
        select(LivePaperConfig).where(
            LivePaperConfig.id == uuid.UUID(config_id),
            LivePaperConfig.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found.")

    total = (await db.execute(
        select(LivePaperConfig).where(LivePaperConfig.user_id == user.id)
    )).scalars().all()
    if len(total) <= 1:
        raise HTTPException(status_code=409, detail="Cannot delete the last config slot.")

    # Block deletion if this slot has a non-terminal session today — the engine
    # task would keep running but the session would disappear from /today and
    # check_and_resume_sessions would silently skip it on next restart.
    _TERMINAL = {"exited", "no_trade", "error"}
    active_session = (await db.execute(
        select(LivePaperSession).where(
            LivePaperSession.config_id == cfg.id,
            LivePaperSession.trade_date == date.today(),
            LivePaperSession.status.notin_(_TERMINAL),
        )
    )).scalar_one_or_none()
    if active_session:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete slot while today's session is {active_session.status}. Stop it first.",
        )

    await db.delete(cfg)
    await db.commit()
    return {"detail": "Config deleted."}


# ── Today snapshot (all slots) ────────────────────────────────────────────────

@router.get("/today")
async def get_today(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Full snapshot for all config slots today.
    Returns:
      {
        slots: [ { config, session, mtm_series, events, run }, ... ],
        token_status: "valid"|"expired"|"missing",
      }
    """
    await _get_or_create_config(db, user)
    configs = (await db.execute(
        select(LivePaperConfig)
        .where(LivePaperConfig.user_id == user.id)
        .order_by(LivePaperConfig.created_at)
    )).scalars().all()

    today_sessions = await get_sessions_for_date(db, date.today(), user_id=user.id)
    session_by_config = {str(s.config_id): s for s in today_sessions}
    tk_st = await _token_status(db, user)

    slots = []
    for cfg in configs:
        session = session_by_config.get(str(cfg.id))
        slots.append(await _build_slot(db, cfg, session))

    return {
        "slots":        slots,
        "token_status": tk_st,
    }


@router.get("/history")
async def get_history(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    rows = (await db.execute(
        select(LivePaperSession)
        .where(LivePaperSession.user_id == user.id)
        .order_by(LivePaperSession.trade_date.desc(), LivePaperSession.created_at.desc())
        .limit(limit)
        .offset(offset)
    )).scalars().all()
    return [_serialize_session(r) for r in rows]


# ── Start / Stop ──────────────────────────────────────────────────────────────

@router.post("/start")
async def manual_start(
    body: StartBody = StartBody(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Start one specific slot (body.config_id) or all enabled slots."""
    cid = uuid.UUID(body.config_id) if body.config_id else None
    # A specific config_id is always started regardless of its enabled flag
    # (manual override). No config_id → start only auto-enabled slots.
    error = await start_live_session(
        db, user_id=user.id,
        require_enabled=(cid is None),
        config_id=cid,
    )
    if error == "no_config":
        raise HTTPException(status_code=404, detail="No live paper config found.")
    if error == "no_token":
        raise HTTPException(status_code=409, detail="No valid Zerodha token. Please connect Zerodha first.")
    if error == "session_exists":
        raise HTTPException(status_code=409, detail="Session already exists for today.")
    return {"detail": "Session(s) started."}


@router.post("/stop")
async def manual_stop(
    body: StopBody = StopBody(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Stop one session (body.session_id) or all today's active sessions."""
    if body.session_id:
        sessions = (await db.execute(
            select(LivePaperSession).where(
                LivePaperSession.id == uuid.UUID(body.session_id),
                LivePaperSession.user_id == user.id,
            )
        )).scalars().all()
    else:
        sessions = await get_sessions_for_date(db, date.today(), user_id=user.id)

    if not sessions:
        raise HTTPException(status_code=404, detail="No session(s) found.")

    stopped = 0
    for session in sessions:
        if session.status not in ("exited", "no_trade", "error", "stop_requested"):
            await db.execute(
                update(LivePaperSession)
                .where(LivePaperSession.id == session.id)
                .values(status="stop_requested")
            )
            stopped += 1
    await db.commit()
    return {"detail": f"Stop signal sent to {stopped} session(s)."}


# ── Backward-compat single-config endpoints ───────────────────────────────────

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
        raise HTTPException(status_code=422, detail="execution_mode='live' is not yet enabled.")
    if body.label          is not None: cfg.label          = body.label
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


# ── SSE stream ────────────────────────────────────────────────────────────────

async def _get_user_from_token_param(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> User:
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
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")
    return user


@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_get_user_from_token_param),
):
    """SSE stream for a specific session (by session_id)."""
    sid = uuid.UUID(session_id)
    session = (await db.execute(
        select(LivePaperSession).where(
            LivePaperSession.id == sid,
            LivePaperSession.user_id == user.id,
        )
    )).scalar_one_or_none()

    if not session:
        async def _empty():
            yield 'data: {"type": "NO_SESSION"}\n\n'
        return StreamingResponse(_empty(), media_type="text/event-stream")

    q = get_or_create_sse_queue(session.id)

    async def _event_stream():
        snap = _serialize_session(session)
        yield f"data: {json.dumps({'type': 'SNAPSHOT', 'session': snap})}\n\n"
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=25)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("type") == "DONE":
                    break
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.get("/today/stream")
async def stream_today(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_get_user_from_token_param),
):
    """Backward-compat SSE stream — attaches to the first active session today."""
    sessions = await get_sessions_for_date(db, date.today(), user_id=user.id)
    active = next((s for s in sessions if is_session_active(s.id)), None) or \
             (sessions[0] if sessions else None)

    if not active:
        async def _empty():
            yield 'data: {"type": "NO_SESSION"}\n\n'
        return StreamingResponse(_empty(), media_type="text/event-stream")

    q = get_or_create_sse_queue(active.id)

    async def _event_stream():
        snap = _serialize_session(active)
        yield f"data: {json.dumps({'type': 'SNAPSHOT', 'session': snap})}\n\n"
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=25)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("type") == "DONE":
                    break
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")

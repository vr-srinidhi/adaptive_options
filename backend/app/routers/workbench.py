from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models.historical import SessionBatch, TradingDay
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
from app.services.strategy_config import STRATEGY_CONFIG as _CFG
from app.services.token_store import get_broker_token, store_broker_token
from app.services.workbench_catalog import get_strategy, list_strategies, supported_strategy_ids
from app.services.workbench_views import (
    historical_batch_library_item,
    paper_session_library_item,
    parse_compare_refs,
    replay_payload,
    serialize_strategy_metrics,
)
from app.services.zerodha_client import DataUnavailableError

router = APIRouter(prefix="/api/v2", tags=["workbench-v2"])


class CreateRunRequest(BaseModel):
    run_type: str = Field(..., pattern="^(paper_replay|historical_backtest)$")
    strategy_id: str
    config: dict[str, Any]


def _strategy_snapshot(instrument: str, capital: float) -> dict[str, Any]:
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
        "option_price_source": "ltp",
    }


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run ID.")


async def _owned_paper_session(session_id: uuid.UUID, user: User, db: AsyncSession, *, session_type: Optional[str] = None) -> PaperSession:
    q = select(PaperSession).where(
        PaperSession.id == session_id,
        (PaperSession.user_id == user.id) | (PaperSession.user_id.is_(None)),
    )
    if session_type:
        q = q.where(PaperSession.session_type == session_type)
    session = (await db.execute(q)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return session


async def _owned_batch(batch_id: uuid.UUID, user: User, db: AsyncSession) -> SessionBatch:
    batch = (await db.execute(
        select(SessionBatch).where(
            SessionBatch.id == batch_id,
            (SessionBatch.created_by == user.id) | (SessionBatch.created_by.is_(None)),
        )
    )).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return batch


async def _build_workspace_summary(user: User, db: AsyncSession) -> dict:
    paper_count = (await db.execute(
        select(func.count()).select_from(PaperSession).where(
            PaperSession.session_type == "paper_replay",
            (PaperSession.user_id == user.id) | (PaperSession.user_id.is_(None)),
        )
    )).scalar_one()
    historical_batch_count = (await db.execute(
        select(func.count()).select_from(SessionBatch).where(
            (SessionBatch.created_by == user.id) | (SessionBatch.created_by.is_(None)),
        )
    )).scalar_one()
    historical_session_count = (await db.execute(
        select(func.count()).select_from(PaperSession).where(
            PaperSession.session_type == "historical_backtest",
            (PaperSession.user_id == user.id) | (PaperSession.user_id.is_(None)),
        )
    )).scalar_one()

    trading_days = (await db.execute(
        select(TradingDay).order_by(TradingDay.trade_date.desc()).limit(365)
    )).scalars().all()

    paper_rows = (await db.execute(
        select(PaperSession)
        .where(
            PaperSession.session_type == "paper_replay",
            (PaperSession.user_id == user.id) | (PaperSession.user_id.is_(None)),
        )
        .order_by(PaperSession.created_at.desc())
        .limit(4)
    )).scalars().all()
    paper_trades = {}
    if paper_rows:
        trade_rows = (await db.execute(
            select(PaperTradeHeader).where(PaperTradeHeader.session_id.in_([row.id for row in paper_rows]))
        )).scalars().all()
        paper_trades = {trade.session_id: trade for trade in trade_rows}

    batch_rows = (await db.execute(
        select(SessionBatch)
        .where((SessionBatch.created_by == user.id) | (SessionBatch.created_by.is_(None)))
        .order_by(SessionBatch.created_at.desc())
        .limit(4)
    )).scalars().all()

    recent_runs = [
        *[paper_session_library_item(row, paper_trades.get(row.id)) for row in paper_rows],
        *[historical_batch_library_item(row) for row in batch_rows],
    ]
    recent_runs.sort(key=lambda item: item.get("created_at") or "", reverse=True)

    strategies = list_strategies()
    readiness = serialize_strategy_metrics(trading_days)

    return {
        "metrics": {
            "available_strategies": sum(1 for item in strategies if item["status"] == "available"),
            "planned_strategies": sum(1 for item in strategies if item["status"] != "available"),
            "paper_sessions": int(paper_count or 0),
            "historical_batches": int(historical_batch_count or 0),
            "historical_sessions": int(historical_session_count or 0),
            "ready_trading_days": readiness["ready_days"],
        },
        "data_readiness": readiness,
        "recent_runs": recent_runs[:6],
        "featured_strategies": [item for item in strategies if item["status"] == "available"][:3],
    }


@router.get("/workspace/summary")
async def get_workspace_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await _build_workspace_summary(current_user, db)


@router.get("/strategies")
async def get_strategies():
    return {"strategies": list_strategies()}


@router.get("/strategies/{strategy_id}")
async def get_strategy_detail(strategy_id: str):
    strategy = get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return strategy


@router.get("/runs")
async def list_runs(
    kind: Optional[str] = Query(None, pattern="^(paper_session|historical_batch)$"),
    limit: int = Query(40, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    items = []

    if kind in (None, "paper_session"):
        paper_rows = (await db.execute(
            select(PaperSession)
            .where(
                PaperSession.session_type == "paper_replay",
                (PaperSession.user_id == current_user.id) | (PaperSession.user_id.is_(None)),
            )
            .order_by(PaperSession.created_at.desc())
            .limit(limit)
        )).scalars().all()
        trade_by_session = {}
        if paper_rows:
            trade_rows = (await db.execute(
                select(PaperTradeHeader).where(PaperTradeHeader.session_id.in_([row.id for row in paper_rows]))
            )).scalars().all()
            trade_by_session = {trade.session_id: trade for trade in trade_rows}
        items.extend(paper_session_library_item(row, trade_by_session.get(row.id)) for row in paper_rows)

    if kind in (None, "historical_batch"):
        batch_rows = (await db.execute(
            select(SessionBatch)
            .where((SessionBatch.created_by == current_user.id) | (SessionBatch.created_by.is_(None)))
            .order_by(SessionBatch.created_at.desc())
            .limit(limit)
        )).scalars().all()
        items.extend(historical_batch_library_item(row) for row in batch_rows)

    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"runs": items[:limit]}


@router.post("/runs")
async def create_run(
    body: CreateRunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    strategy = get_strategy(body.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    if body.strategy_id not in supported_strategy_ids():
        raise HTTPException(status_code=422, detail="Strategy is catalogued but not executable yet.")
    if body.run_type not in strategy["modes"]:
        raise HTTPException(status_code=422, detail="This strategy does not support the selected run mode.")

    config = body.config or {}

    if body.run_type == "paper_replay":
        instrument = str(config.get("instrument", "")).strip().upper()
        if instrument not in ("NIFTY", "BANKNIFTY"):
            raise HTTPException(status_code=400, detail="instrument must be NIFTY or BANKNIFTY.")
        try:
            trade_date = date.fromisoformat(str(config.get("date", "")))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        try:
            capital = float(config.get("capital", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Capital must be numeric.")
        if not (50_000 <= capital <= 10_000_000):
            raise HTTPException(status_code=400, detail="Capital must be ₹50,000 – ₹10,000,000.")

        access_token = ""
        request_token = str(config.get("request_token", "") or "").strip()
        if request_token:
            try:
                session_data = await asyncio.get_event_loop().run_in_executor(
                    None,
                    zerodha_client.generate_session,
                    request_token,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Zerodha token exchange failed: {exc}")
            access_token = session_data.get("access_token", "")
            await store_broker_token(db, current_user.id, access_token)
            asyncio.ensure_future(
                log_event(
                    "ZERODHA_CONNECT",
                    user_id=current_user.id,
                    ip_address=request.client.host if request.client else "unknown",
                )
            )
        else:
            access_token = await get_broker_token(db, current_user.id) or ""

        if not access_token or len(access_token) < 10:
            raise HTTPException(
                status_code=400,
                detail="No valid Zerodha token available. Connect Zerodha or provide a request token.",
            )

        session_id = uuid.uuid4()
        paper_session = PaperSession(
            id=session_id,
            instrument=instrument,
            session_date=trade_date,
            capital=capital,
            status="RUNNING",
            user_id=current_user.id,
            session_type="paper_replay",
            execution_mode="interactive",
            source_mode="live_like",
            strategy_config_snapshot={
                "strategy_id": body.strategy_id,
                "strategy_name": strategy["name"],
                "run_type": body.run_type,
                "input": config,
            },
        )
        db.add(paper_session)
        await db.commit()

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                run_paper_engine,
                session_id, trade_date, instrument, capital, access_token,
            )
        except DataUnavailableError as exc:
            paper_session.status = "ERROR"
            paper_session.error_message = str(exc)
            await db.commit()
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            paper_session.status = "ERROR"
            paper_session.error_message = str(exc)
            await db.commit()
            raise HTTPException(status_code=500, detail=f"Engine error: {exc}")

        for decision in result["decisions"]:
            db.add(MinuteDecision(**decision))

        trade_db_id = None
        if result["trade_header"]:
            header_data = {k: v for k, v in result["trade_header"].items() if k != "id"}
            header = PaperTradeHeader(**header_data)
            db.add(header)
            await db.flush()
            trade_db_id = header.id
            await db.commit()
            for leg in result["trade_legs"]:
                db.add(PaperTradeLeg(**{**leg, "trade_id": trade_db_id}))
            for mark in result["minute_marks"]:
                db.add(PaperTradeMinuteMark(**{**mark, "trade_id": trade_db_id}))
            await db.commit()
        else:
            await db.commit()

        for candle in result.get("candle_series", []):
            db.add(PaperCandleSeries(**candle))
        await db.commit()

        paper_session.status = "COMPLETED"
        paper_session.decision_count = len(result["decisions"])
        paper_session.final_session_state = result.get("final_session_state")
        if result["trade_header"] and result["trade_header"].get("realized_net_pnl") is not None:
            paper_session.summary_pnl = result["trade_header"]["realized_net_pnl"]
        await db.commit()
        await db.refresh(paper_session)

        trade = None
        if trade_db_id:
            trade = (await db.execute(select(PaperTradeHeader).where(PaperTradeHeader.id == trade_db_id))).scalar_one_or_none()
        return {
            "run": paper_session_library_item(paper_session, trade),
            "navigate_to": f"/workbench/replay/paper_session/{session_id}",
        }

    instrument = str(config.get("instrument", "NIFTY")).strip().upper()
    if instrument not in ("NIFTY", "BANKNIFTY"):
        raise HTTPException(status_code=400, detail="instrument must be NIFTY or BANKNIFTY.")
    try:
        start_date = date.fromisoformat(str(config.get("start_date", "")))
        end_date = date.fromisoformat(str(config.get("end_date", "")))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start or end date. Use YYYY-MM-DD.")
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be ≤ end_date")

    try:
        capital = float(config.get("capital", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Capital must be numeric.")
    if not (50_000 <= capital <= 10_000_000):
        raise HTTPException(status_code=400, detail="Capital must be ₹50,000 – ₹10,000,000.")
    name = str(config.get("name", "")).strip() or "ORB historical replay"
    execution_order = str(config.get("execution_order", "latest_first"))
    autorun = bool(config.get("autorun", True))
    snapshot = _strategy_snapshot(instrument, capital)

    batch = SessionBatch(
        name=name,
        strategy_id=snapshot["strategy_id"],
        strategy_version=snapshot.get("strategy_version"),
        strategy_config_snapshot=snapshot,
        start_date=start_date,
        end_date=end_date,
        execution_order=execution_order,
        status="queued" if autorun else "draft",
        created_by=current_user.id,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)

    if autorun:
        from app.services.batch_runner import run_batch

        background_tasks.add_task(run_batch, batch.id)

    return {
        "run": historical_batch_library_item(batch),
        "navigate_to": f"/workbench/history/historical_batch/{batch.id}",
    }


@router.get("/runs/{kind}/{item_id}")
async def get_run_detail(
    kind: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if kind == "paper_session":
        session = await _owned_paper_session(_parse_uuid(item_id), current_user, db, session_type="paper_replay")
        trade = (await db.execute(
            select(PaperTradeHeader).where(PaperTradeHeader.session_id == session.id)
        )).scalar_one_or_none()
        return {"run": paper_session_library_item(session, trade)}

    if kind == "historical_batch":
        batch = await _owned_batch(_parse_uuid(item_id), current_user, db)
        sessions = (await db.execute(
            select(PaperSession)
            .where(PaperSession.batch_id == batch.id)
            .order_by(PaperSession.session_date.desc())
        )).scalars().all()
        wins = sum(1 for row in sessions if (row.summary_pnl or 0) > 0)
        return {
            "run": historical_batch_library_item(batch, sessions_total=len(sessions), winning_sessions=wins),
            "sessions": [
                {
                    "id": str(row.id),
                    "session_date": row.session_date.isoformat(),
                    "status": row.status,
                    "final_session_state": row.final_session_state,
                    "summary_pnl": float(row.summary_pnl) if row.summary_pnl is not None else None,
                    "decision_count": row.decision_count,
                    "route": f"/workbench/replay/historical_session/{row.id}",
                    "legacy_route": f"/backtests/sessions/{row.id}",
                }
                for row in sessions
            ],
        }

    if kind == "historical_session":
        session = await _owned_paper_session(_parse_uuid(item_id), current_user, db, session_type="historical_backtest")
        return {
            "run": {
                "kind": "historical_session",
                "id": str(session.id),
                "title": f"{session.instrument} historical session",
                "subtitle": session.session_date.isoformat(),
                "status": session.status,
                "strategy_id": (session.strategy_config_snapshot or {}).get("strategy_id") or "orb_intraday_spread",
                "strategy_name": "Opening Range Spread",
                "run_mode": "historical_session",
                "instrument": session.instrument,
                "date_label": session.session_date.isoformat(),
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "pnl": float(session.summary_pnl) if session.summary_pnl is not None else None,
                "summary": session.final_session_state or session.status,
                "metrics": {
                    "decision_count": session.decision_count,
                    "capital": float(session.capital),
                    "batch_id": str(session.batch_id) if session.batch_id else None,
                },
                "route": f"/workbench/replay/historical_session/{session.id}",
                "legacy_route": f"/backtests/sessions/{session.id}",
            }
        }

    raise HTTPException(status_code=404, detail="Unsupported run kind.")


@router.get("/runs/{kind}/{item_id}/replay")
async def get_run_replay(
    kind: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if kind not in {"paper_session", "historical_session"}:
        raise HTTPException(status_code=404, detail="Replay is only available for session-level runs.")

    session_type = "paper_replay" if kind == "paper_session" else "historical_backtest"
    session = await _owned_paper_session(_parse_uuid(item_id), current_user, db, session_type=session_type)

    decisions = (await db.execute(
        select(MinuteDecision).where(MinuteDecision.session_id == session.id).order_by(MinuteDecision.timestamp)
    )).scalars().all()
    trade = (await db.execute(
        select(PaperTradeHeader).where(PaperTradeHeader.session_id == session.id)
    )).scalar_one_or_none()

    legs = []
    marks = []
    if trade is not None:
        legs = (await db.execute(
            select(PaperTradeLeg).where(PaperTradeLeg.trade_id == trade.id).order_by(PaperTradeLeg.leg_side)
        )).scalars().all()
        marks = (await db.execute(
            select(PaperTradeMinuteMark).where(PaperTradeMinuteMark.trade_id == trade.id).order_by(PaperTradeMinuteMark.timestamp)
        )).scalars().all()

    candle_series = []
    if kind == "paper_session":
        candle_series = (await db.execute(
            select(PaperCandleSeries).where(PaperCandleSeries.session_id == session.id).order_by(PaperCandleSeries.series_type)
        )).scalars().all()

    return replay_payload(
        kind=kind,
        session=session,
        trade=trade,
        decisions=decisions,
        marks=marks,
        candle_series=candle_series,
        legs=legs,
    )


@router.get("/runs/compare")
async def compare_runs(
    refs: Optional[str] = Query(None, description="Comma-separated refs like paper_session:uuid,historical_batch:uuid"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        parsed = parse_compare_refs(refs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not parsed:
        raise HTTPException(status_code=400, detail="Provide at least one compare ref.")
    if len(parsed) > 4:
        raise HTTPException(status_code=400, detail="Compare supports up to 4 runs at a time.")

    comparisons = []
    for kind, raw_id in parsed:
        if kind == "paper_session":
            session = await _owned_paper_session(_parse_uuid(raw_id), current_user, db, session_type="paper_replay")
            trade = (await db.execute(
                select(PaperTradeHeader).where(PaperTradeHeader.session_id == session.id)
            )).scalar_one_or_none()
            comparisons.append(paper_session_library_item(session, trade))
        elif kind == "historical_batch":
            batch = await _owned_batch(_parse_uuid(raw_id), current_user, db)
            sessions = (await db.execute(
                select(PaperSession).where(PaperSession.batch_id == batch.id)
            )).scalars().all()
            wins = sum(1 for row in sessions if (row.summary_pnl or 0) > 0)
            comparisons.append(historical_batch_library_item(batch, sessions_total=len(sessions), winning_sessions=wins))
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported compare kind: {kind}")

    return {"items": comparisons}

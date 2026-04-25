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
from app.models.historical import OptionsCandle, SessionBatch, SpotCandle, TradingDay
from app.services.charges_service import compute_entry_charges, compute_exit_charges_estimate
from app.models.paper_trade import (
    MinuteDecision,
    PaperCandleSeries,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.models.strategy_run import (
    StrategyLegMtm,
    StrategyRun,
    StrategyRunEvent,
    StrategyRunLeg,
    StrategyRunMtm,
)
from app.models.user import User
from app.services import zerodha_client
from app.services.audit import log_event
from app.services.generic_executor import execute_run, validate_run
from app.services.paper_engine import run_paper_engine
from app.services.strategy_config import (
    WORKBENCH_STRATEGY_ID,
    WORKBENCH_STRATEGY_NAME,
    build_strategy_snapshot,
)
from app.services.strategy_replay_serializer import (
    strategy_run_library_item,
    strategy_run_replay_payload,
)
from app.services.token_store import get_broker_token, store_broker_token
from app.services.workbench_catalog import get_strategy, list_strategies, supported_strategy_ids
from app.services.workbench_views import (
    historical_batch_library_item,
    paper_session_library_item,
    parse_compare_refs,
    replay_payload,
    resolve_strategy_identity,
    serialize_strategy_metrics,
)
from app.services.zerodha_client import DataUnavailableError

router = APIRouter(prefix="/api/v2", tags=["workbench-v2"])


class CreateRunRequest(BaseModel):
    run_type: str = Field(..., pattern="^(paper_replay|historical_backtest|single_session_backtest)$")
    strategy_id: str
    config: dict[str, Any]


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
    # Catalog metadata is intentionally public so the workbench shell can render
    # before login; run creation and replay data remain authenticated.
    return {"strategies": list_strategies()}


@router.get("/strategies/{strategy_id}")
async def get_strategy_detail(strategy_id: str):
    # Same rationale as /strategies: metadata is public, execution data is not.
    strategy = get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return strategy


@router.get("/runs")
async def list_runs(
    kind: Optional[str] = Query(None, pattern="^(paper_session|historical_batch|strategy_run)$"),
    limit: int = Query(40, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    items = []
    fetch_limit = limit + offset

    if kind in (None, "paper_session"):
        paper_query = (
            select(PaperSession)
            .where(
                PaperSession.session_type == "paper_replay",
                (PaperSession.user_id == current_user.id) | (PaperSession.user_id.is_(None)),
            )
            .order_by(PaperSession.created_at.desc())
        )
        if kind == "paper_session":
            paper_query = paper_query.offset(offset).limit(limit)
        else:
            paper_query = paper_query.limit(fetch_limit)
        paper_rows = (await db.execute(paper_query)).scalars().all()
        trade_by_session = {}
        if paper_rows:
            trade_rows = (await db.execute(
                select(PaperTradeHeader).where(PaperTradeHeader.session_id.in_([row.id for row in paper_rows]))
            )).scalars().all()
            trade_by_session = {trade.session_id: trade for trade in trade_rows}
        items.extend(paper_session_library_item(row, trade_by_session.get(row.id)) for row in paper_rows)

    if kind in (None, "historical_batch"):
        batch_query = (
            select(SessionBatch)
            .where((SessionBatch.created_by == current_user.id) | (SessionBatch.created_by.is_(None)))
            .order_by(SessionBatch.created_at.desc())
        )
        if kind == "historical_batch":
            batch_query = batch_query.offset(offset).limit(limit)
        else:
            batch_query = batch_query.limit(fetch_limit)
        batch_rows = (await db.execute(batch_query)).scalars().all()
        items.extend(historical_batch_library_item(row) for row in batch_rows)

    if kind in (None, "strategy_run"):
        sr_query = (
            select(StrategyRun)
            .where(StrategyRun.user_id == current_user.id)
            .order_by(StrategyRun.trade_date.desc(), StrategyRun.created_at.desc())
        )
        if kind == "strategy_run":
            sr_query = sr_query.offset(offset).limit(limit)
        else:
            sr_query = sr_query.limit(fetch_limit)
        sr_rows = (await db.execute(sr_query)).scalars().all()
        items.extend(strategy_run_library_item(row) for row in sr_rows)

    items.sort(key=lambda item: item.get("date_label") or item.get("created_at") or "", reverse=True)
    if kind is None:
        items = items[offset:offset + limit]
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
                session_data = await asyncio.to_thread(zerodha_client.generate_session, request_token)
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Zerodha token exchange failed: {exc}")
            access_token = session_data.get("access_token", "")
            await store_broker_token(db, current_user.id, access_token)
            background_tasks.add_task(
                log_event,
                "ZERODHA_CONNECT",
                user_id=current_user.id,
                ip_address=request.client.host if request.client else "unknown",
            )
        else:
            access_token = await get_broker_token(db, current_user.id) or ""

        if not access_token or len(access_token) < 10:
            raise HTTPException(
                status_code=400,
                detail="No valid Zerodha token available. Connect Zerodha or provide a request token.",
            )

        session_id = uuid.uuid4()
        session_snapshot = build_strategy_snapshot(
            instrument,
            capital,
            strategy_id=body.strategy_id,
            strategy_name=strategy["name"],
            run_type=body.run_type,
            input_config=config,
        )
        session_kwargs = {
            "id": session_id,
            "instrument": instrument,
            "session_date": trade_date,
            "capital": capital,
            "status": "RUNNING",
            "user_id": current_user.id,
            "session_type": "paper_replay",
            "execution_mode": "interactive",
            "source_mode": "live_like",
            "strategy_config_snapshot": session_snapshot,
        }
        paper_session = PaperSession(**session_kwargs)
        db.add(paper_session)
        await db.flush()

        try:
            result = await asyncio.to_thread(run_paper_engine, session_id, trade_date, instrument, capital, access_token)
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

        trade = None
        try:
            for decision in result["decisions"]:
                db.add(MinuteDecision(**decision))

            if result["trade_header"]:
                header_data = {k: v for k, v in result["trade_header"].items() if k != "id"}
                trade = PaperTradeHeader(**header_data)
                db.add(trade)
                await db.flush()
                for leg in result["trade_legs"]:
                    db.add(PaperTradeLeg(**{**leg, "trade_id": trade.id}))
                for mark in result["minute_marks"]:
                    db.add(PaperTradeMinuteMark(**{**mark, "trade_id": trade.id}))

            for candle in result.get("candle_series", []):
                db.add(PaperCandleSeries(**candle))

            paper_session.status = "COMPLETED"
            paper_session.decision_count = len(result["decisions"])
            paper_session.final_session_state = result.get("final_session_state")
            if result["trade_header"] and result["trade_header"].get("realized_net_pnl") is not None:
                paper_session.summary_pnl = result["trade_header"]["realized_net_pnl"]
            await db.commit()
        except Exception as exc:
            await db.rollback()
            error_session = PaperSession(
                **{
                    **session_kwargs,
                    "status": "ERROR",
                    "error_message": f"Persistence error: {exc}",
                }
            )
            db.add(error_session)
            await db.commit()
            raise HTTPException(status_code=500, detail=f"Persistence error: {exc}")

        await db.refresh(paper_session)
        return {
            "run": paper_session_library_item(paper_session, trade),
            "navigate_to": f"/workbench/replay/paper_session/{session_id}",
        }

    if body.run_type == "single_session_backtest":
        validation = await validate_run(db, strategy, config)
        if validation.error:
            raise HTTPException(status_code=422, detail=validation.error)

        run_id = uuid.uuid4()
        result = await execute_run(db, run_id, strategy, config, validation, current_user.id)

        if result.status == "ERROR":
            raise HTTPException(status_code=500, detail=result.exit_reason or "Execution failed.")

        run_row = (await db.execute(
            select(StrategyRun).where(StrategyRun.id == run_id)
        )).scalar_one_or_none()
        if run_row is None:
            raise HTTPException(status_code=500, detail="Run record not found after execution.")

        return {
            "run": strategy_run_library_item(run_row),
            "navigate_to": f"/workbench/replay/strategy_run/{run_id}",
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
    snapshot = build_strategy_snapshot(
        instrument,
        capital,
        strategy_id=body.strategy_id,
        strategy_name=strategy["name"],
        run_type=body.run_type,
        input_config=config,
    )

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


@router.post("/runs/validate")
async def validate_run_endpoint(
    body: CreateRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Dry-run validation: resolves contract, expiry, spot, lots — no DB writes."""
    if body.run_type != "single_session_backtest":
        raise HTTPException(status_code=422, detail="Validation is only supported for single_session_backtest runs.")

    strategy = get_strategy(body.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    if body.strategy_id not in supported_strategy_ids():
        raise HTTPException(status_code=422, detail="Strategy is catalogued but not executable yet.")

    validation = await validate_run(db, strategy, body.config or {})
    if validation.error:
        raise HTTPException(status_code=422, detail=validation.error)

    return {
        "valid": True,
        "instrument": validation.instrument,
        "trade_date": validation.trade_date,
        "entry_time": validation.entry_time,
        "expiry": validation.resolved_expiry,
        "atm_strike": validation.atm_strike,
        "spot_at_entry": validation.spot_at_entry,
        "lot_size": validation.lot_size,
        "approved_lots": validation.approved_lots,
        "estimated_margin": validation.estimated_margin,
        "contracts": validation.contracts or [],
        "warnings": validation.warnings or [],
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
        strategy_id, strategy_name = resolve_strategy_identity(
            session.strategy_config_snapshot,
            fallback_id=WORKBENCH_STRATEGY_ID,
            fallback_name=WORKBENCH_STRATEGY_NAME,
        )
        return {
            "run": {
                "kind": "historical_session",
                "id": str(session.id),
                "title": f"{session.instrument} historical session",
                "subtitle": session.session_date.isoformat(),
                "status": session.status,
                "strategy_id": strategy_id,
                "strategy_name": strategy_name,
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

    if kind == "strategy_run":
        run_id = _parse_uuid(item_id)
        run_row = (await db.execute(
            select(StrategyRun).where(
                StrategyRun.id == run_id,
                StrategyRun.user_id == current_user.id,
            )
        )).scalar_one_or_none()
        if run_row is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return {"run": strategy_run_library_item(run_row)}

    raise HTTPException(status_code=404, detail="Unsupported run kind.")


async def _compute_shadow_mtm(db: AsyncSession, run_row, legs) -> list:
    """
    Hypothetical MTM from exit_time → 15:25 using actual entry prices.
    Same gross-MTM formula as the executor, no stop/target applied.
    """
    from datetime import datetime, time as dt_time

    if not run_row.exit_time or not legs or run_row.status == "no_trade":
        return []

    try:
        h, m = map(int, run_row.exit_time.split(":"))
        exit_dt = datetime.combine(run_row.trade_date, dt_time(h, m))
    except Exception:
        return []

    sq_dt = datetime.combine(run_row.trade_date, dt_time(15, 25))
    if exit_dt >= sq_dt:
        return []

    lot_size      = run_row.lot_size or 0
    approved_lots = run_row.approved_lots or 0
    if lot_size == 0 or approved_lots == 0:
        return []

    leg_info = [
        (l.side, l.option_type, l.strike, l.expiry_date, float(l.entry_price))
        for l in legs if l.entry_price is not None
    ]
    sell_entry_prices = [ep for side, _, _, _, ep in leg_info if side == "SELL"]
    entry_charges = compute_entry_charges(approved_lots, lot_size, sell_entry_prices)

    # Spot candles after exit
    spot_rows = (await db.execute(
        select(SpotCandle)
        .where(
            SpotCandle.symbol == run_row.instrument,
            SpotCandle.trade_date == run_row.trade_date,
            SpotCandle.timestamp > exit_dt,
            SpotCandle.timestamp <= sq_dt,
        )
        .order_by(SpotCandle.timestamp)
    )).scalars().all()

    if not spot_rows:
        return []

    # Option candles after exit, keyed (strike, opt_type, ts) -> price
    option_prices: dict = {}
    for side, opt_type, strike, expiry, _ in leg_info:
        rows = (await db.execute(
            select(OptionsCandle)
            .where(
                OptionsCandle.symbol == run_row.instrument,
                OptionsCandle.trade_date == run_row.trade_date,
                OptionsCandle.expiry_date == expiry,
                OptionsCandle.strike == strike,
                OptionsCandle.option_type == opt_type,
                OptionsCandle.timestamp > exit_dt,
                OptionsCandle.timestamp <= sq_dt,
            )
            .order_by(OptionsCandle.timestamp)
        )).scalars().all()
        for row in rows:
            option_prices[(strike, opt_type, row.timestamp)] = float(row.close)

    shadow: list = []
    last_prices: dict = {}  # carry-forward for stale candles (max 1 min)
    for spot_row in spot_rows:
        ts = spot_row.timestamp
        cur: dict = {}
        for side, opt_type, strike, _, _ in leg_info:
            key = (strike, opt_type)
            p = option_prices.get((strike, opt_type, ts))
            if p is not None:
                cur[key] = p
                last_prices[key] = p
            elif key in last_prices:
                cur[key] = last_prices[key]  # 1-min carry-forward

        if len(cur) < len(leg_info):
            continue  # skip minutes with missing data

        gross_mtm_per_unit = sum(
            (ep - cur[(strike, opt_type)]) if side == "SELL" else (cur[(strike, opt_type)] - ep)
            for side, opt_type, strike, _, ep in leg_info
        )
        gross_mtm_total = gross_mtm_per_unit * lot_size * approved_lots
        sell_cur = [cur[(strike, opt_type)] for side, opt_type, strike, _, _ in leg_info if side == "SELL"]
        est_exit = compute_exit_charges_estimate(approved_lots, lot_size, sell_cur or [0.0])
        net_mtm  = gross_mtm_total - entry_charges - est_exit
        shadow.append({"timestamp": ts.isoformat(), "net_mtm": round(net_mtm, 2)})

    return shadow


@router.get("/runs/{kind}/{item_id}/replay")
async def get_run_replay(
    kind: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if kind == "strategy_run":
        run_id = _parse_uuid(item_id)
        run_row = (await db.execute(
            select(StrategyRun).where(
                StrategyRun.id == run_id,
                StrategyRun.user_id == current_user.id,
            )
        )).scalar_one_or_none()
        if run_row is None:
            raise HTTPException(status_code=404, detail="Run not found.")

        legs = (await db.execute(
            select(StrategyRunLeg)
            .where(StrategyRunLeg.run_id == run_id)
            .order_by(StrategyRunLeg.leg_index)
        )).scalars().all()
        mtm_rows = (await db.execute(
            select(StrategyRunMtm)
            .where(StrategyRunMtm.run_id == run_id)
            .order_by(StrategyRunMtm.timestamp)
        )).scalars().all()
        leg_mtm_rows = (await db.execute(
            select(StrategyLegMtm)
            .where(StrategyLegMtm.run_id == run_id)
            .order_by(StrategyLegMtm.timestamp)
        )).scalars().all()
        events = (await db.execute(
            select(StrategyRunEvent)
            .where(StrategyRunEvent.run_id == run_id)
            .order_by(StrategyRunEvent.timestamp)
        )).scalars().all()

        spot_candles_full = (await db.execute(
            select(SpotCandle)
            .where(
                SpotCandle.symbol == run_row.instrument,
                SpotCandle.trade_date == run_row.trade_date,
            )
            .order_by(SpotCandle.timestamp)
        )).scalars().all()

        shadow_mtm = await _compute_shadow_mtm(db, run_row, legs)

        return strategy_run_replay_payload(run_row, legs, mtm_rows, leg_mtm_rows, events, spot_candles_full, shadow_mtm)

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

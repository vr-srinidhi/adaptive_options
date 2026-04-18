"""
Batch runner — drives historical backtest execution for a SessionBatch.

Runs as a background asyncio task (fire-and-forget from the HTTP request).
Iterates over backtest-ready trading_days in the batch's date range,
calls load_historical_session_data() + run_paper_engine_core() for each,
persists results, and updates the batch progress counters.

Per-session failures are logged and counted; the batch continues regardless.
"""
import asyncio
import logging
import uuid
from datetime import date as date_type, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.historical import SessionBatch, TradingDay
from app.models.paper_trade import (
    MinuteDecision,
    PaperCandleSeries,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.services.historical_market_data import load_historical_session_data
from app.services.opening_range import (
    compute_opening_range,
    generate_bearish_candidates,
    generate_bullish_candidates,
)
from app.services.paper_engine import run_paper_engine_core

log = logging.getLogger(__name__)


# ── Session persistence helper (shared with paper_trading router) ─────────────

async def _persist_session_results(
    db: AsyncSession,
    session_obj: PaperSession,
    result: Dict[str, Any],
    completed_at: datetime,
) -> None:
    """Write engine output rows to DB and mark session COMPLETED."""
    session_id = session_obj.id

    # MinuteDecisions
    for d in result["decisions"]:
        db.add(MinuteDecision(**d))

    # Trade header + legs + minute marks
    trade_header_row: Optional[PaperTradeHeader] = None
    if result["trade_header"]:
        h = result["trade_header"]
        trade_id = uuid.uuid4()
        h["id"] = trade_id
        trade_header_row = PaperTradeHeader(**h)
        db.add(trade_header_row)

        for leg in result.get("trade_legs", []):
            leg["trade_id"] = trade_id
            db.add(PaperTradeLeg(**leg))

        for mark in result.get("minute_marks", []):
            mark["trade_id"] = trade_id
            db.add(PaperTradeMinuteMark(**mark))

    # Candle series
    for cs in result.get("candle_series", []):
        db.add(PaperCandleSeries(**cs))

    # Update session record
    session_obj.status = "COMPLETED"
    session_obj.final_session_state = result.get("final_session_state")
    session_obj.completed_at = completed_at
    session_obj.decision_count = len(result["decisions"])

    # Summary P&L (net P&L from trade header, None if no trade)
    if trade_header_row and trade_header_row.realized_net_pnl is not None:
        session_obj.summary_pnl = trade_header_row.realized_net_pnl
    elif result["trade_header"] and result["trade_header"].get("realized_net_pnl") is not None:
        session_obj.summary_pnl = result["trade_header"]["realized_net_pnl"]

    await db.flush()


# ── Single-day historical run ─────────────────────────────────────────────────

async def run_historical_day(
    db: AsyncSession,
    batch_id: uuid.UUID,
    trade_date: date_type,
    instrument: str,
    capital: float,
    strategy_config_snapshot: Dict,
    user_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """
    Run one historical backtest session for trade_date.

    Returns {"status": "completed"|"skipped"|"failed", "summary_pnl": float|None, ...}
    """
    started_at = datetime.now(tz=timezone.utc)

    # Always create a PaperSession row so the audit trail is complete even for
    # skipped/insufficient-data days. The session status reflects the outcome.
    session_id = uuid.uuid4()
    session_obj = PaperSession(
        id=session_id,
        instrument=instrument,
        session_date=trade_date,
        capital=capital,
        status="RUNNING",
        session_type="historical_backtest",
        batch_id=batch_id,
        execution_mode="batch",
        source_mode="historical_db",
        strategy_config_snapshot=strategy_config_snapshot,
        started_at=started_at,
        user_id=user_id,
    )
    db.add(session_obj)
    await db.flush()

    # ── ORB leg discovery (strategy-specific, done here not in the data layer) ──
    # We need spot candles first to compute OR and derive candidate strikes.
    from app.services.historical_market_data import load_spot_candles
    from app.services.opening_range import OR_WINDOW_MINUTES
    spot_pre = await load_spot_candles(db, instrument, trade_date)
    if len(spot_pre) >= OR_WINDOW_MINUTES:
        or_high, or_low = compute_opening_range(spot_pre)
        legs_to_fetch = set()
        for long_s, short_s in generate_bullish_candidates(or_high):
            legs_to_fetch.add((long_s, "CE"))
            legs_to_fetch.add((short_s, "CE"))
        for long_s, short_s in generate_bearish_candidates(or_low):
            legs_to_fetch.add((long_s, "PE"))
            legs_to_fetch.add((short_s, "PE"))
    else:
        legs_to_fetch = set()

    option_price_source = strategy_config_snapshot.get("option_price_source", "ltp")

    # Load market data — if insufficient, persist a SKIPPED session and return
    data = await load_historical_session_data(
        db, instrument, trade_date, legs_to_fetch, option_price_source
    )
    if data is None:
        log.warning("Skipping %s — insufficient historical data", trade_date)
        session_obj.status = "SKIPPED"
        session_obj.final_session_state = "INSUFFICIENT_DATA"
        session_obj.error_message = "Insufficient historical data for this date"
        session_obj.completed_at = datetime.now(tz=timezone.utc)
        await db.commit()
        return {"status": "skipped", "trade_date": str(trade_date), "summary_pnl": None,
                "reason": "insufficient_data", "session_id": str(session_id)}

    try:
        result = run_paper_engine_core(
            session_id=session_id,
            trade_date=trade_date,
            instrument=instrument,
            capital=capital,
            spot_candles=data["spot_candles"],
            option_market_index=data["option_market_index"],
            option_candles_raw=data["option_candles_raw"],
            expiry=data["expiry"],
            lot_size=data["lot_size"],
            legs_to_fetch=data["legs_to_fetch"],
            monthly_expiry=data.get("monthly_expiry"),
        )

        completed_at = datetime.now(tz=timezone.utc)
        await _persist_session_results(db, session_obj, result, completed_at)
        await db.commit()

        return {
            "status": "completed",
            "trade_date": str(trade_date),
            "summary_pnl": float(session_obj.summary_pnl) if session_obj.summary_pnl is not None else None,
            "final_session_state": result.get("final_session_state"),
            "session_id": str(session_id),
        }

    except Exception as exc:
        session_obj.status = "ERROR"
        session_obj.error_message = str(exc)[:500]
        await db.commit()
        log.exception("Historical session failed for %s: %s", trade_date, exc)
        return {"status": "failed", "trade_date": str(trade_date), "summary_pnl": None,
                "error": str(exc)[:200]}


# ── Batch execution ───────────────────────────────────────────────────────────

async def run_batch(batch_id: uuid.UUID) -> None:
    """
    Background task: execute all sessions in a batch.

    Imports AsyncSessionLocal here to avoid circular imports.
    Each session uses its own DB session to keep transactions small.
    """
    from app.database import AsyncSessionLocal as _SessionLocal  # deferred

    log.info("Starting batch %s", batch_id)

    async with _SessionLocal() as db:
        result = await db.execute(
            select(SessionBatch).where(SessionBatch.id == batch_id)
        )
        batch: Optional[SessionBatch] = result.scalar_one_or_none()
        if batch is None:
            log.error("Batch %s not found", batch_id)
            return
        if batch.status not in ("queued", "draft"):
            log.warning("Batch %s already in status %s — skipping", batch_id, batch.status)
            return

        # Collect backtest-ready trading days in date range
        td_result = await db.execute(
            select(TradingDay)
            .where(
                TradingDay.backtest_ready.is_(True),
                TradingDay.trade_date >= batch.start_date,
                TradingDay.trade_date <= batch.end_date,
            )
            .order_by(
                TradingDay.trade_date.desc()
                if batch.execution_order == "latest_first"
                else TradingDay.trade_date.asc()
            )
        )
        trading_days: List[TradingDay] = td_result.scalars().all()

        batch.status = "running"
        batch.total_sessions = len(trading_days)
        await db.commit()

    if not trading_days:
        async with _SessionLocal() as db:
            result = await db.execute(select(SessionBatch).where(SessionBatch.id == batch_id))
            batch = result.scalar_one_or_none()
            if batch:
                batch.status = "completed"
                await db.commit()
        log.warning("Batch %s: no backtest-ready days in range", batch_id)
        return

    # Re-read config once (safe to hold in memory)
    async with _SessionLocal() as db:
        result = await db.execute(select(SessionBatch).where(SessionBatch.id == batch_id))
        batch = result.scalar_one_or_none()
        strategy_config = batch.strategy_config_snapshot
        instrument = strategy_config.get("instrument", "NIFTY")
        capital = float(strategy_config.get("capital", 2_500_000))
        user_id = batch.created_by

    completed = failed = skipped = 0
    total_pnl = 0.0

    for td in trading_days:
        async with _SessionLocal() as db:
            summary = await run_historical_day(
                db=db,
                batch_id=batch_id,
                trade_date=td.trade_date,
                instrument=instrument,
                capital=capital,
                strategy_config_snapshot=strategy_config,
                user_id=user_id,
            )

        if summary["status"] == "completed":
            completed += 1
            if summary["summary_pnl"] is not None:
                total_pnl += summary["summary_pnl"]
        elif summary["status"] == "skipped":
            skipped += 1
        else:
            failed += 1

        # Update batch progress after every session
        async with _SessionLocal() as db:
            result = await db.execute(select(SessionBatch).where(SessionBatch.id == batch_id))
            batch = result.scalar_one_or_none()
            if batch:
                batch.completed_sessions = completed
                batch.failed_sessions = failed
                batch.skipped_sessions = skipped
                batch.total_pnl = total_pnl
                await db.commit()

        log.info(
            "Batch %s progress: %d/%d done (fail=%d skip=%d pnl=%.2f)",
            batch_id, completed + failed + skipped, len(trading_days),
            failed, skipped, total_pnl,
        )

    # Final batch status
    async with _SessionLocal() as db:
        result = await db.execute(select(SessionBatch).where(SessionBatch.id == batch_id))
        batch = result.scalar_one_or_none()
        if batch:
            batch.status = (
                "completed" if failed == 0
                else "completed_with_warnings"
            )
            await db.commit()

    log.info(
        "Batch %s finished: completed=%d failed=%d skipped=%d total_pnl=%.2f",
        batch_id, completed, failed, skipped, total_pnl,
    )

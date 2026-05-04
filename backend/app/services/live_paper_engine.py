"""
Live Paper Trading Engine

Runs entirely server-side as an asyncio background task (no UI involvement).
Strategy: Short Straddle with Dual Lock (straddle_adjustment_v1 logic).

Flow
----
09:14  Fetch Zerodha instruments master; create StrategyRun row (status in_progress);
       update LivePaperSession status → waiting.
09:14–09:49  Poll spot every minute; record to waiting_spot_json.
09:49  Resolve ATM strike, expiry, option symbols.
09:50+  Fetch live CE/PE prices each minute; execute dual-lock strategy logic;
        write StrategyRunMtm + events incrementally; broadcast via SSE queue.
Exit   Finalize StrategyRun; update LivePaperSession status → exited/no_trade/error.

Recovery: if the process restarts mid-day, scheduler.py calls
check_and_resume_sessions() on startup which re-launches interrupted sessions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from asyncio import Queue
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models.live_paper import LivePaperConfig, LivePaperSession
from app.models.strategy_run import (
    StrategyLegMtm, StrategyRun, StrategyRunEvent, StrategyRunLeg, StrategyRunMtm,
)
from app.services.charges_service import (
    compute_leg_entry_charges,
    compute_leg_exit_charges_estimate,
    compute_leg_total_charges,
)
from app.services.contract_spec_service import get_contract_spec, resolve_atm_strike
from app.services.token_store import get_broker_token
from app.services.zerodha_client import (
    SPOT_SYMBOLS, fetch_live_quote, find_option_symbol,
    get_instruments_with_token,
)

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

_SESSION_START = time(9, 14)
_SESSION_END   = time(15, 30)
_RESOLVE_TIME  = time(9, 49)    # resolve ATM strike from live spot at this minute
_SQ_TIME       = time(15, 25)   # default time exit

# ── SSE broadcast layer ───────────────────────────────────────────────────────
# One asyncio.Queue per live session.  The SSE endpoint subscribes; the engine
# publishes.  If no subscriber is connected, messages are silently discarded.

_sse_queues: Dict[uuid.UUID, Queue] = {}
_active_session_id: Optional[uuid.UUID] = None   # currently running session


def get_or_create_sse_queue(session_id: uuid.UUID) -> Queue:
    if session_id not in _sse_queues:
        _sse_queues[session_id] = Queue(maxsize=200)
    return _sse_queues[session_id]


def get_active_session_id() -> Optional[uuid.UUID]:
    return _active_session_id


async def _broadcast(session_id: uuid.UUID, data: Dict) -> None:
    q = _sse_queues.get(session_id)
    if q:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass  # no listener or listener is slow — drop the message


# ── Startup helpers ───────────────────────────────────────────────────────────

async def get_active_config(db, user_id=None) -> Optional[LivePaperConfig]:
    """Return the enabled config for the given user (or global first if no user_id)."""
    q = select(LivePaperConfig).where(LivePaperConfig.enabled.is_(True))
    if user_id is not None:
        q = q.where(LivePaperConfig.user_id == user_id)
    return (await db.execute(q.limit(1))).scalar_one_or_none()


async def get_session_for_date(db, trade_date: date, user_id=None) -> Optional[LivePaperSession]:
    q = select(LivePaperSession).where(LivePaperSession.trade_date == trade_date)
    if user_id is not None:
        q = q.where(LivePaperSession.user_id == user_id)
    return (await db.execute(q.limit(1))).scalar_one_or_none()


async def start_live_session(db, user_id=None) -> None:
    """
    Called by the scheduler at 09:14 (or manually via /start endpoint).
    Creates a LivePaperSession and launches the background engine task.
    No-op if:
      - no enabled config found for the user
      - session already exists for today
      - no valid Zerodha token
    """
    config = await get_active_config(db, user_id=user_id)
    if not config:
        log.info("Live paper: no enabled config for user=%s, skipping today.", user_id)
        return

    today = date.today()
    existing = await get_session_for_date(db, today, user_id=config.user_id)
    if existing:
        log.info("Live paper: session already exists for %s (status=%s).", today, existing.status)
        return

    # Validate token freshness (Zerodha tokens expire at 6 AM IST daily)
    if config.user_id:
        access_token = await get_broker_token(db, config.user_id)
    else:
        from app.services.zerodha_client import get_access_token
        access_token = get_access_token()

    if not access_token:
        log.warning("Live paper: no Zerodha token — skipping session for %s.", today)
        return

    session = LivePaperSession(
        config_id=config.id,
        user_id=config.user_id,
        trade_date=today,
        status="scheduled",
        waiting_spot_json=[],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    log.info("Live paper session created: %s for %s", session.id, today)
    asyncio.create_task(_run_session(session.id, config, access_token))


async def check_and_resume_sessions() -> None:
    """
    Called at startup to resume any interrupted live sessions from today.
    An interrupted session has status 'waiting' or 'entered' but no running task.
    """
    global _active_session_id
    if _active_session_id is not None:
        return  # already running

    today = date.today()
    async with AsyncSessionLocal() as db:
        session = await get_session_for_date(db, today)
        if not session or session.status not in ("waiting", "entered"):
            return

        config = (await db.execute(
            select(LivePaperConfig).where(LivePaperConfig.id == session.config_id).limit(1)
        )).scalar_one_or_none()
        if not config:
            return

        # Always re-fetch token from DB on resume — picks up tokens stored after a crash
        if config.user_id:
            access_token = await get_broker_token(db, config.user_id)
        else:
            from app.services.zerodha_client import get_access_token
            access_token = get_access_token()

        if not access_token:
            return

    log.info("Live paper: resuming interrupted session %s", session.id)
    asyncio.create_task(_run_session(session.id, config, access_token, resume=True))


# ── Timing helpers ────────────────────────────────────────────────────────────

async def _sleep_until(target: time) -> None:
    """Sleep until the next wall-clock occurrence of target time (IST)."""
    now = datetime.now(IST)
    target_dt = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if target_dt <= now:
        return
    delta = (target_dt - now).total_seconds()
    await asyncio.sleep(delta)


async def _sleep_until_next_minute() -> None:
    """Sleep until the next :00 wall-clock minute boundary."""
    now = datetime.now(IST)
    next_min = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    await asyncio.sleep((next_min - now).total_seconds())


# ── Session state helpers ─────────────────────────────────────────────────────

async def _update_session(session_id: uuid.UUID, **fields) -> None:
    async with AsyncSessionLocal() as db:
        fields["updated_at"] = datetime.now(IST)
        await db.execute(
            update(LivePaperSession)
            .where(LivePaperSession.id == session_id)
            .values(**fields)
        )
        await db.commit()


async def _append_waiting_spot(session_id: uuid.UUID, entry: Dict) -> None:
    """Append one spot reading to waiting_spot_json."""
    async with AsyncSessionLocal() as db:
        session = (await db.execute(
            select(LivePaperSession).where(LivePaperSession.id == session_id)
        )).scalar_one_or_none()
        if session:
            current = list(session.waiting_spot_json or [])
            current.append(entry)
            await db.execute(
                update(LivePaperSession)
                .where(LivePaperSession.id == session_id)
                .values(waiting_spot_json=current, updated_at=datetime.now(IST))
            )
            await db.commit()


async def _is_session_stopped(session_id: uuid.UUID) -> bool:
    """Return True if the session has been manually stopped (status=error)."""
    async with AsyncSessionLocal() as db:
        status = (await db.execute(
            select(LivePaperSession.status).where(LivePaperSession.id == session_id)
        )).scalar_one_or_none()
    return status == "error"


# ── Resume state loader ───────────────────────────────────────────────────────

async def _load_resume_state(
    session_id: uuid.UUID,
    config: LivePaperConfig,
    trade_date: date,
    lot_size: int,
    strike_step: int,
    wing_steps: int,
    trail_pct: float,
) -> Optional[Dict]:
    """
    Load existing session/run/leg/MTM state from DB for mid-day resume.
    Returns a dict of recovered state variables, or None if no run exists yet.
    """
    async with AsyncSessionLocal() as db:
        session = (await db.execute(
            select(LivePaperSession).where(LivePaperSession.id == session_id)
        )).scalar_one_or_none()
        if not session or not session.strategy_run_id:
            return None

        run_id = session.strategy_run_id
        existing_run = (await db.execute(
            select(StrategyRun).where(StrategyRun.id == run_id)
        )).scalar_one_or_none()
        if not existing_run:
            return None

        legs = (await db.execute(
            select(StrategyRunLeg)
            .where(StrategyRunLeg.run_id == run_id)
            .order_by(StrategyRunLeg.leg_index)
        )).scalars().all()

        sell_legs = [l for l in legs if l.side == "SELL"]
        buy_legs  = [l for l in legs if l.side == "BUY"]

        # Reconstruct entry prices
        straddle_entry_prices: List[Optional[float]] = [None, None]
        straddle_leg_ids      = [uuid.uuid4(), uuid.uuid4()]
        if len(sell_legs) >= 2:
            straddle_entry_prices = [
                float(sell_legs[0].entry_price) if sell_legs[0].entry_price else None,
                float(sell_legs[1].entry_price) if sell_legs[1].entry_price else None,
            ]
            straddle_leg_ids = [sell_legs[0].id, sell_legs[1].id]

        wing_entry_prices: List[Optional[float]] = [None, None]
        wing_leg_ids      = [uuid.uuid4(), uuid.uuid4()]
        wings_locked      = False
        lock_reason: Optional[str] = None
        if len(buy_legs) >= 2:
            wings_locked = True
            wing_entry_prices = [
                float(buy_legs[0].entry_price) if buy_legs[0].entry_price else None,
                float(buy_legs[1].entry_price) if buy_legs[1].entry_price else None,
            ]
            wing_leg_ids = [buy_legs[0].id, buy_legs[1].id]
            raw_lock = session.lock_status or "profit_locked"
            lock_reason = raw_lock.replace("_locked", "")

        # Reconstruct strike tuples from leg data
        ce_sell = next((l for l in sell_legs if l.option_type == "CE"), None)
        pe_sell = next((l for l in sell_legs if l.option_type == "PE"), None)
        ce_buy  = next((l for l in buy_legs  if l.option_type == "CE"), None)
        pe_buy  = next((l for l in buy_legs  if l.option_type == "PE"), None)

        atm_strike     = session.atm_strike
        expiry_date    = session.expiry_date
        wing_ce_strike = ce_buy.strike if ce_buy else (atm_strike + wing_steps * strike_step if atm_strike else None)
        wing_pe_strike = pe_buy.strike if pe_buy else (atm_strike - wing_steps * strike_step if atm_strike else None)

        straddle_legs = [("SELL", "CE", ce_sell.strike if ce_sell else atm_strike),
                         ("SELL", "PE", pe_sell.strike if pe_sell else atm_strike)]
        wing_legs     = [("BUY",  "CE", wing_ce_strike),
                         ("BUY",  "PE", wing_pe_strike)]

        # Recompute charges from entry prices (same formula as original entry)
        approved_lots = int(existing_run.approved_lots or 1)
        entry_credit_per_unit = float(existing_run.entry_credit_per_unit or 0)
        entry_credit_total    = float(existing_run.entry_credit_total or 0)
        entry_charges = (
            compute_leg_entry_charges(approved_lots, lot_size, straddle_legs, straddle_entry_prices)
            if all(p is not None for p in straddle_entry_prices) else 0.0
        )
        wing_entry_charges = (
            compute_leg_entry_charges(approved_lots, lot_size, wing_legs, wing_entry_prices)
            if wings_locked and all(p is not None for p in wing_entry_prices) else 0.0
        )

        # Recover trail state from last MTM row
        trail_active = False
        trail_peak   = 0.0
        last_mtm = (await db.execute(
            select(StrategyRunMtm)
            .where(StrategyRunMtm.run_id == run_id)
            .order_by(StrategyRunMtm.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        if last_mtm and last_mtm.trail_stop_level is not None:
            trail_active = True
            # trail_peak = trail_stop_level / trail_pct (reverse the trail_peak * trail_pct formula)
            trail_peak = float(last_mtm.trail_stop_level) / trail_pct if trail_pct else float(last_mtm.trail_stop_level)

        # Recover entry timestamp
        actual_entry_ts: Optional[datetime] = None
        if existing_run.entry_time:
            try:
                h, m = existing_run.entry_time.split(":")
                actual_entry_ts = datetime(
                    trade_date.year, trade_date.month, trade_date.day,
                    int(h), int(m), tzinfo=IST,
                )
            except Exception:
                pass

    return {
        "run_id":               run_id,
        "trade_open":           session.status == "entered",
        "straddle_entry_prices": straddle_entry_prices,
        "straddle_leg_ids":     straddle_leg_ids,
        "straddle_legs":        straddle_legs,
        "wing_entry_prices":    wing_entry_prices,
        "wing_leg_ids":         wing_leg_ids,
        "wing_legs":            wing_legs,
        "wings_locked":         wings_locked,
        "lock_reason":          lock_reason,
        "wing_ce_strike":       wing_ce_strike,
        "wing_pe_strike":       wing_pe_strike,
        "atm_strike":           atm_strike,
        "expiry_date":          expiry_date,
        "ce_symbol":            session.ce_symbol,
        "pe_symbol":            session.pe_symbol,
        "wing_ce_symbol":       session.wing_ce_symbol,
        "wing_pe_symbol":       session.wing_pe_symbol,
        "approved_lots":        approved_lots,
        "entry_credit_per_unit": entry_credit_per_unit,
        "entry_credit_total":   entry_credit_total,
        "entry_charges":        entry_charges,
        "wing_entry_charges":   wing_entry_charges,
        "trail_active":         trail_active,
        "trail_peak":           trail_peak,
        "actual_entry_ts":      actual_entry_ts,
    }


# ── Main engine task ──────────────────────────────────────────────────────────

async def _run_session(
    session_id: uuid.UUID,
    config: LivePaperConfig,
    access_token: str,
    resume: bool = False,
) -> None:
    """
    Self-driving asyncio task.  Runs from 09:14 to session exit.
    All DB writes use fresh AsyncSessionLocal() sessions.
    """
    global _active_session_id
    _active_session_id = session_id
    get_or_create_sse_queue(session_id)

    instrument   = config.instrument
    capital      = float(config.capital)
    entry_time   = _parse_time(config.entry_time, default=time(9, 50))
    params       = dict(config.params_json or {})

    stop_capital_pct  = float(params.get("stop_capital_pct", 0.015))
    trail_trigger     = float(params.get("trail_trigger", 12_000))
    trail_pct         = float(params.get("trail_pct", 0.50))
    lock_trigger      = float(params.get("lock_trigger", 20_000))
    loss_lock_trigger = float(params.get("loss_lock_trigger", 25_000))
    wing_steps        = int(params.get("wing_width_steps", 2))
    sq_time           = _parse_time(params.get("time_exit", "15:25"), default=_SQ_TIME)
    poll_interval     = max(3, int(params.get("poll_interval_seconds", 60)))
    stop_threshold    = -(capital * stop_capital_pct)

    trade_date = date.today()

    try:
        await _update_session(session_id, status="waiting")
        await _broadcast(session_id, {"type": "STATUS", "status": "waiting"})

        # Fetch instruments master once (heavy call, ~4k rows)
        log.info("Live paper: fetching NFO instruments master...")
        instruments = await asyncio.to_thread(get_instruments_with_token, access_token)
        log.info("Live paper: loaded %d instruments.", len(instruments))

        # Contract spec (lot_size, strike_step)
        async with AsyncSessionLocal() as db:
            spec = await get_contract_spec(db, instrument, trade_date)
        lot_size    = spec.lot_size
        strike_step = spec.strike_step
        margin_per_lot = float(spec.estimated_margin_per_lot or 0) or (24000 * lot_size * 0.12)

        # ── Initialise engine state ────────────────────────────────────────────
        run_id = uuid.uuid4()
        trade_open            = False
        straddle_entry_prices: List[Optional[float]] = [None, None]
        straddle_last_prices:  List[Optional[float]] = [None, None]
        straddle_legs: List   = []
        straddle_leg_ids      = [uuid.uuid4(), uuid.uuid4()]
        wing_entry_prices:    List[Optional[float]] = [None, None]
        wing_last_prices:     List[Optional[float]] = [None, None]
        wing_legs: List       = []
        wing_leg_ids          = [uuid.uuid4(), uuid.uuid4()]
        wings_locked          = False
        lock_reason: Optional[str] = None
        wing_lock_ts: Optional[datetime] = None
        wing_entry_charges    = 0.0
        entry_credit_per_unit = 0.0
        entry_credit_total    = 0.0
        entry_charges         = 0.0
        actual_entry_ts: Optional[datetime] = None
        exit_reason: Optional[str] = None
        exit_ts: Optional[datetime] = None
        trail_active   = False
        trail_peak     = 0.0
        trail_stop_at_exit: Optional[float] = None
        approved_lots  = 1

        atm_strike: Optional[int]     = None
        expiry_date: Optional[date]   = None
        ce_symbol: Optional[str]      = None
        pe_symbol: Optional[str]      = None
        wing_ce_symbol: Optional[str] = None
        wing_pe_symbol: Optional[str] = None
        wing_ce_strike: Optional[int] = None
        wing_pe_strike: Optional[int] = None

        # Stale-minutes tracking per leg (for strategy_leg_mtm.stale_minutes)
        ce_stale = 0
        pe_stale = 0
        wce_stale = 0
        wpe_stale = 0

        # Minute-level dedup: DB MTM rows and waiting_spot_json stay at 1-min
        # granularity regardless of poll_interval. SSE broadcasts every tick.
        _last_db_minute      = -1
        _last_waiting_minute = -1

        # ── Resume: load existing state instead of creating a new StrategyRun ─
        if resume:
            saved = await _load_resume_state(
                session_id, config, trade_date, lot_size, strike_step, wing_steps, trail_pct
            )
            if saved:
                run_id                = saved["run_id"]
                trade_open            = saved["trade_open"]
                straddle_entry_prices = saved["straddle_entry_prices"]
                straddle_leg_ids      = saved["straddle_leg_ids"]
                straddle_legs         = saved["straddle_legs"]
                wing_entry_prices     = saved["wing_entry_prices"]
                wing_leg_ids          = saved["wing_leg_ids"]
                wing_legs             = saved["wing_legs"]
                wings_locked          = saved["wings_locked"]
                lock_reason           = saved["lock_reason"]
                wing_ce_strike        = saved["wing_ce_strike"]
                wing_pe_strike        = saved["wing_pe_strike"]
                atm_strike            = saved["atm_strike"]
                expiry_date           = saved["expiry_date"]
                ce_symbol             = saved["ce_symbol"]
                pe_symbol             = saved["pe_symbol"]
                wing_ce_symbol        = saved["wing_ce_symbol"]
                wing_pe_symbol        = saved["wing_pe_symbol"]
                approved_lots         = saved["approved_lots"]
                entry_credit_per_unit = saved["entry_credit_per_unit"]
                entry_credit_total    = saved["entry_credit_total"]
                entry_charges         = saved["entry_charges"]
                wing_entry_charges    = saved["wing_entry_charges"]
                trail_active          = saved["trail_active"]
                trail_peak            = saved["trail_peak"]
                actual_entry_ts       = saved["actual_entry_ts"]
                log.info("Live paper: resumed session %s with existing run %s (trade_open=%s)",
                         session_id, run_id, trade_open)

        if not resume or not trade_open:
            # Create StrategyRun row upfront so MTM rows have a valid FK
            # (skipped on resume when an existing run is reused)
            if not resume:
                async with AsyncSessionLocal() as db:
                    db.add(StrategyRun(
                        id=run_id,
                        user_id=config.user_id,
                        strategy_id=config.strategy_id,
                        strategy_version="v1",
                        run_type="live_paper_session",
                        executor="straddle_adjustment_v1",
                        instrument=instrument,
                        trade_date=trade_date,
                        status="in_progress",
                        capital=capital,
                        config_json={**params, "strategy_id": config.strategy_id, "entry_time": config.entry_time},
                        result_json={"live": True},
                    ))
                    await db.commit()

                await _update_session(session_id, strategy_run_id=run_id)

        spot_symbol = SPOT_SYMBOLS.get(instrument, "NSE:NIFTY 50")

        # ── Main loop ──────────────────────────────────────────────────────────
        while True:
            await asyncio.sleep(poll_interval)
            now = datetime.now(IST)
            t   = now.time()

            # Check for manual stop signal (written by /stop endpoint)
            if await _is_session_stopped(session_id):
                log.info("Live paper: session %s manually stopped, exiting loop.", session_id)
                await _broadcast(session_id, {"type": "DONE", "status": "error", "exit_reason": "MANUAL_STOP"})
                return

            if t < _SESSION_START or t >= _SESSION_END:
                if t >= _SESSION_END:
                    break
                continue

            # Fetch spot
            try:
                spot_quotes = await asyncio.to_thread(
                    fetch_live_quote, [spot_symbol], access_token
                )
                spot = spot_quotes.get(spot_symbol)
            except Exception as exc:
                log.warning("Live paper: spot fetch failed at %s: %s", t, exc)
                spot = None

            # ── Resolve instruments at 09:49 ──────────────────────────────────
            if t >= _RESOLVE_TIME and atm_strike is None and spot is not None:
                atm_strike  = resolve_atm_strike(spot, strike_step)
                wing_ce_strike = atm_strike + wing_steps * strike_step
                wing_pe_strike = atm_strike - wing_steps * strike_step

                # Find nearest expiry from instruments master
                available = sorted(set(
                    r["expiry"].date() if hasattr(r["expiry"], "date") else r["expiry"]
                    for r in instruments
                    if r.get("name") == instrument
                    and r.get("instrument_type") == "CE"
                    and (r["expiry"].date() if hasattr(r["expiry"], "date") else r["expiry"]) >= trade_date
                ))
                expiry_date = available[0] if available else None

                if expiry_date:
                    ce_symbol       = find_option_symbol(instruments, instrument, expiry_date, "CE", atm_strike)
                    pe_symbol       = find_option_symbol(instruments, instrument, expiry_date, "PE", atm_strike)
                    wing_ce_symbol  = find_option_symbol(instruments, instrument, expiry_date, "CE", wing_ce_strike)
                    wing_pe_symbol  = find_option_symbol(instruments, instrument, expiry_date, "PE", wing_pe_strike)

                    straddle_legs = [("SELL", "CE", atm_strike), ("SELL", "PE", atm_strike)]
                    wing_legs     = [("BUY",  "CE", wing_ce_strike), ("BUY", "PE", wing_pe_strike)]

                    approved_lots = max(1, int(capital / margin_per_lot))

                    await _update_session(
                        session_id,
                        atm_strike=atm_strike,
                        expiry_date=expiry_date,
                        ce_symbol=ce_symbol,
                        pe_symbol=pe_symbol,
                        wing_ce_symbol=wing_ce_symbol,
                        wing_pe_symbol=wing_pe_symbol,
                        approved_lots=approved_lots,
                    )
                    await _broadcast(session_id, {
                        "type": "RESOLVED",
                        "timestamp": now.isoformat(),
                        "atm_strike": atm_strike,
                        "expiry_date": str(expiry_date),
                        "approved_lots": approved_lots,
                        "spot": spot,
                    })
                    log.info(
                        "Live paper: ATM=%d expiry=%s lots=%d CE=%s PE=%s",
                        atm_strike, expiry_date, approved_lots, ce_symbol, pe_symbol,
                    )
                else:
                    log.error("Live paper: could not find any expiry for %s on %s", instrument, trade_date)

            # Pre-entry: record spot and broadcast waiting update
            if t < entry_time or atm_strike is None:
                if spot is not None and now.minute != _last_waiting_minute:
                    await _append_waiting_spot(
                        session_id, {"timestamp": now.isoformat(), "spot": spot}
                    )
                    _last_waiting_minute = now.minute
                await _broadcast(session_id, {
                    "type": "WAITING",
                    "timestamp": now.isoformat(),
                    "spot": spot,
                })
                continue

            # ── Fetch option prices ────────────────────────────────────────────
            option_syms = [s for s in [ce_symbol, pe_symbol, wing_ce_symbol, wing_pe_symbol] if s]
            try:
                opt_quotes = await asyncio.to_thread(
                    fetch_live_quote, option_syms, access_token
                )
            except Exception as exc:
                log.warning("Live paper: option fetch failed at %s: %s", t, exc)
                opt_quotes = {}

            s_ce_price = opt_quotes.get(ce_symbol)
            s_pe_price = opt_quotes.get(pe_symbol)
            w_ce_price = opt_quotes.get(wing_ce_symbol)
            w_pe_price = opt_quotes.get(wing_pe_symbol)

            # Track last known prices (used as stale fallback for MTM)
            if s_ce_price is not None:
                straddle_last_prices[0] = s_ce_price
                ce_stale = 0
            else:
                ce_stale += 1

            if s_pe_price is not None:
                straddle_last_prices[1] = s_pe_price
                pe_stale = 0
            else:
                pe_stale += 1

            if w_ce_price is not None:
                wing_last_prices[0] = w_ce_price
                wce_stale = 0
            else:
                wce_stale += 1

            if w_pe_price is not None:
                wing_last_prices[1] = w_pe_price
                wpe_stale = 0
            else:
                wpe_stale += 1

            # ── Entry ──────────────────────────────────────────────────────────
            if not trade_open:
                if s_ce_price is None or s_pe_price is None:
                    await _write_event(run_id, now, "HOLD", "MISSING_LEG_PRICE")
                    await _broadcast(session_id, {"type": "HOLD", "timestamp": now.isoformat(), "reason": "MISSING_LEG_PRICE", "spot": spot})
                    continue

                trade_open = True
                actual_entry_ts = now
                straddle_entry_prices = [s_ce_price, s_pe_price]
                entry_credit_per_unit = s_ce_price + s_pe_price
                entry_credit_total    = entry_credit_per_unit * lot_size * approved_lots
                entry_charges         = compute_leg_entry_charges(approved_lots, lot_size, straddle_legs, straddle_entry_prices)

                # Persist legs
                async with AsyncSessionLocal() as db:
                    for i, ((side, opt_type, strike), leg_id) in enumerate(zip(straddle_legs, straddle_leg_ids)):
                        db.add(StrategyRunLeg(
                            id=leg_id, run_id=run_id, leg_index=i,
                            side=side, option_type=opt_type, strike=strike,
                            expiry_date=expiry_date,
                            quantity=lot_size * approved_lots,
                            entry_price=straddle_entry_prices[i],
                        ))
                    await db.commit()

                # Update run entry_time
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(StrategyRun)
                        .where(StrategyRun.id == run_id)
                        .values(
                            entry_time=actual_entry_ts.strftime("%H:%M"),
                            entry_credit_per_unit=round(entry_credit_per_unit, 2),
                            entry_credit_total=round(entry_credit_total, 2),
                            lot_size=lot_size,
                            approved_lots=approved_lots,
                        )
                    )
                    await db.commit()

                await _update_session(session_id, status="entered")
                await _write_event(run_id, now, "ENTRY", "ENTRY_SCHEDULED", payload={
                    "spot": spot,
                    "legs": [
                        {"side": "SELL", "option_type": "CE", "strike": atm_strike, "price": straddle_entry_prices[0]},
                        {"side": "SELL", "option_type": "PE", "strike": atm_strike, "price": straddle_entry_prices[1]},
                    ],
                })
                await _broadcast(session_id, {
                    "type": "ENTRY",
                    "timestamp": now.isoformat(),
                    "spot": spot,
                    "ce_price": straddle_entry_prices[0],
                    "pe_price": straddle_entry_prices[1],
                    "entry_credit_total": round(entry_credit_total, 2),
                    "atm_strike": atm_strike,
                })
                log.info("Live paper: ENTERED straddle at %s CE=%.2f PE=%.2f credit=%.0f",
                         t, straddle_entry_prices[0], straddle_entry_prices[1], entry_credit_total)
                continue

            # ── Trade open: MTM calculation using last-price fallback ──────────
            # If the current quote is missing, fall back to the last known price
            # so no leg is silently dropped from the MTM sum.
            ce_for_mtm = s_ce_price if s_ce_price is not None else straddle_last_prices[0]
            pe_for_mtm = s_pe_price if s_pe_price is not None else straddle_last_prices[1]
            straddle_cur = [ce_for_mtm, pe_for_mtm]

            straddle_gross = sum(
                (ep - cp)
                for ep, cp in zip(straddle_entry_prices, straddle_cur)
                if ep is not None and cp is not None
            ) * lot_size * approved_lots

            wing_gross = 0.0
            if wings_locked:
                wce_for_mtm = w_ce_price if w_ce_price is not None else wing_last_prices[0]
                wpe_for_mtm = w_pe_price if w_pe_price is not None else wing_last_prices[1]
                wing_cur = [wce_for_mtm, wpe_for_mtm]
                wing_gross = sum(
                    (cp - ep)
                    for ep, cp in zip(wing_entry_prices, wing_cur)
                    if ep is not None and cp is not None
                ) * lot_size * approved_lots
            else:
                wing_cur = [w_ce_price, w_pe_price]

            gross_mtm = straddle_gross + wing_gross

            if wings_locked:
                active_legs = straddle_legs + wing_legs
                all_cur     = list(straddle_cur) + list(wing_cur)
                est_exit    = compute_leg_exit_charges_estimate(approved_lots, lot_size, active_legs, all_cur)
            else:
                est_exit = compute_leg_exit_charges_estimate(approved_lots, lot_size, straddle_legs, straddle_cur)

            net_mtm = gross_mtm - entry_charges - wing_entry_charges - est_exit

            # ── Hard stop: evaluated BEFORE lock to prevent wing buy on deep loss ──
            fired: Optional[str] = None
            if net_mtm <= stop_threshold:
                fired = "STOP_EXIT"

            # ── Lock check (only if trade is still alive after stop check) ─────
            if not wings_locked and fired is None:
                profit_lock_hit = lock_trigger > 0 and net_mtm >= lock_trigger
                loss_lock_hit   = loss_lock_trigger > 0 and net_mtm <= -loss_lock_trigger
                if (profit_lock_hit or loss_lock_hit) and w_ce_price and w_pe_price:
                    wings_locked      = True
                    lock_reason       = "profit" if profit_lock_hit else "loss"
                    wing_entry_prices = [w_ce_price, w_pe_price]
                    wing_lock_ts      = now
                    wing_entry_charges = compute_leg_entry_charges(
                        approved_lots, lot_size, wing_legs, wing_entry_prices
                    )
                    net_mtm -= wing_entry_charges

                    # Persist wing legs
                    async with AsyncSessionLocal() as db:
                        for i, ((side, opt_type, strike), leg_id) in enumerate(zip(wing_legs, wing_leg_ids)):
                            db.add(StrategyRunLeg(
                                id=leg_id, run_id=run_id, leg_index=i + 2,
                                side=side, option_type=opt_type, strike=[wing_ce_strike, wing_pe_strike][i],
                                expiry_date=expiry_date,
                                quantity=lot_size * approved_lots,
                                entry_price=wing_entry_prices[i],
                            ))
                        await db.commit()

                    label = "Profit lock" if profit_lock_hit else "Loss lock"
                    await _write_event(run_id, now, "HOLD", "WINGS_LOCKED", payload={
                        "lock_reason": lock_reason,
                        "net_mtm_at_lock": round(net_mtm, 2),
                        "wing_ce": {"strike": wing_ce_strike, "price": w_ce_price},
                        "wing_pe": {"strike": wing_pe_strike, "price": w_pe_price},
                    })
                    await _update_session(
                        session_id,
                        lock_status=f"{lock_reason}_locked",
                    )
                    await _broadcast(session_id, {
                        "type": "LOCK",
                        "timestamp": now.isoformat(),
                        "lock_reason": lock_reason,
                        "net_mtm": round(net_mtm, 2),
                        "wing_ce_price": w_ce_price,
                        "wing_pe_price": w_pe_price,
                    })
                    log.info("Live paper: %s fired at %s net_mtm=%.0f", label, t, net_mtm)

            # ── Trail ─────────────────────────────────────────────────────────
            trail_stop_level: Optional[float] = None
            if trail_trigger > 0 and trail_pct > 0:
                if not trail_active and net_mtm >= trail_trigger:
                    trail_active = True
                    trail_peak   = net_mtm
                if trail_active:
                    if net_mtm > trail_peak:
                        trail_peak = net_mtm
                    trail_stop_level = round(trail_peak * trail_pct, 2)

            # ── Remaining exit conditions ──────────────────────────────────────
            if fired is None:
                if trail_active and trail_stop_level is not None and net_mtm <= trail_stop_level:
                    fired = "TRAIL_EXIT"
                elif t >= sq_time:
                    fired = "TIME_EXIT"

            # ── Persist MTM row (once per minute; SSE broadcasts every tick) ──
            active_leg_ids    = straddle_leg_ids + (wing_leg_ids if wings_locked else [])
            active_cur_prices = list(straddle_cur) + (list(wing_cur) if wings_locked else [])
            active_entry_p    = straddle_entry_prices + (wing_entry_prices if wings_locked else [])
            active_sides      = ["SELL", "SELL"] + (["BUY", "BUY"] if wings_locked else [])
            active_stale      = [ce_stale, pe_stale] + ([wce_stale, wpe_stale] if wings_locked else [])
            if now.minute != _last_db_minute or fired:
                await _write_mtm(run_id, now, spot, None, gross_mtm, est_exit, net_mtm,
                                 trail_stop_level, fired,
                                 active_leg_ids, active_cur_prices, active_entry_p, active_sides,
                                 active_stale, lot_size, approved_lots)
                _last_db_minute = now.minute

            await _update_session(
                session_id,
                net_mtm_latest=round(net_mtm, 2),
                spot_latest=round(spot, 2) if spot else None,
            )
            await _broadcast(session_id, {
                "type": "MTM",
                "timestamp": now.isoformat(),
                "spot": spot,
                "ce_price": s_ce_price,
                "pe_price": s_pe_price,
                "net_mtm": round(net_mtm, 2),
                "gross_mtm": round(gross_mtm, 2),
                "trail_stop_level": trail_stop_level,
                "wings_locked": wings_locked,
                "lock_reason": lock_reason,
            })

            if fired:
                exit_reason = fired
                exit_ts     = now
                if fired == "TRAIL_EXIT" and trail_stop_level is not None:
                    trail_stop_at_exit = trail_stop_level
                await _write_event(run_id, now, fired, fired, payload={
                    "net_mtm": round(net_mtm, 2), "spot": spot, "wings_locked": wings_locked,
                })
                log.info("Live paper: EXIT %s at %s net_mtm=%.0f", fired, t, net_mtm)
                break

        # ── Finalize ───────────────────────────────────────────────────────────
        realized_net_pnl: Optional[float] = None
        gross_pnl:        Optional[float] = None
        total_charges:    Optional[float] = None

        if trade_open:
            exit_s = list(straddle_last_prices)
            straddle_gross_pnl = sum(
                (ep - xp) * lot_size * approved_lots
                for ep, xp in zip(straddle_entry_prices, exit_s) if ep and xp
            )
            wing_gross_pnl = 0.0
            if wings_locked:
                exit_w = list(wing_last_prices)
                wing_gross_pnl = sum(
                    (xp - ep) * lot_size * approved_lots
                    for ep, xp in zip(wing_entry_prices, exit_w) if ep and xp
                )
            gross_pnl = straddle_gross_pnl + wing_gross_pnl

            if wings_locked:
                all_exit_legs   = straddle_legs + wing_legs
                all_entry_p     = straddle_entry_prices + wing_entry_prices
                all_exit_p      = list(straddle_last_prices) + list(wing_last_prices)
            else:
                all_exit_legs   = straddle_legs
                all_entry_p     = straddle_entry_prices
                all_exit_p      = list(straddle_last_prices)

            total_charges = (
                compute_leg_total_charges(approved_lots, lot_size, all_exit_legs, all_entry_p, all_exit_p)
                + wing_entry_charges
            )
            realized_net_pnl = round(gross_pnl - total_charges, 2)

            if exit_reason == "TRAIL_EXIT" and trail_stop_at_exit is not None:
                realized_net_pnl = round(trail_stop_at_exit, 2)
                gross_pnl        = round(trail_stop_at_exit + total_charges, 2)

            # Update exit prices on legs
            async with AsyncSessionLocal() as db:
                for i, leg_id in enumerate(straddle_leg_ids):
                    xp = straddle_last_prices[i]
                    ep = straddle_entry_prices[i]
                    await db.execute(
                        update(StrategyRunLeg)
                        .where(StrategyRunLeg.id == leg_id)
                        .values(
                            exit_price=xp,
                            gross_leg_pnl=round((ep - xp) * lot_size * approved_lots, 2) if ep and xp else None,
                        )
                    )
                if wings_locked:
                    for i, leg_id in enumerate(wing_leg_ids):
                        xp = wing_last_prices[i]
                        ep = wing_entry_prices[i]
                        await db.execute(
                            update(StrategyRunLeg)
                            .where(StrategyRunLeg.id == leg_id)
                            .values(
                                exit_price=xp,
                                gross_leg_pnl=round((xp - ep) * lot_size * approved_lots, 2) if ep and xp else None,
                            )
                        )
                await db.commit()

        status = "no_trade" if not trade_open else "completed"
        if not trade_open:
            exit_reason = exit_reason or "NO_TRADE"
            await _write_event(
                run_id,
                datetime.combine(trade_date, sq_time),
                "NO_TRADE", exit_reason,
            )

        # Final update to StrategyRun
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(StrategyRun).where(StrategyRun.id == run_id).values(
                    status=status,
                    exit_reason=exit_reason,
                    exit_time=exit_ts.strftime("%H:%M") if exit_ts else None,
                    gross_pnl=round(gross_pnl, 2) if gross_pnl is not None else None,
                    total_charges=round(total_charges, 2) if total_charges is not None else None,
                    realized_net_pnl=realized_net_pnl,
                    result_json={
                        "live": True,
                        "wings_locked": wings_locked,
                        "lock_reason": lock_reason,
                        "wing_lock_ts": wing_lock_ts.isoformat() if wing_lock_ts else None,
                        "warnings": [],
                        "exit_reason": exit_reason,
                    },
                )
            )
            await db.commit()

        await _update_session(
            session_id,
            status="exited" if trade_open else "no_trade",
            exit_reason=exit_reason,
            realized_net_pnl=realized_net_pnl,
        )
        await _broadcast(session_id, {
            "type": "DONE",
            "status": "exited" if trade_open else "no_trade",
            "exit_reason": exit_reason,
            "realized_net_pnl": realized_net_pnl,
            "strategy_run_id": str(run_id),
        })
        log.info(
            "Live paper session %s done — status=%s pnl=%s",
            session_id, status, realized_net_pnl,
        )

    except Exception as exc:
        log.exception("Live paper session %s crashed: %s", session_id, exc)
        await _update_session(session_id, status="error", error_message=str(exc))
        await _broadcast(session_id, {"type": "ERROR", "message": str(exc)})
    finally:
        _active_session_id = None


# ── DB write helpers ──────────────────────────────────────────────────────────

async def _write_event(
    run_id: uuid.UUID,
    ts: datetime,
    event_type: str,
    reason_code: str,
    payload: Optional[Dict] = None,
) -> None:
    async with AsyncSessionLocal() as db:
        db.add(StrategyRunEvent(
            run_id=run_id,
            timestamp=ts.replace(tzinfo=None),  # columns are timezone=False
            event_type=event_type,
            reason_code=reason_code,
            payload_json=payload or {},
        ))
        await db.commit()


async def _write_mtm(
    run_id: uuid.UUID,
    ts: datetime,
    spot: Optional[float],
    vix: Optional[float],
    gross_mtm: float,
    est_exit: float,
    net_mtm: float,
    trail_stop: Optional[float],
    event_code: Optional[str],
    # Per-leg data for strategy_leg_mtm rows
    leg_ids: List[uuid.UUID],
    leg_cur_prices: List[Optional[float]],
    leg_entry_prices: List[Optional[float]],
    leg_sides: List[str],
    leg_stale_minutes: List[int],
    lot_size: int,
    lots: int,
) -> None:
    """Write one StrategyRunMtm aggregate row + one StrategyLegMtm row per active leg."""
    async with AsyncSessionLocal() as db:
        db.add(StrategyRunMtm(
            run_id=run_id,
            timestamp=ts.replace(tzinfo=None),  # column is timezone=False
            spot_close=spot,
            vix_close=vix,
            gross_mtm=round(gross_mtm, 2),
            est_exit_charges=round(est_exit, 2),
            net_mtm=round(net_mtm, 2),
            trail_stop_level=trail_stop,
            event_code=event_code,
        ))
        for leg_id, cur_p, ep, side, stale in zip(
            leg_ids, leg_cur_prices, leg_entry_prices, leg_sides, leg_stale_minutes
        ):
            if ep is None:
                continue
            if side == "SELL":
                leg_pnl = round((ep - (cur_p or ep)) * lot_size * lots, 2) if cur_p else None
            else:
                leg_pnl = round(((cur_p or ep) - ep) * lot_size * lots, 2) if cur_p else None
            db.add(StrategyLegMtm(
                run_id=run_id,
                leg_id=leg_id,
                timestamp=ts.replace(tzinfo=None),  # column is timezone=False
                price=cur_p,
                gross_leg_pnl=leg_pnl,
                stale_minutes=stale,
            ))
        await db.commit()


def _parse_time(s: Any, default: time = time(9, 50)) -> time:
    if not s:
        return default
    try:
        h, m = str(s).split(":")
        return time(int(h), int(m))
    except Exception:
        return default

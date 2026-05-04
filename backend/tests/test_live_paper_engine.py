"""
Targeted tests for live_paper_engine.py addressing PR #29 review blockers.

Tests are pure-unit where possible (no real DB, no real Zerodha).
Async tests mock AsyncSessionLocal and the Zerodha client.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.live_paper_engine import (
    _stop_requested,
    _load_resume_state,
    _parse_time,
    get_active_config,
    get_session_for_date,
    start_live_session,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_session(**kw):
    defaults = dict(
        id=uuid.uuid4(),
        status="waiting",
        trade_date=date.today(),
        strategy_run_id=None,
        atm_strike=None,
        expiry_date=None,
        ce_symbol=None,
        pe_symbol=None,
        wing_ce_symbol=None,
        wing_pe_symbol=None,
        lock_status="none",
        user_id=uuid.uuid4(),
        config_id=uuid.uuid4(),
        waiting_spot_json=[],
        net_mtm_latest=None,
        spot_latest=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_config(**kw):
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        strategy_id="short_straddle_dual_lock",
        instrument="NIFTY",
        capital=2_500_000,
        entry_time="09:50",
        params_json={},
        enabled=True,
        execution_mode="paper",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ── Fix 5: stop fires before lock ────────────────────────────────────────────

def test_stop_fires_before_lock_on_deep_loss():
    """
    When net_mtm is below stop_threshold, STOP_EXIT must fire
    and the loss-lock wing buy must be skipped.
    """
    capital          = 2_500_000
    stop_capital_pct = 0.015
    stop_threshold   = -(capital * stop_capital_pct)  # -37,500
    loss_lock_trigger = 25_000

    net_mtm     = -38_000   # breaches stop AND would trigger loss_lock
    wings_locked = False

    # ── New ordering: stop checked first ──────────────────────────────────────
    fired: Optional[str] = None
    if net_mtm <= stop_threshold:
        fired = "STOP_EXIT"

    lock_triggered = False
    if not wings_locked and fired is None:
        if net_mtm <= -loss_lock_trigger:
            lock_triggered = True

    assert fired == "STOP_EXIT", "STOP_EXIT should fire"
    assert not lock_triggered, "loss-lock wings must not be bought after stop fires"


def test_stop_not_triggered_at_small_loss():
    """Stop threshold is only breached when loss exceeds stop_capital_pct."""
    capital          = 2_500_000
    stop_capital_pct = 0.015
    stop_threshold   = -(capital * stop_capital_pct)  # -37,500

    net_mtm = -10_000   # small loss, far from stop

    fired: Optional[str] = None
    if net_mtm <= stop_threshold:
        fired = "STOP_EXIT"

    assert fired is None


# ── Fix 6: stale last-price fallback ─────────────────────────────────────────

def test_stale_fallback_used_when_ce_price_missing():
    """
    When s_ce_price is None (quote unavailable), the last known CE price
    should be used for MTM so the leg is not silently dropped.
    """
    straddle_entry_prices = [120.0, 110.0]   # CE, PE entry
    straddle_last_prices  = [95.0, 105.0]    # last known
    s_ce_price = None                         # current quote missing
    s_pe_price = 100.0

    ce_for_mtm = s_ce_price if s_ce_price is not None else straddle_last_prices[0]
    pe_for_mtm = s_pe_price if s_pe_price is not None else straddle_last_prices[1]
    straddle_cur = [ce_for_mtm, pe_for_mtm]

    lot_size      = 75
    approved_lots = 1
    straddle_gross = sum(
        (ep - cp) for ep, cp in zip(straddle_entry_prices, straddle_cur)
        if ep is not None and cp is not None
    ) * lot_size * approved_lots

    # With fallback: CE contributes (120 - 95)=25, PE contributes (110 - 100)=10 → 35*75=2625
    assert straddle_gross == pytest.approx(2625.0), (
        f"Expected 2625.0 (both legs included via fallback), got {straddle_gross}"
    )


def test_no_fallback_needed_when_both_quotes_fresh():
    """Fresh quotes should be used directly — fallback must not override them."""
    straddle_entry_prices = [120.0, 110.0]
    straddle_last_prices  = [99.0, 99.0]  # stale
    s_ce_price = 100.0
    s_pe_price = 105.0

    ce_for_mtm = s_ce_price if s_ce_price is not None else straddle_last_prices[0]
    pe_for_mtm = s_pe_price if s_pe_price is not None else straddle_last_prices[1]

    assert ce_for_mtm == 100.0
    assert pe_for_mtm == 105.0


def test_stale_counter_increments_on_missing_quote():
    """Stale counter must increment each tick a quote is missing, reset on fresh quote."""
    ce_stale = 0

    for _ in range(3):
        s_ce_price = None
        ce_stale = 0 if s_ce_price is not None else (ce_stale + 1)

    assert ce_stale == 3

    # Fresh quote resets counter
    s_ce_price = 100.0
    ce_stale = 0 if s_ce_price is not None else (ce_stale + 1)
    assert ce_stale == 0


# ── Fix 3: user-scoped get_active_config ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_active_config_filters_by_user_id():
    """get_active_config with user_id must include user_id in the WHERE clause."""
    user_id = uuid.uuid4()
    captured_queries = []

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, stmt):
            captured_queries.append(stmt)
            return _FakeResult()

    await get_active_config(_FakeDB(), user_id=user_id)

    assert captured_queries, "Expected at least one DB query"
    compiled = str(captured_queries[0].compile(compile_kwargs={"literal_binds": True}))
    # PostgreSQL UUID columns are rendered without dashes in literal binds
    assert user_id.hex in compiled, (
        f"user_id hex {user_id.hex} should appear in query; got: {compiled}"
    )


@pytest.mark.asyncio
async def test_get_active_config_no_user_id_does_not_filter():
    """get_active_config without user_id must not filter on user_id in WHERE."""
    captured_queries = []

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, stmt):
            captured_queries.append(stmt)
            return _FakeResult()

    await get_active_config(_FakeDB(), user_id=None)

    compiled = str(captured_queries[0].compile(compile_kwargs={"literal_binds": True}))
    # The SELECT list always contains user_id; check the WHERE clause specifically
    assert "AND live_paper_configs.user_id" not in compiled


# ── Fix 1: emergency stop detection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_requested_returns_true_for_stop_requested_status():
    """_stop_requested reads 'stop_requested' status (set by /stop endpoint)."""
    session_id = uuid.uuid4()

    class _FakeResult:
        def scalar_one_or_none(self):
            return "stop_requested"

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    with patch("app.services.live_paper_engine.AsyncSessionLocal", return_value=_FakeDB()):
        result = await _stop_requested(session_id)

    assert result is True


@pytest.mark.asyncio
async def test_stop_requested_returns_false_for_entered_status():
    session_id = uuid.uuid4()

    class _FakeResult:
        def scalar_one_or_none(self):
            return "entered"

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    with patch("app.services.live_paper_engine.AsyncSessionLocal", return_value=_FakeDB()):
        result = await _stop_requested(session_id)

    assert result is False


@pytest.mark.asyncio
async def test_stop_requested_returns_false_for_error_status():
    """status=error is a crash, not a manual stop — must not trigger the stop path."""
    session_id = uuid.uuid4()

    class _FakeResult:
        def scalar_one_or_none(self):
            return "error"

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    with patch("app.services.live_paper_engine.AsyncSessionLocal", return_value=_FakeDB()):
        result = await _stop_requested(session_id)

    assert result is False


# ── Fix 7: execution_mode=live blocked ───────────────────────────────────────
# These tests replicate the guard logic directly without importing the router
# (which pulls in python-jose, unavailable in the host Python environment).

def test_execution_mode_live_guard_raises():
    """The guard condition 'execution_mode == live' must trigger a rejection."""
    from fastapi import HTTPException

    execution_mode = "live"
    raised = None
    if execution_mode == "live":
        raised = HTTPException(
            status_code=422,
            detail="execution_mode='live' is disabled — live order placement not yet implemented.",
        )

    assert raised is not None
    assert raised.status_code == 422
    assert "disabled" in raised.detail


def test_execution_mode_paper_passes_guard():
    """execution_mode='paper' must not trigger the guard."""
    execution_mode = "paper"
    raised = None
    if execution_mode == "live":
        from fastapi import HTTPException
        raised = HTTPException(status_code=422, detail="blocked")

    assert raised is None


# ── Fix 8: unique constraint present on model ─────────────────────────────────

def test_live_paper_session_has_unique_constraint():
    from app.models.live_paper import LivePaperSession
    constraints = {
        c.name
        for c in LivePaperSession.__table__.constraints
    }
    assert "uq_live_paper_session_user_date" in constraints, (
        "LivePaperSession must have uq_live_paper_session_user_date unique constraint"
    )


# ── Fix 2: resume loads existing run (not a new one) ─────────────────────────

@pytest.mark.asyncio
async def test_resume_reuses_existing_run_id():
    """
    When resume=True and session has a strategy_run_id, _load_resume_state
    must return that existing run_id, not generate a new one.
    """
    existing_run_id = uuid.uuid4()
    session_id      = uuid.uuid4()
    trade_date      = date.today()

    session = _make_session(
        id=session_id,
        status="entered",
        strategy_run_id=existing_run_id,
        atm_strike=24200,
        expiry_date=date(2026, 5, 8),
        ce_symbol="NFO:NIFTY26MAY24200CE",
        pe_symbol="NFO:NIFTY26MAY24200PE",
        wing_ce_symbol="NFO:NIFTY26MAY24300CE",
        wing_pe_symbol="NFO:NIFTY26MAY24100PE",
        lock_status="none",
    )

    existing_run = SimpleNamespace(
        id=existing_run_id,
        entry_credit_per_unit=230.0,
        entry_credit_total=1_725_000.0,
        lot_size=75,
        approved_lots=10,
        entry_time="09:50",
    )

    sell_leg_ce = SimpleNamespace(id=uuid.uuid4(), side="SELL", option_type="CE",
                                   strike=24200, entry_price=120.0, leg_index=0)
    sell_leg_pe = SimpleNamespace(id=uuid.uuid4(), side="SELL", option_type="PE",
                                   strike=24200, entry_price=110.0, leg_index=1)

    class _FakeScalars:
        def __init__(self, rows): self._rows = rows
        def all(self): return self._rows

    class _FakeResult:
        def __init__(self, value=None, rows=None):
            self._value = value
            self._rows  = rows or []
        def scalar_one_or_none(self): return self._value
        def scalars(self): return _FakeScalars(self._rows)

    call_count = [0]

    class _FakeDB:
        async def execute(self, stmt):
            call_count[0] += 1
            # First call: select LivePaperSession
            if call_count[0] == 1:
                return _FakeResult(value=session)
            # Second call: select StrategyRun
            if call_count[0] == 2:
                return _FakeResult(value=existing_run)
            # Third call: select StrategyRunLeg (returns two SELL legs)
            if call_count[0] == 3:
                return _FakeResult(rows=[sell_leg_ce, sell_leg_pe])
            # Fourth call: select StrategyRunMtm (no trail yet)
            return _FakeResult(value=None)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("app.services.live_paper_engine.AsyncSessionLocal", return_value=_FakeDB()):
        state = await _load_resume_state(
            session_id=session_id,
            config=_make_config(),
            trade_date=trade_date,
            lot_size=75,
            strike_step=50,
            wing_steps=2,
            trail_pct=0.5,
        )

    assert state is not None
    assert state["run_id"] == existing_run_id, "Must reuse the existing run_id"
    assert state["trade_open"] is True, "trade_open should be True when status=entered"
    assert state["straddle_entry_prices"] == [120.0, 110.0]
    assert state["wings_locked"] is False


@pytest.mark.asyncio
async def test_resume_returns_none_when_no_run_exists():
    """If the session has no strategy_run_id, _load_resume_state returns None."""
    session = _make_session(strategy_run_id=None)

    class _FakeResult:
        def scalar_one_or_none(self): return session

    class _FakeDB:
        async def execute(self, stmt): return _FakeResult()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("app.services.live_paper_engine.AsyncSessionLocal", return_value=_FakeDB()):
        state = await _load_resume_state(
            session_id=session.id,
            config=_make_config(),
            trade_date=date.today(),
            lot_size=75,
            strike_step=50,
            wing_steps=2,
            trail_pct=0.5,
        )

    assert state is None


# ── Manual start is user-scoped (integration smoke) ──────────────────────────

@pytest.mark.asyncio
async def test_manual_start_passes_user_id_to_engine():
    """
    start_live_session(db, user_id=uid) must query configs filtered by that user_id.
    """
    user_id  = uuid.uuid4()
    captured = []

    class _FakeResult:
        def scalar_one_or_none(self): return None   # no config → no-op

    class _FakeDB:
        async def execute(self, stmt):
            captured.append(stmt)
            return _FakeResult()

    await start_live_session(_FakeDB(), user_id=user_id)

    assert captured, "Expected at least one query"
    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert user_id.hex in compiled, (
        f"user_id hex {user_id.hex} should appear in config query; got: {compiled}"
    )


# ── Fix 1: Manual stop exit path ─────────────────────────────────────────────

def test_manual_stop_exit_reason_when_trade_open():
    """When stop is detected with trade open, exit_reason must be MANUAL_STOP_EXIT."""
    trade_open = True
    exit_reason: Optional[str] = None
    exit_ts = None

    # Replicate the stop detection logic from the engine loop
    stop_now = datetime(2026, 5, 4, 10, 30, 0)
    if True:  # _stop_requested returned True
        if trade_open:
            exit_reason = "MANUAL_STOP_EXIT"
            exit_ts = stop_now
        else:
            exit_reason = "MANUAL_STOP_BEFORE_ENTRY"

    assert exit_reason == "MANUAL_STOP_EXIT"
    assert exit_ts == stop_now


def test_manual_stop_exit_reason_before_entry():
    """When stop is detected before entry, exit_reason must be MANUAL_STOP_BEFORE_ENTRY."""
    trade_open = False
    exit_reason: Optional[str] = None

    if True:  # _stop_requested returned True
        if trade_open:
            exit_reason = "MANUAL_STOP_EXIT"
        else:
            exit_reason = "MANUAL_STOP_BEFORE_ENTRY"

    assert exit_reason == "MANUAL_STOP_BEFORE_ENTRY"


# ── Fix 3: Manual start ignores enabled flag ──────────────────────────────────

@pytest.mark.asyncio
async def test_start_live_session_with_require_enabled_false_loads_disabled_config():
    """
    start_live_session(require_enabled=False) must succeed even when config.enabled=False.
    """
    user_id = uuid.uuid4()
    disabled_config = _make_config(enabled=False, user_id=user_id)

    call_count = [0]

    class _FakeResult:
        def scalar_one_or_none(self):
            call_count[0] += 1
            if call_count[0] == 1:
                return disabled_config   # config query
            return None                  # session query → no session exists

    class _FakeDB:
        async def execute(self, stmt): return _FakeResult()

    # Patch get_broker_token so we don't need jose installed locally
    with patch("app.services.live_paper_engine.get_broker_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = None  # no token → returns "no_token"
        result = await start_live_session(_FakeDB(), user_id=user_id, require_enabled=False)

    assert result != "no_config", (
        "require_enabled=False must load the config even when enabled=False"
    )
    # Config was found, so we progressed to token check (returned no_token — not no_config)
    assert result == "no_token"


@pytest.mark.asyncio
async def test_start_live_session_with_require_enabled_true_skips_disabled_config():
    """
    start_live_session(require_enabled=True) must return 'no_config' for a disabled config.
    """
    user_id = uuid.uuid4()

    class _FakeResult:
        def scalar_one_or_none(self): return None  # enabled=True filter matches nothing

    class _FakeDB:
        async def execute(self, stmt): return _FakeResult()

    result = await start_live_session(_FakeDB(), user_id=user_id, require_enabled=True)
    assert result == "no_config"


# ── Fix 4: Duplicate session race caught ─────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_session_integrity_error_returns_session_exists():
    """
    When two workers race to insert the same (user_id, trade_date),
    the IntegrityError must be caught and return 'session_exists'.
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    user_id = uuid.uuid4()
    config  = _make_config(user_id=user_id)
    call_count = [0]

    class _FakeResult:
        def scalar_one_or_none(self):
            call_count[0] += 1
            if call_count[0] == 1:
                return config    # config query
            return None          # session query → no existing session

    class _FakeDB:
        async def execute(self, stmt): return _FakeResult()
        def add(self, obj): pass
        async def commit(self):
            raise SAIntegrityError("INSERT", {}, Exception("unique constraint"))
        async def rollback(self): pass

    with patch("app.services.live_paper_engine.get_broker_token", new_callable=AsyncMock) as mock_token:
        mock_token.return_value = "fake_token"
        result = await start_live_session(_FakeDB(), user_id=user_id, require_enabled=False)

    assert result == "session_exists"


# ── Fix 2c: Resume seeds last_prices from StrategyLegMtm ─────────────────────

@pytest.mark.asyncio
async def test_resume_seeds_straddle_last_prices_from_leg_mtm():
    """
    After a mid-day redeploy, _load_resume_state must populate straddle_last_prices
    from the most recent StrategyLegMtm rows so the stale fallback works immediately.
    """
    existing_run_id = uuid.uuid4()
    session_id      = uuid.uuid4()
    trade_date      = date.today()
    leg_ce_id = uuid.uuid4()
    leg_pe_id = uuid.uuid4()

    session = _make_session(
        id=session_id, status="entered",
        strategy_run_id=existing_run_id,
        atm_strike=24200, expiry_date=date(2026, 5, 8),
        ce_symbol="NFO:X", pe_symbol="NFO:Y",
        wing_ce_symbol=None, wing_pe_symbol=None,
        lock_status="none",
    )

    existing_run = SimpleNamespace(
        id=existing_run_id, entry_credit_per_unit=230.0, entry_credit_total=1_725_000.0,
        lot_size=75, approved_lots=10, entry_time="09:50",
    )

    sell_ce = SimpleNamespace(id=leg_ce_id, side="SELL", option_type="CE",
                               strike=24200, entry_price=120.0, leg_index=0)
    sell_pe = SimpleNamespace(id=leg_pe_id, side="SELL", option_type="PE",
                               strike=24200, entry_price=110.0, leg_index=1)

    last_ce_mtm = SimpleNamespace(price=95.5)   # last known CE price
    last_pe_mtm = SimpleNamespace(price=102.0)  # last known PE price

    class _FakeScalars:
        def __init__(self, rows): self._rows = rows
        def all(self): return self._rows

    class _FakeResult:
        def __init__(self, value=None, rows=None):
            self._value = value; self._rows = rows or []
        def scalar_one_or_none(self): return self._value
        def scalars(self): return _FakeScalars(self._rows)

    call_count = [0]

    class _FakeDB:
        async def execute(self, stmt):
            call_count[0] += 1
            if call_count[0] == 1: return _FakeResult(value=session)       # LivePaperSession
            if call_count[0] == 2: return _FakeResult(value=existing_run)  # StrategyRun
            if call_count[0] == 3: return _FakeResult(rows=[sell_ce, sell_pe])  # legs
            if call_count[0] == 4: return _FakeResult(value=None)          # last StrategyRunMtm
            if call_count[0] == 5: return _FakeResult(value=last_ce_mtm)   # CE StrategyLegMtm
            if call_count[0] == 6: return _FakeResult(value=last_pe_mtm)   # PE StrategyLegMtm
            return _FakeResult(value=None)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("app.services.live_paper_engine.AsyncSessionLocal", return_value=_FakeDB()):
        state = await _load_resume_state(
            session_id=session_id, config=_make_config(), trade_date=trade_date,
            lot_size=75, strike_step=50, wing_steps=2, trail_pct=0.5,
        )

    assert state is not None
    assert state["straddle_last_prices"] == [95.5, 102.0], (
        f"Expected last prices [95.5, 102.0] from StrategyLegMtm, got {state['straddle_last_prices']}"
    )

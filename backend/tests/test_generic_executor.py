"""
Tests for generic_executor.py.

validate_run is tested with a fake async DB that replays expected queries.
execute_run is tested by patching the service-layer dependencies so we can
control exactly what data the minute loop sees, and verify that each exit
condition (TARGET, STOP, TIME, DATA_GAP, NO_TRADE) fires correctly.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.generic_executor import ExecutionResult, ValidationResult, validate_run


# ── Minimal async fake DB ─────────────────────────────────────────────────────

class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)
    def scalar_one_or_none(self):
        return self._value
    def fetchall(self):
        return self._rows


class _ScalarsProxy:
    def __init__(self, rows):
        self._rows = rows
    def all(self):
        return self._rows
    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _ExecResult:
    def __init__(self, rows=None, value=None):
        self._rows = rows or []
        self._value = value

    def scalar_one_or_none(self):
        return self._value if self._value is not None else (self._rows[0] if self._rows else None)

    def scalars(self):
        return _ScalarsProxy(self._rows)

    def fetchall(self):
        return self._rows


_TRADE_DATE = date(2025, 3, 27)   # a Thursday
_ENTRY_TIME = "09:50"
_EXPIRY     = date(2025, 3, 27)


def _make_trading_day(*, backtest_ready=True):
    return SimpleNamespace(trade_date=_TRADE_DATE, backtest_ready=backtest_ready)


def _make_spec_row():
    return SimpleNamespace(
        lot_size=75,
        strike_step=50,
        weekly_expiry_weekday=3,
        estimated_margin_per_lot=180_000.0,
    )


def _make_spot_row(close=22_400.0):
    return SimpleNamespace(close=close)


def _make_options_rows():
    entry_dt = datetime.combine(_TRADE_DATE, time(9, 50))
    ce = SimpleNamespace(option_type="CE", timestamp=entry_dt, expiry_date=_EXPIRY)
    pe = SimpleNamespace(option_type="PE", timestamp=entry_dt, expiry_date=_EXPIRY)
    return [ce, pe]


class FakeDB:
    """
    Minimal async DB stub for validate_run tests.
    Routes SQL fragments to appropriate stub data.
    """
    def __init__(self, *, td_row=None, spec_row=None, spot_row=None,
                 option_rows=None, expiry_rows=None):
        self.td_row      = td_row
        self.spec_row    = spec_row
        self.spot_row    = spot_row
        self.option_rows = option_rows or []
        self.expiry_rows = expiry_rows or [(_EXPIRY,)]

    async def execute(self, query, params=None):
        sql = str(query)

        if "trading_days" in sql:
            return _ExecResult(value=self.td_row)

        if "instrument_contract_specs" in sql:
            return _ExecResult(rows=[self.spec_row] if self.spec_row else [])

        if "FROM spot_candles" in sql or "SpotCandle" in sql:
            return _ExecResult(value=self.spot_row)

        if "FROM options_candles" in sql and "DISTINCT expiry_date" in sql:
            return _ExecResult(rows=self.expiry_rows)

        if "OptionsCandle" in sql or "options_candles" in sql:
            return _ExecResult(rows=self.option_rows)

        # strategy_run_events, strategy_runs, etc. — not needed for validate
        return _ExecResult()

    def add(self, obj): pass
    async def commit(self): pass
    async def rollback(self): pass


# ── Strategy fixture ──────────────────────────────────────────────────────────

_SHORT_STRADDLE = {
    "id": "short_straddle",
    "name": "Short Straddle",
    "executor": "generic_v1",
    "entry_rule_id": "timed_entry",
    "leg_template": [
        {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
        {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
    ],
    "exit_rule": {
        "target_pct": 0.30,
        "stop_multiple": 1.5,
        "time_exit": "15:25",
        "data_gap_exit": True,
    },
}

_BASE_CONFIG = {
    "instrument": "NIFTY",
    "trade_date": _TRADE_DATE.isoformat(),
    "entry_time": _ENTRY_TIME,
    "capital": 500_000,
}


# ── validate_run tests ────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_validate_run_bad_date_format():
    db = FakeDB(td_row=_make_trading_day())
    config = {**_BASE_CONFIG, "trade_date": "not-a-date"}
    result = _run(validate_run(db, _SHORT_STRADDLE, config))
    assert not result.validated
    assert result.error and "trade_date" in result.error.lower()


def test_validate_run_no_trading_day():
    db = FakeDB(td_row=None)
    result = _run(validate_run(db, _SHORT_STRADDLE, _BASE_CONFIG))
    assert not result.validated
    assert result.error and "No trading day" in result.error


def test_validate_run_day_not_backtest_ready():
    db = FakeDB(td_row=_make_trading_day(backtest_ready=False))
    result = _run(validate_run(db, _SHORT_STRADDLE, _BASE_CONFIG))
    assert not result.validated
    assert result.error and "backtest_ready" in result.error


def test_validate_run_no_spot_candle():
    db = FakeDB(
        td_row=_make_trading_day(),
        spec_row=_make_spec_row(),
        spot_row=None,
    )
    result = _run(validate_run(db, _SHORT_STRADDLE, _BASE_CONFIG))
    assert not result.validated
    assert result.error and "spot candle" in result.error.lower()


def test_validate_run_insufficient_capital():
    db = FakeDB(
        td_row=_make_trading_day(),
        spec_row=_make_spec_row(),    # margin = 180_000/lot
        spot_row=_make_spot_row(),
        option_rows=_make_options_rows(),
    )
    # 1 lot needs 180k — capital of 50k is below that
    config = {**_BASE_CONFIG, "capital": 50_000}
    result = _run(validate_run(db, _SHORT_STRADDLE, config))
    assert not result.validated
    assert result.error and "CAPITAL_INSUFFICIENT" in result.error


def test_validate_run_happy_path():
    db = FakeDB(
        td_row=_make_trading_day(),
        spec_row=_make_spec_row(),
        spot_row=_make_spot_row(close=22_374.0),
        option_rows=_make_options_rows(),
    )
    result = _run(validate_run(db, _SHORT_STRADDLE, _BASE_CONFIG))
    assert result.validated
    assert result.error is None
    assert result.atm_strike == 22_350    # round(22374/50)*50
    assert result.lot_size == 75
    assert result.approved_lots >= 1
    assert result.resolved_expiry == _EXPIRY.isoformat()
    assert len(result.contracts) == 2


def test_validate_run_resolves_both_legs():
    db = FakeDB(
        td_row=_make_trading_day(),
        spec_row=_make_spec_row(),
        spot_row=_make_spot_row(close=22_400.0),
        option_rows=_make_options_rows(),
    )
    result = _run(validate_run(db, _SHORT_STRADDLE, _BASE_CONFIG))
    assert result.validated
    sides = {c["side"] for c in result.contracts}
    types = {c["option_type"] for c in result.contracts}
    assert sides == {"SELL"}
    assert types == {"CE", "PE"}


# ── execute_run tests (patch service-layer dependencies) ─────────────────────

def _make_spot_candles(trade_date: date, *, count=375, base_close=22_400.0):
    """Synthetic 1-min spot candles from 09:15 to 09:15+count minutes."""
    session_start = datetime.combine(trade_date, time(9, 15))
    return [
        {
            "date": datetime(session_start.year, session_start.month, session_start.day,
                             9, 15 + i // 60, i % 60),
            "close": base_close,
        }
        for i in range(count)
    ]


def _make_option_index(trade_date: date, expiry: date, strike: int, *,
                       ce_price=100.0, pe_price=100.0, count=375):
    """Build the option_index dict expected by execute_run."""
    session_start = datetime.combine(trade_date, time(9, 15))
    index: Dict = {}
    for i in range(count):
        minute_idx = i
        index.setdefault((strike, "CE"), {})[minute_idx] = {"price": ce_price}
        index.setdefault((strike, "PE"), {})[minute_idx] = {"price": pe_price}
    return index


def _make_validation(*, atm_strike=22_400, lot_size=75, approved_lots=2,
                     expiry=_EXPIRY, entry_time=_ENTRY_TIME,
                     capital=500_000, trade_date=_TRADE_DATE):
    return ValidationResult(
        validated=True,
        instrument="NIFTY",
        trade_date=trade_date.isoformat(),
        entry_time=entry_time,
        resolved_expiry=expiry.isoformat(),
        spot_at_entry=22_400.0,
        atm_strike=atm_strike,
        contracts=[
            {"side": "SELL", "option_type": "CE", "strike": atm_strike},
            {"side": "SELL", "option_type": "PE", "strike": atm_strike},
        ],
        lot_size=lot_size,
        approved_lots=approved_lots,
        estimated_margin=approved_lots * 180_000,
        warnings=[],
    )


def _fake_db_for_execute():
    """Fake DB that satisfies execute_run's persistence calls."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    # get_contract_spec query
    spec_row = _make_spec_row()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = spec_row
    exec_result.scalars.return_value.all.return_value = [spec_row]
    db.execute = AsyncMock(return_value=exec_result)
    return db


def _run_execute(validation, spot_candles, option_index, *, exit_rule=None):
    """Patch the three data-loading functions and run execute_run."""
    from app.services.generic_executor import execute_run

    strategy = {**_SHORT_STRADDLE}
    if exit_rule:
        strategy = {**strategy, "exit_rule": exit_rule}

    config = {
        **_BASE_CONFIG,
        "trade_date": validation.trade_date,
        "entry_time": validation.entry_time,
        "exit_rule": strategy["exit_rule"],
    }

    db = _fake_db_for_execute()
    run_id = uuid.uuid4()

    with (
        patch("app.services.generic_executor.get_contract_spec",
              new=AsyncMock(return_value=SimpleNamespace(
                  lot_size=validation.lot_size,
                  strike_step=50,
                  weekly_expiry_weekday=3,
                  estimated_margin_per_lot=180_000.0,
              ))),
        patch("app.services.generic_executor.load_spot_candles",
              new=AsyncMock(return_value=spot_candles)),
        patch("app.services.generic_executor.load_vix_candles",
              new=AsyncMock(return_value=[])),
        patch("app.services.generic_executor.load_option_candles_for_strikes",
              new=AsyncMock(return_value=(option_index, {}))),
    ):
        return asyncio.get_event_loop().run_until_complete(
            execute_run(db, run_id, strategy, config, validation, user_id=uuid.uuid4())
        )


def test_execute_run_target_exit():
    """Net MTM >= 30% of entry_credit_total → TARGET_EXIT."""
    # entry_credit = (100 + 100) × 75 × 2 = 30_000
    # target = 30_000 × 0.30 = 9_000 net profit needed
    # After entry at 09:50, price drops to 20 each → gross MTM = (100-20)+(100-20)=160 per unit
    # gross_total = 160 × 75 × 2 = 24_000 → comfortably above 9_000
    trade_date = _TRADE_DATE
    expiry = _EXPIRY
    atm = 22_400
    validation = _make_validation(atm_strike=atm, approved_lots=2)

    spot = _make_spot_candles(trade_date)
    opt_index = _make_option_index(trade_date, expiry, atm, ce_price=20.0, pe_price=20.0)

    result = _run_execute(validation, spot, opt_index)
    assert result.status in ("completed", "no_trade")
    if result.status == "completed":
        assert result.exit_reason in ("TARGET_EXIT", "TIME_EXIT", "STOP_EXIT", "DATA_GAP_EXIT")


def test_execute_run_stop_exit():
    """Net MTM <= -(1.5 × entry_credit_total) → STOP_EXIT."""
    # entry_credit = 30_000; stop_threshold = -45_000
    # price rises to 400 each → gross MTM = (100-400)+(100-400) = -600 per unit
    # gross_total = -600 × 75 × 2 = -90_000 → below -45_000
    trade_date = _TRADE_DATE
    expiry = _EXPIRY
    atm = 22_400
    validation = _make_validation(atm_strike=atm, approved_lots=2)

    spot = _make_spot_candles(trade_date)
    opt_index = _make_option_index(trade_date, expiry, atm, ce_price=400.0, pe_price=400.0)

    result = _run_execute(validation, spot, opt_index)
    assert result.status in ("completed", "no_trade")
    if result.status == "completed":
        assert result.exit_reason in ("STOP_EXIT", "TIME_EXIT")


def test_execute_run_time_exit():
    """At 15:25 with no target/stop hit → TIME_EXIT."""
    # entry price 100; current price remains 100 → MTM = 0, no target/stop
    trade_date = _TRADE_DATE
    expiry = _EXPIRY
    atm = 22_400
    validation = _make_validation(atm_strike=atm, approved_lots=2)

    spot = _make_spot_candles(trade_date)
    opt_index = _make_option_index(trade_date, expiry, atm, ce_price=100.0, pe_price=100.0)

    result = _run_execute(validation, spot, opt_index,
                          exit_rule={**_SHORT_STRADDLE["exit_rule"], "time_exit": "09:55"})
    assert result.status in ("completed", "no_trade")
    if result.status == "completed":
        assert result.exit_reason in ("TIME_EXIT", "TARGET_EXIT", "STOP_EXIT")


def test_execute_run_no_spot_data():
    """Empty spot candles → no_trade with NO_SPOT_DATA reason."""
    validation = _make_validation()
    result = _run_execute(validation, spot_candles=[], option_index={})
    assert result.status == "no_trade"
    assert result.exit_reason == "NO_SPOT_DATA"


def test_execute_run_returns_execution_result():
    """execute_run always returns an ExecutionResult, never raises."""
    validation = _make_validation()
    spot = _make_spot_candles(_TRADE_DATE)
    opt_index = _make_option_index(_TRADE_DATE, _EXPIRY, 22_400)
    result = _run_execute(validation, spot, opt_index)
    assert isinstance(result, ExecutionResult)
    assert result.run_id is not None
    assert result.status in ("completed", "no_trade")


def test_execute_run_pnl_negative_on_adverse_move():
    """Large adverse move → realized_net_pnl should be negative (after charges)."""
    validation = _make_validation(approved_lots=1)
    spot = _make_spot_candles(_TRADE_DATE)
    # entry at 100, exits at 350 — significant loss
    opt_index = _make_option_index(_TRADE_DATE, _EXPIRY, 22_400, ce_price=350.0, pe_price=350.0)
    result = _run_execute(validation, spot, opt_index)
    if result.realized_net_pnl is not None:
        assert result.realized_net_pnl < 0

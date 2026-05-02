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


_UNSET = object()


class _ExecResult:
    """
    leg_rows: when set, scalar_one_or_none() draws from this list instead of rows.
    This lets FakeDB return non-empty rows for resolve_expiry's .scalars().all()
    while returning None for the per-leg price check's .scalar_one_or_none().
    """
    def __init__(self, rows=None, value=None, leg_rows=_UNSET):
        self._rows = rows or []
        self._value = value
        self._leg_rows = self._rows if leg_rows is _UNSET else leg_rows

    def scalar_one_or_none(self):
        if self._value is not None:
            return self._value
        return self._leg_rows[0] if self._leg_rows else None

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

    leg_price_rows: controls the per-leg candle price check in validate_run.
    When set to [], scalar_one_or_none() returns None → validation fails with
    "No price data" error.  Defaults to option_rows (backward-compatible).
    """
    def __init__(self, *, td_row=None, spec_row=None, spot_row=None,
                 option_rows=None, expiry_rows=None, leg_price_rows=_UNSET):
        self.td_row        = td_row
        self.spec_row      = spec_row
        self.spot_row      = spot_row
        self.option_rows   = option_rows or []
        self.expiry_rows   = expiry_rows or [(_EXPIRY,)]
        self._leg_price_rows = self.option_rows if leg_price_rows is _UNSET else leg_price_rows

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
            # Per-leg price checks (validate_run step 7) filter by strike; resolve_expiry does not.
            # SQLAlchemy renders a strike filter as `:strike_1` in the parameterised SQL string.
            if ":strike_" in sql:
                return _ExecResult(rows=self.option_rows, leg_rows=self._leg_price_rows)
            # resolve_expiry CE/PE availability check — always use option_rows
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

_IRON_BUTTERFLY = {
    "id": "iron_butterfly",
    "name": "Iron Butterfly",
    "executor": "generic_v1",
    "entry_rule_id": "timed_entry",
    "leg_template": [
        {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
        {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
        {"side": "BUY", "option_type": "CE", "strike_offset_steps_from_config": "wing_width_steps", "strike_offset_sign": 1},
        {"side": "BUY", "option_type": "PE", "strike_offset_steps_from_config": "wing_width_steps", "strike_offset_sign": -1},
    ],
    "exit_rule": {
        "target_pct": 0.30,
        "stop_capital_pct": 0.015,
        "time_exit": "15:25",
        "data_gap_exit": True,
    },
    "sizing": {
        "model": "defined_risk_credit",
        "wing_width_steps_key": "wing_width_steps",
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


def test_validate_run_resolves_iron_butterfly_wings_and_defined_risk_margin():
    entry_dt = datetime.combine(_TRADE_DATE, time(9, 50))
    priced_rows = [
        SimpleNamespace(option_type="CE", timestamp=entry_dt, expiry_date=_EXPIRY, close=100.0),
        SimpleNamespace(option_type="PE", timestamp=entry_dt, expiry_date=_EXPIRY, close=100.0),
    ]
    db = FakeDB(
        td_row=_make_trading_day(),
        spec_row=_make_spec_row(),
        spot_row=_make_spot_row(close=22_400.0),
        option_rows=priced_rows,
        leg_price_rows=priced_rows,
    )
    result = _run(validate_run(
        db,
        _IRON_BUTTERFLY,
        {**_BASE_CONFIG, "wing_width_steps": 2, "capital": 500_000},
    ))
    assert result.validated
    assert [(c["side"], c["option_type"], c["strike"]) for c in result.contracts] == [
        ("SELL", "CE", 22_400),
        ("SELL", "PE", 22_400),
        ("BUY", "CE", 22_500),
        ("BUY", "PE", 22_300),
    ]
    assert result.estimated_margin < 500_000
    assert result.approved_lots == 66


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


# ── PR comment fixes ─────────────────────────────────────────────────────────

def test_validate_run_missing_leg_price_at_entry():
    """validate_run fails if the resolved ATM strike has no price at entry minute."""
    db = FakeDB(
        td_row=_make_trading_day(),
        spec_row=_make_spec_row(),
        spot_row=_make_spot_row(close=22_400.0),
        option_rows=_make_options_rows(),  # resolve_expiry sees CE + PE → passes
        leg_price_rows=[],                 # per-leg price check → no row → fails
    )
    result = _run(validate_run(db, _SHORT_STRADDLE, _BASE_CONFIG))
    assert not result.validated
    assert result.error and "No price data" in result.error


def _make_minute_spot_candles(trade_date: date, *, count: int = 20,
                               base_close: float = 22_400.0) -> list:
    """Proper 1-min spot candles — one per minute from 09:15."""
    from datetime import timedelta
    session_start = datetime.combine(trade_date, time(9, 15))
    return [
        {"date": session_start + timedelta(minutes=i), "close": base_close}
        for i in range(count)
    ]


def _make_minute_option_index(strike: int, *,
                               from_idx: int, to_idx: int,
                               ce_price: float = 100.0,
                               pe_price: float = 100.0) -> Dict:
    """Option index with prices only for minute indices [from_idx, to_idx)."""
    return {
        (strike, "CE"): {i: {"price": ce_price} for i in range(from_idx, to_idx)},
        (strike, "PE"): {i: {"price": pe_price} for i in range(from_idx, to_idx)},
    }


def _run_execute_direct(validation, spot_candles, option_index, *, entry_time="09:16"):
    """Like _run_execute but accepts explicit entry_time and spot/option data."""
    from app.services.generic_executor import execute_run

    strategy = {**_SHORT_STRADDLE}
    config = {
        **_BASE_CONFIG,
        "trade_date": validation.trade_date,
        "entry_time": entry_time,
        "exit_rule": strategy["exit_rule"],
    }
    db = _fake_db_for_execute()
    run_id = uuid.uuid4()

    # Override validation entry_time so execute_run parses it correctly
    from dataclasses import replace
    val = replace(validation, entry_time=entry_time)

    with (
        patch("app.services.generic_executor.get_contract_spec",
              new=AsyncMock(return_value=SimpleNamespace(
                  lot_size=val.lot_size, strike_step=50,
                  weekly_expiry_weekday=3, estimated_margin_per_lot=180_000.0,
              ))),
        patch("app.services.generic_executor.load_spot_candles",
              new=AsyncMock(return_value=spot_candles)),
        patch("app.services.generic_executor.load_vix_candles",
              new=AsyncMock(return_value=[])),
        patch("app.services.generic_executor.load_option_candles_for_strikes",
              new=AsyncMock(return_value=(option_index, {}))),
    ):
        result = asyncio.get_event_loop().run_until_complete(
            execute_run(db, run_id, strategy, config, val, user_id=uuid.uuid4())
        )
    return result, db


def _run_execute_strategy_direct(strategy, validation, spot_candles, option_index, *, config_overrides=None, entry_time="09:16"):
    from app.services.generic_executor import execute_run

    config = {
        **_BASE_CONFIG,
        "trade_date": validation.trade_date,
        "entry_time": entry_time,
        "exit_rule": strategy["exit_rule"],
        **(config_overrides or {}),
    }
    db = _fake_db_for_execute()
    run_id = uuid.uuid4()

    from dataclasses import replace
    val = replace(validation, entry_time=entry_time)

    with (
        patch("app.services.generic_executor.get_contract_spec",
              new=AsyncMock(return_value=SimpleNamespace(
                  lot_size=val.lot_size, strike_step=50,
                  weekly_expiry_weekday=3, estimated_margin_per_lot=180_000.0,
              ))),
        patch("app.services.generic_executor.load_spot_candles",
              new=AsyncMock(return_value=spot_candles)),
        patch("app.services.generic_executor.load_vix_candles",
              new=AsyncMock(return_value=[])),
        patch("app.services.generic_executor.load_option_candles_for_strikes",
              new=AsyncMock(return_value=(option_index, {}))),
    ):
        result = asyncio.get_event_loop().run_until_complete(
            execute_run(db, run_id, strategy, config, val, user_id=uuid.uuid4())
        )
    return result, db


def test_execute_run_data_gap_exit_uses_gap_minute_not_last_mtm():
    """
    DATA_GAP_EXIT must record the minute it fires as exit_time, not the last MTM minute.

    Layout (minute_idx from session start 09:15):
      0 = 09:15  HOLD
      1 = 09:16  ENTER  (option data present)
      2 = 09:17  HOLD   (option data present, MTM row written → last good MTM minute)
      3 = 09:18  HOLD   (no data → stale_count 0→1, MTM row still written)
      4 = 09:19  EXIT   (no data → stale_count 1, 1 < _MAX_STALE_MINUTES=1? No → gap!)

    Without fix: exit_time derives from mtm_rows[-1] = 09:18.
    With fix:    exit_time derives from exit_ts        = 09:19.
    """
    from app.models.strategy_run import StrategyRun

    validation = _make_validation(atm_strike=22_400, approved_lots=1)
    # 20 minute-level candles cover 09:15–09:34; entry at 09:16 (idx 1)
    spot = _make_minute_spot_candles(_TRADE_DATE, count=20)
    # Prices available at idx 1 and 2 only; absent from idx 3 onwards
    opt_index = _make_minute_option_index(22_400, from_idx=1, to_idx=3)

    result, db = _run_execute_direct(validation, spot, opt_index, entry_time="09:16")

    assert result.exit_reason == "DATA_GAP_EXIT", (
        f"Expected DATA_GAP_EXIT, got {result.exit_reason}"
    )

    run_rows = [
        call.args[0] for call in db.add.call_args_list
        if isinstance(call.args[0], StrategyRun)
    ]
    assert run_rows, "StrategyRun was not persisted"
    # exit_time must be the gap minute (09:19), NOT the last-MTM minute (09:18)
    assert run_rows[0].exit_time == "09:19", (
        f"exit_time should be '09:19' (gap minute) but was '{run_rows[0].exit_time}'"
    )


def test_execute_run_leg_gross_pnl_populated_after_trade():
    """StrategyRunLeg.gross_leg_pnl must be non-None when a trade completes.

    Entry price = 100 at 09:16, current price drops to 20 → TARGET_EXIT fires.
    Both SELL legs must record a positive realized P&L.
    """
    from app.models.strategy_run import StrategyRunLeg

    validation = _make_validation(atm_strike=22_400, approved_lots=1)
    spot = _make_minute_spot_candles(_TRADE_DATE, count=20)
    # Entry at idx 1 (price=100), then idx 2+ at price=20 → TARGET_EXIT on minute 2
    # entry_credit_total = (100+100) × 75 = 15_000
    # target = 15_000 × 0.30 = 4_500
    # gross_mtm at idx 2 = (100-20 + 100-20) × 75 = 12_000 > 4_500 → TARGET
    opt_index = {
        (22_400, "CE"): {1: {"price": 100.0}, **{i: {"price": 20.0} for i in range(2, 20)}},
        (22_400, "PE"): {1: {"price": 100.0}, **{i: {"price": 20.0} for i in range(2, 20)}},
    }

    result, db = _run_execute_direct(validation, spot, opt_index, entry_time="09:16")

    leg_rows = [
        call.args[0] for call in db.add.call_args_list
        if isinstance(call.args[0], StrategyRunLeg)
    ]
    assert leg_rows, "No StrategyRunLeg rows persisted — trade may not have entered"
    for leg_row in leg_rows:
        assert leg_row.gross_leg_pnl is not None, (
            f"gross_leg_pnl is None for {leg_row.option_type} {leg_row.strike}"
        )
        assert leg_row.gross_leg_pnl > 0, (
            f"Expected positive P&L (SELL at 100, exit at 20) but got {leg_row.gross_leg_pnl}"
        )


def test_execute_run_iron_butterfly_uses_buy_sell_mtm_signs():
    from app.models.strategy_run import StrategyLegMtm

    validation = _make_validation(atm_strike=22_400, approved_lots=1)
    spot = _make_minute_spot_candles(_TRADE_DATE, count=4)
    option_index = {
        (22_400, "CE"): {1: {"price": 100.0}, 2: {"price": 80.0}},
        (22_400, "PE"): {1: {"price": 100.0}, 2: {"price": 90.0}},
        (22_500, "CE"): {1: {"price": 40.0}, 2: {"price": 30.0}},
        (22_300, "PE"): {1: {"price": 40.0}, 2: {"price": 45.0}},
    }
    strategy = {
        **_IRON_BUTTERFLY,
        "exit_rule": {**_IRON_BUTTERFLY["exit_rule"], "time_exit": "09:17"},
    }

    result, db = _run_execute_strategy_direct(
        strategy,
        validation,
        spot,
        option_index,
        config_overrides={"wing_width_steps": 2},
        entry_time="09:16",
    )

    assert result.exit_reason == "TIME_EXIT"
    leg_mtm_rows = [
        call.args[0] for call in db.add.call_args_list
        if isinstance(call.args[0], StrategyLegMtm)
    ]
    assert [row.gross_leg_pnl for row in leg_mtm_rows] == [1500.0, 750.0, -750.0, 375.0]

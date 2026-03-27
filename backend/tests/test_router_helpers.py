"""
Unit tests for router helper functions in app/routers/backtest.py

Tests _trading_days and _to_dict without starting FastAPI or a database.
"""
import uuid
from datetime import date, time
from unittest.mock import MagicMock

import pytest

from app.routers.backtest import _to_dict, _trading_days


# ── _trading_days ─────────────────────────────────────────────────────────────

class TestTradingDaysPositive:
    def test_full_week_returns_5_days(self):
        # Mon 2025-01-06 → Fri 2025-01-10
        days = _trading_days(date(2025, 1, 6), date(2025, 1, 10))
        assert len(days) == 5

    def test_returns_only_weekdays(self):
        # Range that includes a weekend
        days = _trading_days(date(2025, 1, 6), date(2025, 1, 12))  # Mon–Sun
        for d in days:
            assert d.weekday() < 5   # Mon=0 … Fri=4

    def test_single_monday_returns_one_day(self):
        days = _trading_days(date(2025, 1, 6), date(2025, 1, 6))
        assert days == [date(2025, 1, 6)]

    def test_dates_are_in_ascending_order(self):
        days = _trading_days(date(2025, 1, 6), date(2025, 1, 17))
        assert days == sorted(days)

    def test_returns_list_of_date_objects(self):
        days = _trading_days(date(2025, 1, 6), date(2025, 1, 10))
        assert all(isinstance(d, date) for d in days)

    def test_two_weeks_returns_10_days(self):
        days = _trading_days(date(2025, 1, 6), date(2025, 1, 17))
        assert len(days) == 10

    def test_same_start_and_end_friday_included(self):
        days = _trading_days(date(2025, 1, 10), date(2025, 1, 10))  # Friday
        assert len(days) == 1


class TestTradingDaysNegative:
    def test_saturday_only_range_returns_empty(self):
        days = _trading_days(date(2025, 1, 11), date(2025, 1, 11))  # Saturday
        assert days == []

    def test_weekend_only_range_returns_empty(self):
        days = _trading_days(date(2025, 1, 11), date(2025, 1, 12))  # Sat–Sun
        assert days == []

    def test_end_before_start_returns_empty(self):
        days = _trading_days(date(2025, 1, 10), date(2025, 1, 6))
        assert days == []

    def test_sunday_single_day_returns_empty(self):
        days = _trading_days(date(2025, 1, 12), date(2025, 1, 12))
        assert days == []


# ── _to_dict ──────────────────────────────────────────────────────────────────

def _make_session(**overrides):
    """
    Return a MagicMock that mimics a BacktestSession ORM object
    with sensible defaults.
    """
    session_id = uuid.uuid4()
    s = MagicMock()
    s.id = session_id
    s.instrument = "NIFTY"
    s.session_date = date(2025, 1, 6)
    s.capital = 500_000.0
    s.regime = "BULLISH"
    s.iv_rank = 40
    s.strategy = "IRON_CONDOR"
    s.entry_time = time(9, 30)
    s.exit_time = time(15, 15)
    s.exit_reason = "END_OF_DAY"
    s.spot_in = 22000.0
    s.spot_out = 22050.0
    s.lots = 2
    s.max_profit = 1800.0
    s.max_loss = 8200.0
    s.pnl = 500.0
    s.pnl_pct = 0.1
    s.wl = "WIN"
    s.ema5 = 22010.0
    s.ema20 = 21960.0
    s.rsi14 = 55.0
    s.created_at = None
    s.legs = [{"act": "SELL", "typ": "CE", "strike": 22150}]
    s.min_data = [{"time": "09:30", "spot": 22000.0, "pnl": 0.0}]
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestToDictPositive:
    REQUIRED_KEYS = {
        "id", "instrument", "session_date", "capital", "regime", "iv_rank",
        "strategy", "entry_time", "exit_time", "exit_reason",
        "spot_in", "spot_out", "lots", "max_profit", "max_loss",
        "pnl", "pnl_pct", "wl", "ema5", "ema20", "rsi14", "created_at",
    }

    def test_all_required_keys_present(self):
        s = _make_session()
        d = _to_dict(s, full=True)
        assert self.REQUIRED_KEYS.issubset(d.keys())

    def test_full_true_includes_legs_and_min_data(self):
        s = _make_session()
        d = _to_dict(s, full=True)
        assert "legs" in d
        assert "min_data" in d

    def test_full_false_excludes_legs_and_min_data(self):
        s = _make_session()
        d = _to_dict(s, full=False)
        assert "legs" not in d
        assert "min_data" not in d

    def test_id_is_string(self):
        s = _make_session()
        d = _to_dict(s)
        assert isinstance(d["id"], str)

    def test_session_date_is_string(self):
        s = _make_session()
        d = _to_dict(s)
        assert isinstance(d["session_date"], str)

    def test_capital_is_float(self):
        s = _make_session()
        d = _to_dict(s)
        assert isinstance(d["capital"], float)

    def test_pnl_is_float(self):
        s = _make_session()
        d = _to_dict(s)
        assert isinstance(d["pnl"], float)

    def test_entry_time_serialised_as_string(self):
        s = _make_session(entry_time=time(9, 30))
        d = _to_dict(s)
        assert isinstance(d["entry_time"], str)

    def test_legs_content_preserved(self):
        s = _make_session()
        d = _to_dict(s, full=True)
        assert d["legs"] == s.legs

    def test_min_data_content_preserved(self):
        s = _make_session()
        d = _to_dict(s, full=True)
        assert d["min_data"] == s.min_data


class TestToDictNegative:
    def test_none_entry_time_returns_none(self):
        s = _make_session(entry_time=None)
        d = _to_dict(s)
        assert d["entry_time"] is None

    def test_none_exit_time_returns_none(self):
        s = _make_session(exit_time=None)
        d = _to_dict(s)
        assert d["exit_time"] is None

    def test_none_pnl_defaults_to_zero(self):
        s = _make_session(pnl=None)
        d = _to_dict(s)
        assert d["pnl"] == 0.0

    def test_none_pnl_pct_defaults_to_zero(self):
        s = _make_session(pnl_pct=None)
        d = _to_dict(s)
        assert d["pnl_pct"] == 0.0

    def test_none_spot_in_returns_none(self):
        s = _make_session(spot_in=None)
        d = _to_dict(s)
        assert d["spot_in"] is None

    def test_none_spot_out_returns_none(self):
        s = _make_session(spot_out=None)
        d = _to_dict(s)
        assert d["spot_out"] is None

    def test_none_legs_returns_empty_list(self):
        s = _make_session(legs=None)
        d = _to_dict(s, full=True)
        assert d["legs"] == []

    def test_none_min_data_returns_empty_list(self):
        s = _make_session(min_data=None)
        d = _to_dict(s, full=True)
        assert d["min_data"] == []

    def test_none_ema5_returns_none(self):
        s = _make_session(ema5=None)
        d = _to_dict(s)
        assert d["ema5"] is None

    def test_none_max_profit_returns_none(self):
        s = _make_session(max_profit=None)
        d = _to_dict(s)
        assert d["max_profit"] is None

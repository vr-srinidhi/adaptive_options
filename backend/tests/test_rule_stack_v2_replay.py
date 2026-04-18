"""
Deterministic ORB replay tests for the v2 rule stack.

These are fixture-backed integration tests in the same style as test_apr7_10_replay:
the Zerodha layer is mocked so the engine replays against known minute candles.
"""
import uuid
from datetime import date, datetime
from unittest.mock import patch

import pytest

from app.services.paper_engine import run_paper_engine

TOKEN_SPOT = 256265

TOKEN_22100CE_MAR12 = 3102
TOKEN_22150CE_MAR12 = 3103
TOKEN_22100CE_MAR05 = 3202
TOKEN_22150CE_MAR05 = 3203
TOKEN_22100CE_FEB19 = 3302
TOKEN_22150CE_FEB19 = 3303


def _c(year, month, day, hour, minute, close, high=None, low=None):
    return {
        "date": datetime(year, month, day, hour, minute),
        "open": float(close),
        "high": float(high if high is not None else close),
        "low": float(low if low is not None else close),
        "close": float(close),
        "volume": 100,
        "oi": 1_000,
    }


def _or_window(year, month, day):
    candles = [_c(year, month, day, 9, 15, 22000, high=22100.0, low=21900.0)]
    for i in range(1, 15):
        candles.append(_c(year, month, day, 9, 15 + i, 22000))
    return candles


def _post_or(year, month, day, closes):
    out = []
    for i, close in enumerate(closes):
        total_min = 9 * 60 + 30 + i
        h, m = divmod(total_min, 60)
        out.append(_c(year, month, day, h, m, close))
    return out


def _option_series(year, month, day, closes):
    out = []
    for i, close in enumerate(closes):
        total_min = 9 * 60 + 15 + i
        h, m = divmod(total_min, 60)
        out.append(_c(year, month, day, h, m, close))
    return out


def _inst(name, inst_type, strike, expiry, token, lot_size=75):
    return {
        "name": name,
        "instrument_type": inst_type,
        "strike": strike,
        "expiry": expiry,
        "instrument_token": token,
        "lot_size": lot_size,
    }


@pytest.mark.integration
class TestMar10TrailExit:
    TRADE_DATE = date(2026, 3, 10)
    EXPIRY = date(2026, 3, 12)

    INSTRUMENTS = [
        _inst("NIFTY", "CE", 22100, EXPIRY, TOKEN_22100CE_MAR12),
        _inst("NIFTY", "CE", 22150, EXPIRY, TOKEN_22150CE_MAR12),
    ]

    SPOT = _or_window(2026, 3, 10) + _post_or(2026, 3, 10, [
        22130.0,  # idx 15 tentative
        22130.0,  # idx 16 confirmed -> ENTER
        22140.0,  # idx 17 trail arms
        22132.0,  # idx 18 giveback -> EXIT_TRAIL
        22120.0,  # idx 19 session complete
    ])

    CE_LONG = _option_series(2026, 3, 10, [50.0] * 15 + [55.0, 60.0, 62.35, 61.20, 61.00])
    CE_SHORT = _option_series(2026, 3, 10, [25.0] * 15 + [27.0, 30.0, 30.0, 30.0, 30.0])

    def _fetch(self, token, trade_date, access_token):
        return {
            TOKEN_SPOT: self.SPOT,
            TOKEN_22100CE_MAR12: self.CE_LONG,
            TOKEN_22150CE_MAR12: self.CE_SHORT,
        }.get(token, [])

    def _run(self):
        with patch("app.services.paper_engine.fetch_candles_with_token", side_effect=self._fetch), patch(
            "app.services.paper_engine.get_instruments_with_token",
            return_value=self.INSTRUMENTS,
        ):
            return run_paper_engine(uuid.uuid4(), self.TRADE_DATE, "NIFTY", 2_500_000, "tok")

    def test_trail_exit_fires_and_audits(self):
        result = self._run()
        trade = result["trade_header"]
        assert trade is not None
        assert trade["exit_reason"] == "EXIT_TRAIL"
        assert -3_000 <= trade["realized_net_pnl"] <= 3_000

        reason_codes = [d["reason_code"] for d in result["decisions"]]
        assert "TRAIL_ARMED" in reason_codes
        assert "EXIT_TRAIL" in reason_codes


@pytest.mark.integration
class TestMar02EntryCutoff:
    TRADE_DATE = date(2026, 3, 2)
    EXPIRY = date(2026, 3, 5)

    INSTRUMENTS = [
        _inst("NIFTY", "CE", 22100, EXPIRY, TOKEN_22100CE_MAR05),
        _inst("NIFTY", "CE", 22150, EXPIRY, TOKEN_22150CE_MAR05),
    ]

    SPOT = _or_window(2026, 3, 2) + _post_or(
        2026,
        3,
        2,
        [22050.0] * 266 + [22130.0, 22130.0, 22050.0],
    )
    CE_LONG = _option_series(2026, 3, 2, [50.0] * len(SPOT))
    CE_SHORT = _option_series(2026, 3, 2, [25.0] * len(SPOT))

    def _fetch(self, token, trade_date, access_token):
        return {
            TOKEN_SPOT: self.SPOT,
            TOKEN_22100CE_MAR05: self.CE_LONG,
            TOKEN_22150CE_MAR05: self.CE_SHORT,
        }.get(token, [])

    def _run(self):
        with patch("app.services.paper_engine.fetch_candles_with_token", side_effect=self._fetch), patch(
            "app.services.paper_engine.get_instruments_with_token",
            return_value=self.INSTRUMENTS,
        ):
            return run_paper_engine(uuid.uuid4(), self.TRADE_DATE, "NIFTY", 2_500_000, "tok")

    def test_cutoff_blocks_late_entry(self):
        result = self._run()
        assert result["trade_header"] is None
        assert result["minute_marks"] == []

        late_rows = [
            d for d in result["decisions"]
            if d["timestamp"].strftime("%H:%M") == "13:57"
        ]
        assert len(late_rows) == 1
        assert late_rows[0]["reason_code"] == "TOO_LATE_TO_ENTER"
        assert late_rows[0]["rejection_gate"] == "G0"


@pytest.mark.integration
class TestFeb16StillTargets:
    TRADE_DATE = date(2026, 2, 16)
    EXPIRY = date(2026, 2, 19)

    INSTRUMENTS = [
        _inst("NIFTY", "CE", 22100, EXPIRY, TOKEN_22100CE_FEB19),
        _inst("NIFTY", "CE", 22150, EXPIRY, TOKEN_22150CE_FEB19),
    ]

    SPOT = _or_window(2026, 2, 16) + _post_or(2026, 2, 16, [
        22130.0,  # idx 15 tentative
        22130.0,  # idx 16 confirmed -> ENTER
        22131.0,  # idx 17 below trail-arm threshold
        22220.0,  # idx 18 direct EXIT_TARGET
        22230.0,  # idx 19 session complete
    ])

    CE_LONG = _option_series(2026, 2, 16, [50.0] * 15 + [55.0, 60.0, 62.0, 63.212, 63.0])
    CE_SHORT = _option_series(2026, 2, 16, [25.0] * 15 + [27.0, 30.0, 30.0, 25.0, 25.0])

    def _fetch(self, token, trade_date, access_token):
        return {
            TOKEN_SPOT: self.SPOT,
            TOKEN_22100CE_FEB19: self.CE_LONG,
            TOKEN_22150CE_FEB19: self.CE_SHORT,
        }.get(token, [])

    def _run(self):
        with patch("app.services.paper_engine.fetch_candles_with_token", side_effect=self._fetch), patch(
            "app.services.paper_engine.get_instruments_with_token",
            return_value=self.INSTRUMENTS,
        ):
            return run_paper_engine(uuid.uuid4(), self.TRADE_DATE, "NIFTY", 2_500_000, "tok")

    def test_session_still_hits_target(self):
        result = self._run()
        trade = result["trade_header"]
        assert trade is not None
        assert trade["exit_reason"] == "EXIT_TARGET"
        assert trade["realized_net_pnl"] == pytest.approx(13_194.48, abs=100.0)
        assert "EXIT_TRAIL" not in [d["reason_code"] for d in result["decisions"]]

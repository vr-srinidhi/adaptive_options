"""
Fixture-backed ORB replay tests for Apr 7–10 2026.

Each test mocks the Zerodha API layer so that run_paper_engine() replays
against a known, deterministic candle fixture instead of live data.

Scenarios
---------
  Apr 7  — Bullish two-step breakout → EXIT_TARGET
  Apr 8  — Bearish two-step breakout → EXIT_STOP
  Apr 9  — No breakout all day → no trade (final_session_state = OBSERVING)
  Apr 10 — Confirmed breakout but option prices stale → STALE_OPTION_PRICE rejection

Fixture design
--------------
  OR_HIGH = 22100, OR_LOW = 21900  (consistent across all four days)
  Breakout thresholds (breakout_buffer_pct = 0.001):
    Bull breakout  : close > 22100 * 1.001 = 22122.1  → use 22130
    Bear breakout  : close < 21900 * 0.999 = 21879.1  → use 21875

  Two-step pattern (G4 follow-through):
    idx 15 (09:30) : first breakout candle — G4 fails (prev is inside OR)
    idx 16 (09:31) : second breakout candle — G4 passes → ENTER (unless stale)

  Trade parameters at entry (Bull, idx 16):
    22100CE long  @ 60,  22150CE short @ 30  → debit = 30
    max_loss_per_lot = 30 * 75 = 2250
    approved_lots    = floor(50000 / 2250) = 22
    total_max_loss   = 49500,  target = 2_500_000 * 0.005 = 12500
    max_gain (full move) = (50−30)*75*22 = 33000 ≥ 12500  → G7 passes

  EXIT_TARGET (idx 18): spread 70−25=45  → MTM = (45−30)*75*22 = 24750 ≥ 12500
  EXIT_STOP   (idx 17): spread  1−1 =0   → MTM = ( 0−30)*75*22 = −49500 ≤ −49500
"""
import uuid
from datetime import date, datetime
from unittest.mock import patch

import pytest

from app.services.paper_engine import run_paper_engine

# ── Instrument / API tokens ────────────────────────────────────────────────────
TOKEN_SPOT       = 256265   # NSE:NIFTY 50 spot

# Apr 7 / Apr 8 → expiry Apr 9 (nearest Thursday)
TOKEN_22100CE_9  = 1002
TOKEN_22150CE_9  = 1003
TOKEN_21900PE_9  = 2003
TOKEN_21850PE_9  = 2002

# Apr 10 → expiry Apr 16 (Apr 9 is the trade date → pushed to next Thursday)
TOKEN_22100CE_16 = 3002
TOKEN_22150CE_16 = 3003

# ── Candle helpers ─────────────────────────────────────────────────────────────

def _c(year, month, day, hour, minute, close, high=None, low=None):
    """Create one OHLCV candle dict using a tz-naive datetime."""
    return {
        "date":   datetime(year, month, day, hour, minute),
        "open":   float(close),
        "high":   float(high if high is not None else close),
        "low":    float(low  if low  is not None else close),
        "close":  float(close),
        "volume": 100,
    }


def _or_window(year, month, day):
    """
    Build the 15 OR-window candles (09:15 – 09:29).
    Candle 0 sets OR_HIGH=22100 / OR_LOW=21900; the rest sit quietly at 22000.
    """
    candles = [_c(year, month, day, 9, 15, 22000, high=22100.0, low=21900.0)]
    for i in range(1, 15):
        candles.append(_c(year, month, day, 9, 15 + i, 22000))
    return candles


def _post_or(year, month, day, closes):
    """Build post-OR spot candles starting at 09:30."""
    out = []
    for i, close in enumerate(closes):
        total_min = 9 * 60 + 30 + i
        h, m = divmod(total_min, 60)
        out.append(_c(year, month, day, h, m, close))
    return out


def _option_series(year, month, day, closes):
    """
    Build an option candle list whose indices align with the spot candle list
    (first candle = 09:15).  Provide exactly len(closes) candles.
    """
    out = []
    for i, close in enumerate(closes):
        total_min = 9 * 60 + 15 + i
        h, m = divmod(total_min, 60)
        out.append(_c(year, month, day, h, m, close))
    return out


# ── Instruments-master builders ────────────────────────────────────────────────

def _inst(name, inst_type, strike, expiry, token, lot_size=75):
    return {
        "name":             name,
        "instrument_type":  inst_type,
        "strike":           strike,
        "expiry":           expiry,
        "instrument_token": token,
        "lot_size":         lot_size,
    }


# ── Scenario 1: Apr 7 — Bullish EXIT_TARGET ───────────────────────────────────

class TestApr7BullishExitTarget:
    """
    Two-step bullish breakout:
      idx 15 (09:30): close=22130 → G4 tentative (prev=22000 inside OR)
      idx 16 (09:31): close=22130 → G4 confirmed (prev=22130 > 22122.1) → ENTER
      idx 17 (09:32): spread 65-28=37, MTM=11550 → HOLD
      idx 18 (09:33): spread 70-25=45, MTM=24750 ≥ 12500 → EXIT_TARGET
      idx 19 (09:34): SESSION_COMPLETE  (no re-entry)
    """

    TRADE_DATE = date(2026, 4, 7)
    EXPIRY     = date(2026, 4, 9)

    INSTRUMENTS = [
        _inst("NIFTY", "CE", 22100, date(2026, 4, 9), TOKEN_22100CE_9),
        _inst("NIFTY", "CE", 22150, date(2026, 4, 9), TOKEN_22150CE_9),
    ]

    # Spot: OR (15 candles) + 5 post-OR
    SPOT = _or_window(2026, 4, 7) + _post_or(2026, 4, 7, [
        22130.0,   # idx 15 — tentative breakout
        22130.0,   # idx 16 — confirmed → ENTER
        22140.0,   # idx 17 — HOLD
        22500.0,   # idx 18 — EXIT_TARGET
        22510.0,   # idx 19 — SESSION_COMPLETE
    ])

    # 22100CE: 20 candles (indices 0–19).  Entry at idx 16 = 60.
    CE_LONG  = _option_series(2026, 4, 7, [50]*15 + [55, 60, 65, 70, 72])
    # 22150CE: 20 candles.  Entry at idx 16 = 30.
    CE_SHORT = _option_series(2026, 4, 7, [25]*15 + [27, 30, 28, 25, 24])

    def _fetch(self, token, trade_date, access_token):
        return {
            TOKEN_SPOT:      self.SPOT,
            TOKEN_22100CE_9: self.CE_LONG,
            TOKEN_22150CE_9: self.CE_SHORT,
        }.get(token, [])

    def _run(self):
        with patch("app.services.paper_engine.fetch_candles_with_token",
                   side_effect=self._fetch), \
             patch("app.services.paper_engine.get_instruments_with_token",
                   return_value=self.INSTRUMENTS):
            return run_paper_engine(
                uuid.uuid4(), self.TRADE_DATE, "NIFTY", 2_500_000, "tok"
            )

    def test_trade_opened_and_exit_target(self):
        result = self._run()
        th = result["trade_header"]
        assert th is not None, "Expected a trade to be opened"
        assert th["exit_reason"]   == "EXIT_TARGET"
        assert th["bias"]          == "BULLISH"
        assert th["long_strike"]   == 22100
        assert th["short_strike"]  == 22150
        assert th["option_type"]   == "CE"
        assert th["realized_gross_pnl"] == pytest.approx(24750.0)
        assert th["selection_method"] == "ranked_candidate_selection_v1"
        assert th["selected_candidate_rank"] == 1
        assert th["selected_candidate_score"] is not None

    def test_final_state_is_session_complete(self):
        assert self._run()["final_session_state"] == "SESSION_COMPLETE"

    def test_no_reentry_after_exit(self):
        """All decisions after EXIT_TARGET must be SESSION_COMPLETE / NO_TRADE."""
        result = self._run()
        # Find first exit decision index in the list
        exit_idx = next(
            i for i, d in enumerate(result["decisions"])
            if d.get("action") == "EXIT_TARGET"
        )
        post_exit = result["decisions"][exit_idx + 1:]
        assert len(post_exit) > 0, "Expected at least one post-exit decision"
        for d in post_exit:
            assert d["session_state"] == "SESSION_COMPLETE"
            assert d["action"]        == "NO_TRADE"
            assert d["reason_code"]   == "SESSION_COMPLETE"

    def test_strategy_context_frozen_at_entry(self):
        """trade_header must record the strategy params frozen at entry time."""
        th = self._run()["trade_header"]
        assert th["strategy_name"]    == "ORB_DEBIT_SPREAD_V1"
        assert th["strategy_version"] is not None
        assert th["risk_cap"]         == pytest.approx(2_500_000 * 0.02)

    def test_two_step_signal_substates(self):
        """idx 15 → TENTATIVE_BREAKOUT; idx 16 → CONFIRMED_BREAKOUT (ENTER)."""
        decisions = self._run()["decisions"]
        # Idx 15 is the 16th decision (0-based, 15 OR + 1st post-OR)
        d15 = decisions[15]
        assert d15["signal_substate"] == "TENTATIVE_BREAKOUT"
        assert d15["session_state"]   == "TENTATIVE_SIGNAL"

        d16 = decisions[16]
        assert d16["signal_substate"] == "CONFIRMED_BREAKOUT"
        assert d16["action"]          == "ENTER"
        assert d16["reason_code"]     == "ENTER_TRADE"
        assert d16["candidate_ranking_json"] is not None
        assert d16["selected_candidate_rank"] == 1
        assert d16["selected_candidate_score"] is not None


# ── Scenario 2: Apr 8 — Bearish EXIT_STOP ─────────────────────────────────────

class TestApr8BearishExitStop:
    """
    Two-step bearish breakout:
      idx 15 (09:30): close=21875 → G4 tentative (prev=22000 outside bear threshold)
      idx 16 (09:31): close=21870 → G4 confirmed (prev=21875 < 21879.1) → ENTER
      idx 17 (09:32): spread 1-1=0, MTM=(0-30)*75*22=-49500 ≤ -49500 → EXIT_STOP
      idx 18 (09:33): SESSION_COMPLETE
    """

    TRADE_DATE = date(2026, 4, 8)
    EXPIRY     = date(2026, 4, 9)

    INSTRUMENTS = [
        _inst("NIFTY", "PE", 21900, date(2026, 4, 9), TOKEN_21900PE_9),
        _inst("NIFTY", "PE", 21850, date(2026, 4, 9), TOKEN_21850PE_9),
    ]

    SPOT = _or_window(2026, 4, 8) + _post_or(2026, 4, 8, [
        21875.0,   # idx 15 — tentative bearish breakout
        21870.0,   # idx 16 — confirmed → ENTER
        21500.0,   # idx 17 — EXIT_STOP
        21490.0,   # idx 18 — SESSION_COMPLETE
    ])

    # 21900PE: 19 candles.  Entry price at idx 16 = 60.
    PE_LONG  = _option_series(2026, 4, 8, [40]*15 + [55, 60,  1,  1])
    # 21850PE: 19 candles.  Entry price at idx 16 = 30.
    PE_SHORT = _option_series(2026, 4, 8, [20]*15 + [27, 30,  1,  1])

    def _fetch(self, token, trade_date, access_token):
        return {
            TOKEN_SPOT:      self.SPOT,
            TOKEN_21900PE_9: self.PE_LONG,
            TOKEN_21850PE_9: self.PE_SHORT,
        }.get(token, [])

    def _run(self):
        with patch("app.services.paper_engine.fetch_candles_with_token",
                   side_effect=self._fetch), \
             patch("app.services.paper_engine.get_instruments_with_token",
                   return_value=self.INSTRUMENTS):
            return run_paper_engine(
                uuid.uuid4(), self.TRADE_DATE, "NIFTY", 2_500_000, "tok"
            )

    def test_trade_opened_and_exit_stop(self):
        result = self._run()
        th = result["trade_header"]
        assert th is not None, "Expected a trade to be opened"
        assert th["exit_reason"]  == "EXIT_STOP"
        assert th["bias"]         == "BEARISH"
        assert th["long_strike"]  == 21900
        assert th["short_strike"] == 21850
        assert th["option_type"]  == "PE"
        assert th["realized_gross_pnl"] < 0
        assert th["selected_candidate_rank"] == 1
        assert th["selected_candidate_score"] is not None

    def test_final_state_is_session_complete(self):
        assert self._run()["final_session_state"] == "SESSION_COMPLETE"

    def test_bearish_signal_substates(self):
        decisions = self._run()["decisions"]
        d15 = decisions[15]
        assert d15["signal_substate"] == "TENTATIVE_BREAKOUT"

        d16 = decisions[16]
        assert d16["signal_substate"] == "CONFIRMED_BREAKOUT"
        assert d16["action"]          == "ENTER"
        assert d16["reason_code"]     == "ENTER_TRADE"


# ── Scenario 3: Apr 9 — No breakout, no trade ─────────────────────────────────

class TestApr9NoBreakout:
    """
    All post-OR candles close inside the OR (22050 is between 21879.1 and 22122.1).
    G3 fires for every post-OR minute → no entry, ever.
    final_session_state must be OBSERVING.
    """

    TRADE_DATE = date(2026, 4, 9)
    EXPIRY     = date(2026, 4, 9)   # Apr 9 itself is the expiry (still valid: >= trade_date)

    INSTRUMENTS = [
        _inst("NIFTY", "CE", 22100, date(2026, 4, 9), TOKEN_22100CE_9),
        _inst("NIFTY", "CE", 22150, date(2026, 4, 9), TOKEN_22150CE_9),
        _inst("NIFTY", "PE", 21900, date(2026, 4, 9), TOKEN_21900PE_9),
        _inst("NIFTY", "PE", 21850, date(2026, 4, 9), TOKEN_21850PE_9),
    ]

    # 20 post-OR candles all inside the range
    SPOT = _or_window(2026, 4, 9) + _post_or(2026, 4, 9, [22050.0] * 20)

    # Option candles: minimal (content irrelevant — G3 rejects before G5)
    _OPT_STUB = _option_series(2026, 4, 9, [30.0] * 35)

    def _fetch(self, token, trade_date, access_token):
        if token == TOKEN_SPOT:
            return self.SPOT
        return self._OPT_STUB

    def _run(self):
        with patch("app.services.paper_engine.fetch_candles_with_token",
                   side_effect=self._fetch), \
             patch("app.services.paper_engine.get_instruments_with_token",
                   return_value=self.INSTRUMENTS):
            return run_paper_engine(
                uuid.uuid4(), self.TRADE_DATE, "NIFTY", 2_500_000, "tok"
            )

    def test_no_trade_opened(self):
        result = self._run()
        assert result["trade_header"] is None
        assert result["minute_marks"] == []

    def test_final_state_is_observing(self):
        assert self._run()["final_session_state"] == "OBSERVING"

    def test_all_post_or_decisions_are_no_breakout(self):
        decisions = self._run()["decisions"]
        post_or = [d for d in decisions if d.get("signal_state") == "EVALUATE"]
        assert len(post_or) == 20
        for d in post_or:
            assert d["reason_code"] == "NO_BREAKOUT_CONFIRMATION"
            assert d["action"]      == "NO_TRADE"
            assert d["session_state"] == "OBSERVING"


# ── Scenario 4: Apr 10 — STALE_OPTION_PRICE rejection ─────────────────────────

class TestApr10StaleOptionPrice:
    """
    Breakout confirmed at idx 16 (09:31) — all gates pass — but option
    price index only covers indices 0–14 (15 candles).
    At idx 16 the staleness is 16−14=2 minutes → engine must block entry
    with reason_code=STALE_OPTION_PRICE / rejection_gate=FRESHNESS.

    The signal_substate must remain CONFIRMED_BREAKOUT (the signal was real;
    only the price data quality prevented entry).
    """

    TRADE_DATE  = date(2026, 4, 10)
    EXPIRY      = date(2026, 4, 16)   # Apr 10 is Friday; next Thursday = Apr 16

    INSTRUMENTS = [
        _inst("NIFTY", "CE", 22100, date(2026, 4, 16), TOKEN_22100CE_16),
        _inst("NIFTY", "CE", 22150, date(2026, 4, 16), TOKEN_22150CE_16),
    ]

    SPOT = _or_window(2026, 4, 10) + _post_or(2026, 4, 10, [
        22130.0,   # idx 15 — tentative breakout
        22130.0,   # idx 16 — confirmed, but option prices stale → STALE_OPTION_PRICE
        22050.0,   # idx 17 — back inside range, NO_BREAKOUT_CONFIRMATION
    ])

    # Only 15 candles for each CE leg — indices 0–14 (last fresh price = idx 14).
    # At idx 16 (spot candle index 16), backfill → staleness = 16 − 14 = 2.
    CE_LONG_STALE  = _option_series(2026, 4, 10, [50]*14 + [60])   # 15 candles
    CE_SHORT_STALE = _option_series(2026, 4, 10, [25]*14 + [30])   # 15 candles

    def _fetch(self, token, trade_date, access_token):
        if token == TOKEN_SPOT:
            return self.SPOT
        if token == TOKEN_22100CE_16:
            return self.CE_LONG_STALE
        if token == TOKEN_22150CE_16:
            return self.CE_SHORT_STALE
        return []

    def _run(self):
        with patch("app.services.paper_engine.fetch_candles_with_token",
                   side_effect=self._fetch), \
             patch("app.services.paper_engine.get_instruments_with_token",
                   return_value=self.INSTRUMENTS):
            return run_paper_engine(
                uuid.uuid4(), self.TRADE_DATE, "NIFTY", 2_500_000, "tok"
            )

    def test_no_trade_opened_due_to_staleness(self):
        result = self._run()
        assert result["trade_header"] is None
        assert result["minute_marks"] == []

    def test_stale_decision_has_correct_codes(self):
        result = self._run()
        stale_decisions = [
            d for d in result["decisions"]
            if d.get("reason_code") == "STALE_OPTION_PRICE"
        ]
        assert len(stale_decisions) == 1, "Expected exactly one STALE_OPTION_PRICE decision"
        d = stale_decisions[0]
        assert d["action"]         == "NO_TRADE"
        assert d["rejection_gate"] == "FRESHNESS"
        assert d["candidate_ranking_json"] is not None
        assert d["selected_candidate_rank"] is None

    def test_signal_substate_is_confirmed_despite_staleness(self):
        """
        The breakout signal was genuine — signal_substate must remain CONFIRMED_BREAKOUT
        even though the stale price blocked entry.
        """
        stale_decisions = [
            d for d in self._run()["decisions"]
            if d.get("reason_code") == "STALE_OPTION_PRICE"
        ]
        assert stale_decisions[0]["signal_substate"] == "CONFIRMED_BREAKOUT"

    def test_price_freshness_json_records_staleness(self):
        """price_freshness_json must record the exact staleness in minutes."""
        stale_decisions = [
            d for d in self._run()["decisions"]
            if d.get("reason_code") == "STALE_OPTION_PRICE"
        ]
        pf = stale_decisions[0]["price_freshness_json"]
        assert pf is not None
        assert pf.get("22100_CE_age_min") == 2
        assert pf.get("22150_CE_age_min") == 2

    def test_final_state_is_observing_not_open_trade(self):
        """Stale rejection must leave session state as OBSERVING (not OPEN_TRADE)."""
        assert self._run()["final_session_state"] == "OBSERVING"

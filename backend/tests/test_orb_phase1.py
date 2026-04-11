"""
Phase 1 ORB fix — correctness tests.

Scenarios covered:
  1. Single trade per session: no re-entry after exit
  2. Two-step breakout confirmation: tentative → confirmed
  3. Two-step breakout confirmation: tentative → failed
  4. G4 pre_entry_snapshot has failing_gate="G4"
  5. ExitEval carries estimated_exit_charges correctly
  6. Session lifecycle state transitions (OBSERVING → TENTATIVE_SIGNAL → OPEN_TRADE → TRADE_CLOSED → SESSION_COMPLETE)
  7. SESSION_COMPLETE decisions produced for every minute after trade close
  8. trade_header includes 'charges' field
  9. minute_marks carry gross_mtm / estimated_exit_charges / estimated_net_mtm
"""
import uuid
from datetime import date, time as time_type
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from app.services.entry_gates import evaluate_gates, GateResult
from app.services.exit_engine import evaluate_exit, ExitEval
from app.services.opening_range import generate_bullish_candidates, generate_bearish_candidates


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_candle(close: float, idx: int = 0) -> Dict:
    """Build a minimal Zerodha-style candle dict."""
    from datetime import datetime, timedelta
    base = datetime(2026, 4, 7, 9, 15)
    ts = base + timedelta(minutes=idx)
    return {
        "date": ts,
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1000,
    }


def _make_option_prices(
    bullish_candidates: List[Tuple[int, int]],
    bearish_candidates: List[Tuple[int, int]],
    default_price: float = 50.0,
) -> Dict:
    """Build a flat option_prices dict with uniform prices."""
    prices = {}
    for long_s, short_s in bullish_candidates:
        prices[(long_s, "CE")] = default_price
        prices[(short_s, "CE")] = default_price * 0.6
    for long_s, short_s in bearish_candidates:
        prices[(long_s, "PE")] = default_price
        prices[(short_s, "PE")] = default_price * 0.6
    return prices


# ── Fixtures ───────────────────────────────────────────────────────────────────

OR_HIGH = 22150.0
OR_LOW  = 21950.0

# Bullish breakout: close > OR_HIGH * 1.001
BULLISH_CLOSE = OR_HIGH * 1.002   # ~22194.15

# Bearish breakout: close < OR_LOW * 0.999
BEARISH_CLOSE = OR_LOW * 0.998    # ~21906.10

# Inside range: no breakout
INSIDE_CLOSE = (OR_HIGH + OR_LOW) / 2   # ~22050.0


def _base_gate_kwargs(**overrides) -> Dict:
    instrument = overrides.pop("instrument", "NIFTY")
    capital    = overrides.pop("capital", 2_500_000.0)
    candidates = generate_bullish_candidates(OR_HIGH)
    prices     = _make_option_prices(
        generate_bullish_candidates(OR_HIGH),
        generate_bearish_candidates(OR_LOW),
    )
    return {
        "candle":           _make_candle(BULLISH_CLOSE, idx=15),
        "or_high":          OR_HIGH,
        "or_low":           OR_LOW,
        "or_ready":         True,
        "has_open_trade":   False,
        "option_prices":    prices,
        "instrument":       instrument,
        "capital":          capital,
        "expiry":           date(2026, 4, 10),
        "prev_candle_close": None,
        "lot_size":         75,
        **overrides,
    }


# ── Tests: entry_gates ──────────────────────────────────────────────────────────

class TestG4FollowThrough:
    def test_first_breakout_fails_g4_when_prev_inside(self):
        """Minute N: breakout + prev was inside range → G4 fails → TENTATIVE_BREAKOUT."""
        kwargs = _base_gate_kwargs(prev_candle_close=INSIDE_CLOSE)
        result = evaluate_gates(**kwargs)
        assert result.action == "NO_TRADE"
        assert result.reason_code == "FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH"

    def test_g4_failure_populates_pre_entry_snapshot(self):
        """G4 failure pre_entry_snapshot should carry failing_gate='G4'."""
        kwargs = _base_gate_kwargs(prev_candle_close=INSIDE_CLOSE)
        result = evaluate_gates(**kwargs)
        assert result.pre_entry_snapshot is not None
        assert result.pre_entry_snapshot["failing_gate"] == "G4"
        assert result.pre_entry_snapshot["bias"] == "BULLISH"

    def test_second_breakout_candle_passes_g4(self):
        """Minute N+1: breakout + prev was also a breakout → G4 passes → ENTER."""
        kwargs = _base_gate_kwargs(prev_candle_close=BULLISH_CLOSE)
        result = evaluate_gates(**kwargs)
        assert result.action == "ENTER"
        assert result.reason_code == "ENTER_TRADE"

    def test_skip_g4_when_no_prev_candle(self):
        """prev_candle_close=None → G4 check skipped → can ENTER on first minute."""
        kwargs = _base_gate_kwargs(prev_candle_close=None)
        result = evaluate_gates(**kwargs)
        # Should reach G5-G7; with our prices it should ENTER
        assert result.action == "ENTER"

    def test_bearish_g4_failure(self):
        """Bearish G4: current candle is bearish breakout but prev was inside."""
        candidates = generate_bearish_candidates(OR_LOW)
        prices = _make_option_prices(
            generate_bullish_candidates(OR_HIGH),
            candidates,
        )
        result = evaluate_gates(
            candle=_make_candle(BEARISH_CLOSE, idx=15),
            or_high=OR_HIGH,
            or_low=OR_LOW,
            or_ready=True,
            has_open_trade=False,
            option_prices=prices,
            instrument="NIFTY",
            capital=2_500_000.0,
            expiry=date(2026, 4, 10),
            prev_candle_close=INSIDE_CLOSE,
            lot_size=75,
        )
        assert result.action == "NO_TRADE"
        assert result.reason_code == "FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH"
        assert result.pre_entry_snapshot["bias"] == "BEARISH"
        assert result.pre_entry_snapshot["failing_gate"] == "G4"


# ── Tests: exit_engine ──────────────────────────────────────────────────────────

class TestExitEngineCharges:
    def test_hold_carries_estimated_charges(self):
        ev = evaluate_exit(
            current_time=time_type(10, 30),
            long_price=55.0,
            short_price=30.0,
            entry_debit=20.0,
            lot_size=75,
            approved_lots=2,
            total_max_loss=3000.0,
            target_profit=6250.0,
            estimated_charges=250.0,
        )
        assert ev.action == "HOLD"
        assert ev.gross_mtm == ev.total_mtm
        assert ev.estimated_exit_charges == 250.0
        assert ev.estimated_net_mtm == round(ev.total_mtm - 250.0, 2)

    def test_target_exit_carries_estimated_charges(self):
        # spread=85-20=65, mtm_per_lot=(65-20)*75=3375, total_mtm=6750 >= target 6250
        ev = evaluate_exit(
            current_time=time_type(11, 0),
            long_price=85.0,
            short_price=20.0,
            entry_debit=20.0,
            lot_size=75,
            approved_lots=2,
            total_max_loss=3000.0,
            target_profit=6250.0,
            estimated_charges=300.0,
        )
        assert ev.action == "EXIT_TARGET"
        assert ev.gross_mtm > 0
        assert ev.estimated_exit_charges == 300.0
        assert ev.estimated_net_mtm == round(ev.gross_mtm - 300.0, 2)

    def test_stop_exit_carries_estimated_charges(self):
        ev = evaluate_exit(
            current_time=time_type(11, 0),
            long_price=5.0,
            short_price=5.0,
            entry_debit=20.0,
            lot_size=75,
            approved_lots=2,
            total_max_loss=3000.0,
            target_profit=6250.0,
            estimated_charges=150.0,
        )
        assert ev.action == "EXIT_STOP"
        assert ev.estimated_exit_charges == 150.0

    def test_zero_charges_when_not_provided(self):
        ev = evaluate_exit(
            current_time=time_type(10, 0),
            long_price=55.0,
            short_price=30.0,
            entry_debit=20.0,
            lot_size=75,
            approved_lots=1,
            total_max_loss=1500.0,
            target_profit=3125.0,
        )
        assert ev.estimated_exit_charges == 0.0
        assert ev.estimated_net_mtm == round(ev.total_mtm, 2)


# ── Tests: paper_engine session-state machine ───────────────────────────────────

def _build_engine_result_synthetic(
    candles_close: List[float],
    entry_at_idx: Optional[int],
    exit_action_at: Optional[int] = None,
):
    """
    Build a minimal paper_engine result dict by calling the engine's internal
    state-machine logic, simulated via evaluate_gates + evaluate_exit directly.

    This avoids Zerodha API calls by driving the gate logic manually.
    Returns (decisions, trade_header, minute_marks, final_session_state).
    """
    from datetime import datetime, timedelta
    import uuid as _uuid

    or_high = 22150.0
    or_low  = 21950.0
    capital = 2_500_000.0
    expiry  = date(2026, 4, 10)
    lot_size = 75

    prices = _make_option_prices(
        generate_bullish_candidates(or_high),
        generate_bearish_candidates(or_low),
    )

    decisions = []
    minute_marks = []
    trade_header = None
    trade_legs = []
    active_trade = None
    trade_closed_this_session = False
    prev_was_tentative_breakout = False
    current_session_state = "OBSERVING"

    base_time = datetime(2026, 4, 7, 9, 15)

    for idx, close in enumerate(candles_close):
        ts = base_time + timedelta(minutes=idx)
        candle = {"date": ts, "open": close - 1, "high": close + 1, "low": close - 2, "close": close}
        or_ready = idx >= 15
        prev_close = candles_close[idx - 1] if idx > 0 else None
        current_time = ts.time()

        if trade_closed_this_session:
            current_session_state = "SESSION_COMPLETE"
            decisions.append({
                "action": "NO_TRADE",
                "reason_code": "SESSION_COMPLETE",
                "session_state": current_session_state,
                "signal_substate": None,
                "timestamp": ts,
            })
            continue

        if active_trade is None:
            gate = evaluate_gates(
                candle=candle,
                or_high=or_high,
                or_low=or_low,
                or_ready=or_ready,
                has_open_trade=False,
                option_prices=prices,
                instrument="NIFTY",
                capital=capital,
                expiry=expiry,
                prev_candle_close=prev_close,
                lot_size=lot_size,
            )

            signal_substate = None
            if gate.reason_code == "FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH":
                signal_substate = "TENTATIVE_BREAKOUT"
            elif prev_was_tentative_breakout:
                if gate.reason_code == "NO_BREAKOUT_CONFIRMATION":
                    signal_substate = "FAILED_FIRST_BREAKOUT"
                else:
                    signal_substate = "CONFIRMED_BREAKOUT"

            if not or_ready:
                current_session_state = "OBSERVING"
            elif gate.action == "ENTER":
                current_session_state = "OPEN_TRADE"
            elif signal_substate == "TENTATIVE_BREAKOUT":
                current_session_state = "TENTATIVE_SIGNAL"
            elif signal_substate in ("FAILED_FIRST_BREAKOUT", None):
                current_session_state = "OBSERVING"
            elif signal_substate == "CONFIRMED_BREAKOUT" and gate.action != "ENTER":
                current_session_state = "OBSERVING"

            prev_was_tentative_breakout = (
                gate.reason_code == "FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH"
            )

            decisions.append({
                "action": gate.action,
                "reason_code": gate.reason_code,
                "session_state": current_session_state,
                "signal_substate": signal_substate,
                "timestamp": ts,
            })

            if gate.action == "ENTER":
                trade_id = _uuid.uuid4()
                long_p = prices.get((gate.long_strike, gate.opt_type), 50.0)
                short_p = prices.get((gate.short_strike, gate.opt_type), 30.0)
                active_trade = {
                    "id": trade_id,
                    "entry_time": ts,
                    "bias": gate.bias,
                    "long_strike": gate.long_strike,
                    "short_strike": gate.short_strike,
                    "opt_type": gate.opt_type,
                    "entry_debit": gate.entry_debit,
                    "approved_lots": gate.approved_lots,
                    "lot_size": lot_size,
                    "total_max_loss": gate.computed_max_loss,
                    "target_profit": gate.computed_target,
                    "expiry": expiry,
                    "_entry_long_price": float(long_p),
                    "_entry_short_price": float(short_p),
                }

        else:
            current_session_state = "OPEN_TRADE"
            long_p  = float(prices.get((active_trade["long_strike"],  active_trade["opt_type"]), 50.0))
            short_p = float(prices.get((active_trade["short_strike"], active_trade["opt_type"]), 30.0))

            # Force exit if at the designated exit index
            force_exit = (exit_action_at is not None and idx == exit_action_at)
            if force_exit:
                # Simulate target hit by using very high long price
                long_p = 120.0

            ev = evaluate_exit(
                current_time=current_time,
                long_price=long_p,
                short_price=short_p,
                entry_debit=active_trade["entry_debit"],
                lot_size=active_trade["lot_size"],
                approved_lots=active_trade["approved_lots"],
                total_max_loss=active_trade["total_max_loss"],
                target_profit=active_trade["target_profit"],
                estimated_charges=200.0,
            )

            if ev.action != "HOLD":
                current_session_state = "TRADE_CLOSED"

            decisions.append({
                "action": ev.action,
                "reason_code": ev.action,
                "session_state": current_session_state,
                "signal_substate": None,
                "timestamp": ts,
            })
            minute_marks.append({
                "action": ev.action,
                "total_mtm": round(ev.total_mtm, 2),
                "gross_mtm": round(ev.gross_mtm, 2),
                "estimated_exit_charges": round(ev.estimated_exit_charges, 2),
                "estimated_net_mtm": round(ev.estimated_net_mtm, 2),
            })

            if ev.action != "HOLD":
                trade_header = {
                    "id": active_trade["id"],
                    "entry_time": active_trade["entry_time"],
                    "exit_time": ts,
                    "exit_reason": ev.action,
                    "realized_gross_pnl": round(ev.total_mtm, 2),
                    "charges": 200.0,
                    "realized_net_pnl": round(ev.total_mtm - 200.0, 2),
                }
                active_trade = None
                trade_closed_this_session = True

    return decisions, trade_header, minute_marks, current_session_state


class TestSessionStateMachine:
    def _build_candles(self, or_minutes=15, total=40,
                       breakout_at: Optional[int] = None,
                       second_breakout_at: Optional[int] = None):
        """Build a close-price sequence.
        Candles 0..14 = inside range (OR formation).
        Candle *breakout_at* = bullish breakout.
        Candle *second_breakout_at* = second consecutive breakout (confirms entry).
        All others = inside range.
        """
        closes = [INSIDE_CLOSE] * total
        if breakout_at is not None:
            closes[breakout_at] = BULLISH_CLOSE
        if second_breakout_at is not None:
            closes[second_breakout_at] = BULLISH_CLOSE
        return closes

    def test_no_trade_session_stays_observing(self):
        closes = self._build_candles(total=30)   # all inside range
        decisions, trade_header, marks, final_state = _build_engine_result_synthetic(
            closes, entry_at_idx=None
        )
        assert trade_header is None
        assert final_state == "OBSERVING"
        or_window_decisions = [d for d in decisions if d["session_state"] == "OBSERVING"]
        assert len(or_window_decisions) == 30

    def test_tentative_breakout_sets_tentative_signal_state(self):
        """Breakout at idx=15 but prev is inside → TENTATIVE_SIGNAL."""
        closes = self._build_candles(total=30, breakout_at=15)
        decisions, _, _, _ = _build_engine_result_synthetic(closes, entry_at_idx=None)
        d15 = decisions[15]
        assert d15["signal_substate"] == "TENTATIVE_BREAKOUT"
        assert d15["session_state"] == "TENTATIVE_SIGNAL"

    def test_failed_first_breakout(self):
        """Breakout at idx=15, no breakout at idx=16 → FAILED_FIRST_BREAKOUT."""
        closes = self._build_candles(total=30, breakout_at=15)
        # idx=16 is INSIDE_CLOSE (default)
        decisions, _, _, _ = _build_engine_result_synthetic(closes, entry_at_idx=None)
        d16 = decisions[16]
        assert d16["signal_substate"] == "FAILED_FIRST_BREAKOUT"
        assert d16["session_state"] == "OBSERVING"

    def test_confirmed_breakout_leads_to_entry(self):
        """Two consecutive breakout candles → ENTER at idx=16.
        trade_header is only created at exit; we verify the ENTER decision itself.
        """
        closes = self._build_candles(total=40, breakout_at=15, second_breakout_at=16)
        decisions, trade_header, _, _ = _build_engine_result_synthetic(
            closes, entry_at_idx=None
        )
        d15 = decisions[15]
        d16 = decisions[16]
        assert d15["signal_substate"] == "TENTATIVE_BREAKOUT"
        assert d16["signal_substate"] == "CONFIRMED_BREAKOUT"
        assert d16["action"] == "ENTER"
        assert d16["session_state"] == "OPEN_TRADE"
        # trade_header is None until exit occurs (no forced exit in this test)
        # Subsequent decisions should be OPEN_TRADE / HOLD, not SESSION_COMPLETE
        open_trade_decisions = [d for d in decisions[17:] if d["session_state"] == "OPEN_TRADE"]
        assert len(open_trade_decisions) > 0

    def test_single_trade_per_session_no_reentry(self):
        """After trade exits, all subsequent decisions are SESSION_COMPLETE."""
        closes = self._build_candles(total=50, breakout_at=15, second_breakout_at=16)
        # Force exit at idx=20 by manipulating the test helper
        decisions, trade_header, _, final_state = _build_engine_result_synthetic(
            closes, entry_at_idx=16, exit_action_at=20
        )
        assert trade_header is not None

        session_complete_decisions = [d for d in decisions if d["session_state"] == "SESSION_COMPLETE"]
        assert len(session_complete_decisions) == len(closes) - 21   # mins 21..49

        # After SESSION_COMPLETE, no ENTER should appear
        session_complete_actions = [
            d["action"] for d in decisions if d["session_state"] == "SESSION_COMPLETE"
        ]
        assert all(a == "NO_TRADE" for a in session_complete_actions)

        assert final_state == "SESSION_COMPLETE"

    def test_trade_closed_state_on_exit_minute(self):
        """The exit minute itself should have session_state=TRADE_CLOSED."""
        closes = self._build_candles(total=30, breakout_at=15, second_breakout_at=16)
        decisions, _, _, _ = _build_engine_result_synthetic(
            closes, entry_at_idx=16, exit_action_at=20
        )
        exit_decisions = [d for d in decisions if d["action"] in ("EXIT_TARGET", "EXIT_STOP", "EXIT_TIME")]
        for d in exit_decisions:
            assert d["session_state"] == "TRADE_CLOSED"

    def test_minute_marks_carry_gross_net_split(self):
        """All minute marks must have gross_mtm, estimated_exit_charges, estimated_net_mtm."""
        closes = self._build_candles(total=25, breakout_at=15, second_breakout_at=16)
        _, _, marks, _ = _build_engine_result_synthetic(closes, entry_at_idx=16)
        assert len(marks) > 0
        for mark in marks:
            assert "gross_mtm" in mark
            assert "estimated_exit_charges" in mark
            assert "estimated_net_mtm" in mark
            assert mark["estimated_net_mtm"] == round(mark["gross_mtm"] - mark["estimated_exit_charges"], 2)

    def test_trade_header_has_charges(self):
        """trade_header must include a 'charges' field."""
        closes = self._build_candles(total=30, breakout_at=15, second_breakout_at=16)
        _, trade_header, _, _ = _build_engine_result_synthetic(
            closes, entry_at_idx=16, exit_action_at=22
        )
        if trade_header:
            assert "charges" in trade_header
            assert trade_header["charges"] >= 0


# ── Tests: strategy_config ──────────────────────────────────────────────────────

# ── Tests: TOO_LATE_TO_ENTER ───────────────────────────────────────────────────

class TestTooLateToEnter:
    def _prices(self):
        return _make_option_prices(
            generate_bullish_candidates(OR_HIGH),
            generate_bearish_candidates(OR_LOW),
        )

    def test_rejects_entry_when_too_close_to_squareoff(self):
        """With 5 minutes left and min=20, TOO_LATE_TO_ENTER fires."""
        from app.services.strategy_config import STRATEGY_CONFIG
        sq = STRATEGY_CONFIG["square_off_time"]
        # Build a current_time 10 min before square-off (< min_minutes_left_to_enter=20)
        import datetime
        sq_dt = datetime.datetime.combine(datetime.date.today(), sq)
        close_time = (sq_dt - datetime.timedelta(minutes=10)).time()
        result = evaluate_gates(
            candle=_make_candle(BULLISH_CLOSE, idx=15),
            or_high=OR_HIGH,
            or_low=OR_LOW,
            or_ready=True,
            has_open_trade=False,
            option_prices=self._prices(),
            instrument="NIFTY",
            capital=2_500_000.0,
            expiry=date(2026, 4, 10),
            prev_candle_close=BULLISH_CLOSE,  # prev also breakout so G4 passes
            lot_size=75,
            current_time=close_time,
        )
        assert result.action == "NO_TRADE"
        assert result.reason_code == "TOO_LATE_TO_ENTER"

    def test_allows_entry_with_enough_time_remaining(self):
        """With 30 minutes left and min=20, entry proceeds normally."""
        from app.services.strategy_config import STRATEGY_CONFIG
        import datetime
        sq = STRATEGY_CONFIG["square_off_time"]
        sq_dt = datetime.datetime.combine(datetime.date.today(), sq)
        close_time = (sq_dt - datetime.timedelta(minutes=30)).time()
        result = evaluate_gates(
            candle=_make_candle(BULLISH_CLOSE, idx=15),
            or_high=OR_HIGH,
            or_low=OR_LOW,
            or_ready=True,
            has_open_trade=False,
            option_prices=self._prices(),
            instrument="NIFTY",
            capital=2_500_000.0,
            expiry=date(2026, 4, 10),
            prev_candle_close=BULLISH_CLOSE,
            lot_size=75,
            current_time=close_time,
        )
        assert result.action == "ENTER"

    def test_no_current_time_skips_check(self):
        """current_time=None skips the TOO_LATE_TO_ENTER check entirely."""
        result = evaluate_gates(
            candle=_make_candle(BULLISH_CLOSE, idx=15),
            or_high=OR_HIGH,
            or_low=OR_LOW,
            or_ready=True,
            has_open_trade=False,
            option_prices=self._prices(),
            instrument="NIFTY",
            capital=2_500_000.0,
            expiry=date(2026, 4, 10),
            prev_candle_close=BULLISH_CLOSE,
            lot_size=75,
            current_time=None,  # no check
        )
        # Should reach ENTER (not blocked by time)
        assert result.action == "ENTER"


# ── Tests: config centralization ───────────────────────────────────────────────

class TestConfigCentralization:
    def test_opening_range_uses_config_values(self):
        from app.services.opening_range import (
            OR_WINDOW_MINUTES, FOLLOW_THROUGH_PCT, STRIKE_STEP, N_CANDIDATE_SPREADS
        )
        from app.services.strategy_config import STRATEGY_CONFIG
        assert OR_WINDOW_MINUTES   == STRATEGY_CONFIG["or_window_minutes"]
        assert FOLLOW_THROUGH_PCT  == STRATEGY_CONFIG["breakout_buffer_pct"]
        assert STRIKE_STEP         == STRATEGY_CONFIG["strike_step"]
        assert N_CANDIDATE_SPREADS == STRATEGY_CONFIG["n_candidate_spreads"]

    def test_exit_engine_uses_config_squareoff(self):
        from app.services.exit_engine import SQUARE_OFF_TIME
        from app.services.strategy_config import STRATEGY_CONFIG
        assert SQUARE_OFF_TIME == STRATEGY_CONFIG["square_off_time"]

    def test_entry_gates_uses_config_squareoff(self):
        from app.services.entry_gates import _SQUARE_OFF_TIME, _MIN_MINUTES_LEFT_TO_ENTER
        from app.services.strategy_config import STRATEGY_CONFIG
        assert _SQUARE_OFF_TIME           == STRATEGY_CONFIG["square_off_time"]
        assert _MIN_MINUTES_LEFT_TO_ENTER == STRATEGY_CONFIG["min_minutes_left_to_enter"]


# ── Tests: candidate rank + charges breakdown ──────────────────────────────────

class TestCandidateAudit:
    def test_enter_candidate_structure_has_rank_and_spread_width(self):
        prices = _make_option_prices(
            generate_bullish_candidates(OR_HIGH),
            generate_bearish_candidates(OR_LOW),
        )
        result = evaluate_gates(
            candle=_make_candle(BULLISH_CLOSE, idx=15),
            or_high=OR_HIGH,
            or_low=OR_LOW,
            or_ready=True,
            has_open_trade=False,
            option_prices=prices,
            instrument="NIFTY",
            capital=2_500_000.0,
            expiry=date(2026, 4, 10),
            prev_candle_close=BULLISH_CLOSE,
            lot_size=75,
        )
        assert result.action == "ENTER"
        cs = result.candidate_structure
        assert "candidate_rank" in cs
        assert cs["candidate_rank"] >= 1
        assert "spread_width" in cs
        assert cs["spread_width"] > 0
        assert result.candidate_ranking_json is not None
        assert result.selected_candidate_rank == 1
        assert result.selected_candidate_score is not None
        assert result.selected_candidate_score_breakdown is not None


class TestChargesBreakdown:
    def test_compute_charges_breakdown_has_all_components(self):
        from app.services.paper_engine import _compute_charges_breakdown
        bd = _compute_charges_breakdown(
            entry_long_price=50.0,
            entry_short_price=30.0,
            exit_long_price=80.0,
            exit_short_price=15.0,
            lot_size=75,
            approved_lots=2,
        )
        for key in ("brokerage", "stt", "exchange_charges", "gst", "total"):
            assert key in bd, f"Missing key: {key}"
            assert bd[key] >= 0

    def test_total_equals_sum_of_components(self):
        from app.services.paper_engine import _compute_charges_breakdown
        bd = _compute_charges_breakdown(
            entry_long_price=50.0,
            entry_short_price=30.0,
            exit_long_price=80.0,
            exit_short_price=15.0,
            lot_size=75,
            approved_lots=2,
        )
        # total is rounded from raw floats; allow 1-cent tolerance from
        # intermediate rounding of individual components
        component_sum = bd["brokerage"] + bd["stt"] + bd["exchange_charges"] + bd["gst"]
        assert abs(bd["total"] - component_sum) <= 0.02

    def test_compute_charges_wrapper_returns_total(self):
        from app.services.paper_engine import _compute_charges, _compute_charges_breakdown
        total = _compute_charges(50.0, 30.0, 80.0, 15.0, 75, 2)
        bd = _compute_charges_breakdown(50.0, 30.0, 80.0, 15.0, 75, 2)
        assert total == bd["total"]


# ── Tests: strategy_config ──────────────────────────────────────────────────────

class TestStrategyConfig:
    def test_all_required_keys_present(self):
        from app.services.strategy_config import STRATEGY_CONFIG
        required = [
            "strategy_name", "strategy_version",
            "or_window_minutes", "breakout_buffer_pct",
            "max_risk_pct", "target_profit_pct",
            "square_off_time", "strike_step", "n_candidate_spreads",
            "max_price_staleness_min",
            "brokerage_per_order", "stt_rate", "exchange_txn_rate", "gst_rate",
            "fallback_lot_sizes",
        ]
        for key in required:
            assert key in STRATEGY_CONFIG, f"Missing key: {key}"

    def test_entry_gates_uses_config_values(self):
        """entry_gates.py should pull MAX_RISK_PCT from strategy_config, not hardcode."""
        from app.services.entry_gates import MAX_RISK_PCT, TARGET_PCT
        from app.services.strategy_config import STRATEGY_CONFIG
        assert MAX_RISK_PCT == STRATEGY_CONFIG["max_risk_pct"]
        assert TARGET_PCT == STRATEGY_CONFIG["target_profit_pct"]

"""Fill reconstruction against the event schema the engines actually write.

These exist because the first version of the report searched for event types
that no engine emits ('LOCK', 'WING_LOCK', ...) and matched normal exits on
event_type == 'EXIT', which only a manual stop uses. Both failures were silent:
the affected fills simply came back unpriced or matched to the wrong minute.
"""
import importlib.util
import pathlib
from datetime import date, datetime

import pytest

# The report is a top-level script, not a package module.
_spec = importlib.util.spec_from_file_location(
    "execution_cost_report",
    pathlib.Path(__file__).resolve().parent.parent / "execution_cost_report.py",
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

TD = date(2026, 8, 31)
EXPIRY = date(2026, 9, 1)


def _run(exit_reason, exit_time="15:25"):
    return {"id": "r1", "trade_date": TD, "exit_time": exit_time,
            "exit_reason": exit_reason, "lot_size": 75, "approved_lots": 13}


def _leg(idx, side, ot, strike, qty=975, entry=100.0, exit_=90.0, entry_ts=None):
    return {"leg_index": idx, "side": side, "option_type": ot, "strike": strike,
            "quantity": qty, "expiry_date": EXPIRY, "entry_timestamp": entry_ts,
            "entry_price": entry, "exit_price": exit_}


def _ev(ts, event_type, reason_code, payload=None):
    return {"timestamp": ts, "event_type": event_type,
            "reason_code": reason_code, "payload_json": payload or {}}


# ── The lock is HOLD/WINGS_LOCKED, not a 'LOCK' event ───────────────────────
def test_wing_legs_get_the_lock_timestamp_from_a_hold_event():
    lock_ts = datetime(2026, 8, 31, 11, 56, 15)
    events = [
        _ev(datetime(2026, 8, 31, 9, 50, 0), "ENTRY", "ENTRY_SCHEDULED"),
        _ev(lock_ts, "HOLD", "WINGS_LOCKED", {"lock_reason": "loss"}),
        _ev(datetime(2026, 8, 31, 15, 25, 3), "TIME_EXIT", "TIME_EXIT"),
    ]
    legs = [_leg(0, "SELL", "CE", 24050), _leg(1, "SELL", "PE", 24050),
            _leg(2, "BUY", "CE", 24150), _leg(3, "BUY", "PE", 23950)]
    fills = report._build_fills(_run("TIME_EXIT"), legs, events)
    opens = {f["label"]: f["timestamp"] for f in fills if f["label"].endswith("open")}
    assert opens["leg2 open"] == lock_ts
    assert opens["leg3 open"] == lock_ts


# ── Normal exits are typed as the fired code, not 'EXIT' ────────────────────
@pytest.mark.parametrize("code", ["TIME_EXIT", "TRAIL_EXIT", "STOP_EXIT", "DATA_GAP_EXIT"])
def test_automatic_exit_uses_the_events_sub_minute_timestamp(code):
    exact = datetime(2026, 8, 31, 15, 25, 3, 471000)
    events = [
        _ev(datetime(2026, 8, 31, 9, 50, 0), "ENTRY", "ENTRY_SCHEDULED"),
        _ev(exact, code, code),
    ]
    fills = report._build_fills(_run(code), [_leg(0, "SELL", "CE", 24050)], events)
    closes = [f for f in fills if f["label"].endswith("close")]
    assert closes and closes[0]["timestamp"] == exact
    # Not rounded down to the minute from the run's exit_time.
    assert closes[0]["timestamp"].second == 3


def test_manual_stop_still_resolves():
    exact = datetime(2026, 8, 31, 15, 18, 42)
    events = [
        _ev(datetime(2026, 8, 31, 9, 50, 0), "ENTRY", "ENTRY_SCHEDULED"),
        _ev(exact, "EXIT", "MANUAL_STOP_EXIT"),
    ]
    fills = report._build_fills(_run("MANUAL_STOP_EXIT", "15:18"),
                                [_leg(0, "SELL", "CE", 24050)], events)
    assert [f for f in fills if f["label"].endswith("close")][0]["timestamp"] == exact


def test_falls_back_to_minute_precision_when_no_terminal_event():
    events = [_ev(datetime(2026, 8, 31, 9, 50, 0), "ENTRY", "ENTRY_SCHEDULED")]
    fills = report._build_fills(_run("TIME_EXIT", "15:25"),
                                [_leg(0, "SELL", "CE", 24050)], events)
    ts = [f for f in fills if f["label"].endswith("close")][0]["timestamp"]
    assert ts == datetime(2026, 8, 31, 15, 25)


# ── Leg-level timestamp wins where the engine recorded one ──────────────────
def test_entry_timestamp_on_the_leg_takes_precedence():
    own = datetime(2026, 8, 31, 11, 54, 36)
    events = [_ev(datetime(2026, 8, 31, 11, 56, 15), "HOLD", "WINGS_LOCKED")]
    legs = [_leg(4, "BUY", "CE", 24150, qty=375, entry_ts=own)]
    fills = report._build_fills(_run("TIME_EXIT"), legs, events)
    assert [f for f in fills if f["label"] == "leg4 open"][0]["timestamp"] == own


# ── Contract identity survives reconstruction ───────────────────────────────
def test_fills_carry_expiry_so_they_cannot_match_another_expiry():
    events = [_ev(datetime(2026, 8, 31, 15, 25, 0), "TIME_EXIT", "TIME_EXIT")]
    fills = report._build_fills(_run("TIME_EXIT"), [_leg(0, "SELL", "CE", 24050)], events)
    assert all(f["expiry_date"] == EXPIRY for f in fills)


def test_hedge_legs_match_their_own_execution_event():
    h1 = datetime(2026, 8, 31, 10, 20, 11)
    h2 = datetime(2026, 8, 31, 11, 54, 36)
    events = [
        _ev(h1, "DELTA_HEDGE", "DELTA_HEDGE_EXECUTED",
            {"lots": 5, "strike": 23950, "option_type": "PE", "quantity": 375, "price": 37.6}),
        _ev(h2, "DELTA_HEDGE", "DELTA_HEDGE_EXECUTED",
            {"lots": 5, "strike": 24150, "option_type": "CE", "quantity": 375, "price": 53.35}),
        _ev(datetime(2026, 8, 31, 15, 25, 0), "TIME_EXIT", "TIME_EXIT"),
    ]
    legs = [_leg(4, "BUY", "PE", 23950, qty=375), _leg(5, "BUY", "CE", 24150, qty=375)]
    opens = {f["label"]: f["timestamp"]
             for f in report._build_fills(_run("TIME_EXIT"), legs, events)
             if f["label"].endswith("open")}
    assert opens["leg4 open"] == h1
    assert opens["leg5 open"] == h2

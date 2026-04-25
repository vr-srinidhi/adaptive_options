"""
Entry rule registry for the generic strategy executor.

Each entry rule implements a single method:
  evaluate(minute_ts, config) → EntrySignal

Currently registered rules
--------------------------
timed_entry   TimedEntryRule   Enter at the configured entry_time. Covers ~38 of
                                40 planned strategies (all non-conditional entries).
orb_breakout  (future)         Wraps existing G1-G7 gate stack.

Adding a new rule
-----------------
1. Subclass BaseEntryRule and implement evaluate().
2. Register in ENTRY_RULES dict below.
That's it — no other files need to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, Optional


# ── Signal returned by every entry rule ───────────────────────────────────────

@dataclass
class EntrySignal:
    action: str          # "ENTER" | "HOLD" | "NO_TRADE"
    reason_code: str
    reason_text: str = ""


# ── Base class ────────────────────────────────────────────────────────────────

class BaseEntryRule(ABC):
    @abstractmethod
    def evaluate(
        self,
        minute_ts: datetime,
        config: Dict[str, Any],
        *,
        trade_open: bool,
    ) -> EntrySignal:
        ...


# ── TimedEntryRule ────────────────────────────────────────────────────────────

_ENTRY_GRACE_MINUTES = 5   # retry window: enter at entry_time or any of the next 5 minutes


class TimedEntryRule(BaseEntryRule):
    """
    Enter at the configured entry_time, with a grace window for missing prices.

    Returns ENTER for any minute in [entry_time, entry_time + _ENTRY_GRACE_MINUTES].
    This lets execute_run retry entry on the next available minute if the exact
    entry_time candle is missing from the warehouse.

    config keys used:
      entry_time  str  "HH:MM"  (required)
    """

    def evaluate(
        self,
        minute_ts: datetime,
        config: Dict[str, Any],
        *,
        trade_open: bool,
    ) -> EntrySignal:
        if trade_open:
            return EntrySignal("HOLD", "ACTIVE_TRADE_EXISTS")

        entry_time_str: str = config.get("entry_time", "09:50")
        try:
            h, m = map(int, entry_time_str.split(":"))
            target = time(h, m)
        except Exception:
            target = time(9, 50)

        from datetime import date as _date, timedelta
        target_end = (
            datetime.combine(_date.today(), target) + timedelta(minutes=_ENTRY_GRACE_MINUTES)
        ).time()

        current = minute_ts.time().replace(second=0, microsecond=0)

        if target <= current <= target_end:
            return EntrySignal("ENTER", "ENTRY_SCHEDULED", f"Entry at {entry_time_str}")
        if current > target_end:
            return EntrySignal(
                "HOLD",
                "PAST_ENTRY_TIME",
                f"Entry time {entry_time_str} already passed",
            )
        return EntrySignal("HOLD", "BEFORE_ENTRY_TIME")


# ── Registry ──────────────────────────────────────────────────────────────────

ENTRY_RULES: Dict[str, BaseEntryRule] = {
    "timed_entry": TimedEntryRule(),
}


def get_entry_rule(rule_id: str) -> BaseEntryRule:
    rule = ENTRY_RULES.get(rule_id)
    if rule is None:
        raise ValueError(
            f"Unknown entry_rule_id '{rule_id}'. "
            f"Registered rules: {list(ENTRY_RULES.keys())}"
        )
    return rule

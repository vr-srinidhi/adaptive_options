"""
Central strategy configuration for Phase 1 ORB paper trading.

All numeric parameters live here. Import from this module — never hardcode
magic numbers in engine or gate logic.
"""
from __future__ import annotations

from datetime import date, time as time_type, timedelta

WORKBENCH_STRATEGY_ID = "orb_intraday_spread"
WORKBENCH_STRATEGY_NAME = "Opening Range Spread"

STRATEGY_CONFIG = {
    # ── Identity ─────────────────────────────────────────────────────────────
    "strategy_name":    "ORB_DEBIT_SPREAD_V1",
    "strategy_version": "v2.0",

    # ── Opening Range ─────────────────────────────────────────────────────────
    "or_window_minutes":     15,       # candles 0–14 form the OR (09:15–09:29)
    "breakout_buffer_pct":   0.001,    # close must be 0.1% beyond OR boundary

    # ── Confirmation ─────────────────────────────────────────────────────────
    "confirmation_mode":         "next_candle_hold",   # two-step confirmation
    "single_trade_per_session":  True,

    # ── Risk / reward ─────────────────────────────────────────────────────────
    "max_risk_pct":       0.02,    # max 2% of capital as max loss
    "target_profit_pct":  0.005,   # target = 0.5% of capital

    # ── Timing ────────────────────────────────────────────────────────────────
    "square_off_time":            time_type(15, 20),  # forced exit at or after 15:20
    "min_minutes_left_to_enter":  20,                 # reject entry if fewer mins remain

    # ── Strike selection ─────────────────────────────────────────────────────
    "strike_step":          50,    # NSE Nifty strike granularity (pts)
    "n_candidate_spreads":  5,     # candidate spread pairs tried per direction
    "selection_method":     "ranked_candidate_selection_v1",

    # ── Price freshness ───────────────────────────────────────────────────────
    "max_price_staleness_min": 5,  # backfill lookback limit (minutes)

    # ── Charges ──────────────────────────────────────────────────────────────
    "brokerage_per_order":  20.0,     # ₹20 flat per executed order
    "stt_rate":             0.0005,   # 0.05% of sell-side premium
    "exchange_txn_rate":    0.00053,  # 0.053% of total premium turnover
    "gst_rate":             0.18,     # 18% on (brokerage + exchange)

    # ── Fallback lot sizes (used only when master lookup fails) ───────────────
    "fallback_lot_sizes": {
        "NIFTY":     75,
        "BANKNIFTY": 35,
    },
}


def shift_weekdays(anchor: date, delta_days: int) -> date:
    """Move by trading weekdays only, skipping weekends."""
    if delta_days == 0:
        return anchor

    current = anchor
    direction = 1 if delta_days > 0 else -1
    remaining = abs(delta_days)

    while remaining:
        current += timedelta(days=direction)
        if current.weekday() < 5:
            remaining -= 1
    return current


def latest_weekday(today: date | None = None) -> date:
    """Return today when it is a weekday, otherwise the most recent weekday."""
    current = today or date.today()
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def build_strategy_snapshot(
    instrument: str,
    capital: float,
    *,
    strategy_id: str | None = None,
    strategy_name: str | None = None,
    run_type: str | None = None,
    input_config: dict | None = None,
) -> dict:
    """Freeze the current strategy config at run creation time."""
    snapshot = {
        "instrument": instrument,
        "capital": capital,
        "strategy_id": strategy_id or STRATEGY_CONFIG["strategy_name"],
        "strategy_name": strategy_name or WORKBENCH_STRATEGY_NAME,
        "strategy_engine": STRATEGY_CONFIG["strategy_name"],
        "strategy_version": STRATEGY_CONFIG["strategy_version"],
        "or_window_minutes": STRATEGY_CONFIG["or_window_minutes"],
        "max_risk_pct": STRATEGY_CONFIG["max_risk_pct"],
        "target_pct": STRATEGY_CONFIG["target_profit_pct"],
        "n_candidate_spreads": STRATEGY_CONFIG["n_candidate_spreads"],
        "max_price_staleness_min": STRATEGY_CONFIG["max_price_staleness_min"],
        "option_price_source": "ltp",
    }
    if run_type is not None:
        snapshot["run_type"] = run_type
    if input_config is not None:
        snapshot["input"] = input_config
    return snapshot

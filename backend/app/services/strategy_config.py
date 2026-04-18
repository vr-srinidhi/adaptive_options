"""
Central strategy configuration for Phase 1 ORB paper trading.

All numeric parameters live here. Import from this module — never hardcode
magic numbers in engine or gate logic.
"""
import os
from datetime import time as time_type


def _parse_time_env(name: str, default: str) -> time_type:
    raw = os.getenv(name, default).strip()
    try:
        hh, mm = raw.split(":")
        return time_type(int(hh), int(mm))
    except Exception as exc:  # pragma: no cover - defensive fail-fast
        raise RuntimeError(
            f"{name} must be a valid HH:MM time, got {raw!r}."
        ) from exc


def _parse_float_env(name: str, default: str) -> float:
    raw = os.getenv(name, default).strip()
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive fail-fast
        raise RuntimeError(
            f"{name} must be a valid float, got {raw!r}."
        ) from exc


STRATEGY_CONFIG = {
    # ── Identity ─────────────────────────────────────────────────────────────
    "strategy_name":    "ORB_DEBIT_SPREAD_V1",
    "strategy_version": "v2.1",

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
    "entry_cutoff_time":          _parse_time_env("ENTRY_CUTOFF_TIME", "13:00"),

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
    "trail_arm_pct":        _parse_float_env("TRAIL_ARM_PCT", "0.30"),
    "trail_giveback":       _parse_float_env("TRAIL_GIVEBACK", "0.40"),

    # ── Fallback lot sizes (used only when master lookup fails) ───────────────
    "fallback_lot_sizes": {
        "NIFTY":     75,
        "BANKNIFTY": 35,
    },
}


def validate_strategy_config_env() -> None:
    cutoff = STRATEGY_CONFIG["entry_cutoff_time"]
    if not 9 <= cutoff.hour <= 15:
        raise RuntimeError(
            "ENTRY_CUTOFF_TIME hour must be between 09 and 15 inclusive."
        )
    if not 0 <= cutoff.minute <= 59:
        raise RuntimeError("ENTRY_CUTOFF_TIME minute must be between 00 and 59.")

    trail_arm_pct = STRATEGY_CONFIG["trail_arm_pct"]
    if trail_arm_pct <= 0:
        raise RuntimeError(
            "TRAIL_ARM_PCT must be > 0. Values above 1 are allowed to disable arming for A/B comparison."
        )

    trail_giveback = STRATEGY_CONFIG["trail_giveback"]
    if not 0 < trail_giveback < 1:
        raise RuntimeError("TRAIL_GIVEBACK must be between 0 and 1 (exclusive).")

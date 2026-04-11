"""
Central strategy configuration for Phase 1 ORB paper trading.

All numeric parameters live here. Import from this module — never hardcode
magic numbers in engine or gate logic.
"""
from datetime import time as time_type

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

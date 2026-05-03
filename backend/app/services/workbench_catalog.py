from __future__ import annotations

from copy import deepcopy

from app.services.strategy_config import latest_weekday, shift_weekdays


def _current_replay_defaults() -> dict:
    anchor = latest_weekday()
    return {
        "paper_replay": {
            "instrument": "NIFTY",
            "capital": 2500000,
            "date": anchor.isoformat(),
            "request_token": "",
        },
        "historical_backtest": {
            "name": "ORB historical replay",
            "instrument": "NIFTY",
            "capital": 2500000,
            "start_date": shift_weekdays(anchor, -22).isoformat(),
            "end_date": anchor.isoformat(),
            "execution_order": "latest_first",
            "autorun": True,
        },
    }


_STRATEGIES = [
    {
        "id": "orb_intraday_spread",
        "name": "Opening Range Spread",
        "bias": "adaptive",
        "status": "available",
        "executor": "orb_v1",
        "modes": ["paper_replay", "historical_backtest"],
        "family": "intraday",
        "playbook": "Breakout confirmation with debit spread execution and full minute audit.",
        "description": (
            "Uses the current Opening Range Breakout engine, validates entry gates, "
            "ranks candidate spreads, and replays the session minute by minute."
        ),
        "chips": ["G1-G7 gates", "Minute audit", "Replay", "Charges aware"],
        "params_schema": [
            {"key": "instrument", "label": "Instrument", "type": "select", "required": True, "options": ["NIFTY", "BANKNIFTY"]},
            {"key": "capital", "label": "Capital", "type": "number", "required": True, "min": 50000, "max": 10000000},
            {"key": "date", "label": "Trade date", "type": "date", "required": True, "modes": ["paper_replay"]},
            {"key": "request_token", "label": "Zerodha request token", "type": "text", "required": False, "modes": ["paper_replay"]},
            {"key": "name", "label": "Backtest name", "type": "text", "required": True, "modes": ["historical_backtest"]},
            {"key": "start_date", "label": "Start date", "type": "date", "required": True, "modes": ["historical_backtest"]},
            {"key": "end_date", "label": "End date", "type": "date", "required": True, "modes": ["historical_backtest"]},
            {"key": "execution_order", "label": "Execution order", "type": "select", "required": True, "modes": ["historical_backtest"], "options": ["latest_first", "oldest_first"]},
            {"key": "autorun", "label": "Auto run", "type": "boolean", "required": False, "modes": ["historical_backtest"]},
        ],
        "defaults": _current_replay_defaults(),
        "visual_hints": {
            "badge": "Opening Range Spread",
            "assumption": "Fills use candle close price and ranked ORB spread candidates. Bid/ask is not available in the current executor.",
            "summary_title": "Opening Range Spread",
            "summary_copy": "Breakout confirms after the OR window. The executor selects the directional debit spread and replays the session minute by minute.",
            "shape": "adaptive",
            "expiry_label": "Weekly (auto)",
            "exit_rule": "Stop / Target / Time",
            "constraint_fields": [
                {"label": "Target %", "value": "45", "hint": "of max profit"},
                {"label": "Stop %", "value": "100", "hint": "of max loss"},
                {"label": "VIX Min", "value": "14", "hint": ""},
                {"label": "VIX Max", "value": "22", "hint": ""},
            ],
            "legs": [
                {"side": "BUY", "option_type": "ENTRY", "strike": "ATM", "expiry": "Weekly", "premium": "auto"},
                {"side": "SELL", "option_type": "HEDGE", "strike": "ATM ± 200", "expiry": "Weekly", "premium": "auto"},
            ],
            "payoff_hint": "Defined-risk breakout profile: capped downside, capped upside once the spread reaches max value.",
            "metrics": {"max_profit_ratio": 0.008, "max_risk_ratio": 0.02, "margin_ratio": 0.12, "max_loss_text": "Defined risk"},
        },
        "notes": [
            "Only fully executable strategy in this release.",
            "Acts as the reference executor behind the new workbench shell.",
        ],
    },
    {
        "id": "buy_call",
        "name": "Buy Call",
        "bias": "bullish",
        "status": "planned",
        "executor": None,
        "modes": ["paper_replay", "historical_backtest"],
        "family": "directional",
        "playbook": "Simple long delta expression with fixed debit risk.",
        "description": "Single-leg bullish expression queued behind the generic run engine rollout.",
        "chips": ["Single leg", "Bullish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "visual_hints": {
            "badge": "Buy Call",
            "assumption": "Single-leg debit setup. Preview shell matches the workbench, while execution remains pending.",
            "summary_title": "Buy Call",
            "summary_copy": "Long bullish delta with fixed debit risk and convex upside exposure.",
            "shape": "call",
            "expiry_label": "Weekly",
            "exit_rule": "Stop / Target / Time",
            "constraint_fields": [
                {"label": "Target %", "value": "60", "hint": "of max profit"},
                {"label": "Stop %", "value": "100", "hint": "premium paid"},
                {"label": "VIX Min", "value": "12", "hint": ""},
                {"label": "VIX Max", "value": "28", "hint": ""},
            ],
            "legs": [
                {"side": "BUY", "option_type": "CE", "strike": "ATM", "expiry": "Weekly", "premium": "auto"},
            ],
            "payoff_hint": "Limited downside to premium paid with open-ended upside.",
            "metrics": {"max_profit_ratio": 0.018, "max_risk_ratio": 0.01, "margin_ratio": 0.06, "max_loss_text": "Premium paid"},
        },
        "notes": ["Included in the catalog so the workbench can grow without redesigning navigation."],
    },
    {
        "id": "sell_put",
        "name": "Sell Put",
        "bias": "bullish",
        "status": "planned",
        "executor": None,
        "modes": ["historical_backtest"],
        "family": "income",
        "playbook": "Premium selling with capped risk overlays still pending.",
        "description": "Will depend on margin-aware sizing and short-option risk controls.",
        "chips": ["Short premium", "Bullish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
    },
    {
        "id": "bull_call_spread",
        "name": "Bull Call Spread",
        "bias": "bullish",
        "status": "planned",
        "executor": None,
        "modes": ["paper_replay", "historical_backtest"],
        "family": "defined_risk",
        "playbook": "Debit spread with fixed risk and directional upside.",
        "description": "Natural next executor after the ORB spread because the leg model already exists.",
        "chips": ["Defined risk", "Bullish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
    },
    {
        "id": "bull_put_spread",
        "name": "Bull Put Spread",
        "bias": "bullish",
        "status": "planned",
        "executor": None,
        "modes": ["paper_replay", "historical_backtest"],
        "family": "credit_spread",
        "playbook": "Credit spread for bullish premium selling.",
        "description": "Requires a more generic entry and fill model than the current ORB executor exposes.",
        "chips": ["Credit spread", "Bullish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
    },
    {
        "id": "buy_put",
        "name": "Buy Put",
        "bias": "bearish",
        "status": "planned",
        "executor": None,
        "modes": ["paper_replay", "historical_backtest"],
        "family": "directional",
        "playbook": "Simple long downside expression with fixed debit risk.",
        "description": "Single-leg bearish expression queued for the generic replay layer.",
        "chips": ["Single leg", "Bearish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "visual_hints": {
            "badge": "Buy Put",
            "assumption": "Single-leg debit setup. Preview shell matches the workbench, while execution remains pending.",
            "summary_title": "Buy Put",
            "summary_copy": "Long bearish delta with fixed debit risk and convex downside exposure.",
            "shape": "put",
            "expiry_label": "Weekly",
            "exit_rule": "Stop / Target / Time",
            "constraint_fields": [
                {"label": "Target %", "value": "60", "hint": "of max profit"},
                {"label": "Stop %", "value": "100", "hint": "premium paid"},
                {"label": "VIX Min", "value": "12", "hint": ""},
                {"label": "VIX Max", "value": "28", "hint": ""},
            ],
            "legs": [
                {"side": "BUY", "option_type": "PE", "strike": "ATM", "expiry": "Weekly", "premium": "auto"},
            ],
            "payoff_hint": "Limited downside to premium paid with accelerated profit on downside expansion.",
            "metrics": {"max_profit_ratio": 0.018, "max_risk_ratio": 0.01, "margin_ratio": 0.06, "max_loss_text": "Premium paid"},
        },
        "notes": [],
    },
    {
        "id": "sell_call",
        "name": "Sell Call",
        "bias": "bearish",
        "status": "planned",
        "executor": None,
        "modes": ["historical_backtest"],
        "family": "income",
        "playbook": "Premium selling with capped risk overlays still pending.",
        "description": "Will depend on margin-aware sizing and short-option risk controls.",
        "chips": ["Short premium", "Bearish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
    },
    {
        "id": "bear_call_spread",
        "name": "Bear Call Spread",
        "bias": "bearish",
        "status": "planned",
        "executor": None,
        "modes": ["paper_replay", "historical_backtest"],
        "family": "credit_spread",
        "playbook": "Defined-risk bearish premium selling.",
        "description": "Pairs naturally with the spread selector framework already in the ORB engine.",
        "chips": ["Credit spread", "Bearish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
    },
    {
        "id": "bear_put_spread",
        "name": "Bear Put Spread",
        "bias": "bearish",
        "status": "planned",
        "executor": None,
        "modes": ["paper_replay", "historical_backtest"],
        "family": "defined_risk",
        "playbook": "Debit spread for directional downside with capped risk.",
        "description": "Next wave candidate once generic spread leg construction is lifted out of ORB.",
        "chips": ["Defined risk", "Bearish", "Planned"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
    },
    {
        "id": "short_straddle",
        "name": "Short Straddle",
        "bias": "neutral",
        "status": "available",
        "executor": "generic_v1",
        "entry_rule_id": "timed_entry",
        "leg_template": [
            {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
            {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
        ],
        "exit_rule": {
            "target_pct":       0.30,
            "target_amount":    12000,
            "stop_loss_amount": 35000,
            "stop_capital_pct": 0.015,
            "time_exit":        "15:25",
            "data_gap_exit":    True,
            "trail_trigger":    12000,
            "trail_pct":        0.50,
        },
        "modes": ["single_session_backtest"],
        "family": "neutral",
        "playbook": "Sell ATM CE + ATM PE at a fixed entry time. Profit from premium decay if NIFTY stays range-bound.",
        "description": (
            "Sells the ATM call and put at a configurable entry time. "
            "Profits when combined premium decays. "
            "Risk is theoretically unlimited — always runs with stop, target, and time exits."
        ),
        "chips": ["Neutral", "Short vol", "Intraday", "Undefined risk"],
        "params_schema": [
            {"key": "trade_date",  "label": "Trade Date",   "type": "date",    "required": True},
            {"key": "entry_time",  "label": "Entry Time",   "type": "time",    "required": True, "default": "09:50"},
            {"key": "capital",     "label": "Capital",      "type": "number",  "required": True, "min": 100000, "max": 50000000},
            {"key": "vix_guardrail_enabled", "label": "VIX Guardrail", "type": "boolean", "required": False, "default": True},
            {"key": "vix_min",       "label": "VIX Min",         "type": "number",  "required": False, "default": 14,    "depends_on": "vix_guardrail_enabled"},
            {"key": "vix_max",       "label": "VIX Max",         "type": "number",  "required": False, "default": 22,    "depends_on": "vix_guardrail_enabled"},
            {"key": "trail_trigger", "label": "Trail Trigger ₹",  "type": "number",  "required": False, "default": 12000, "min": 0},
            {"key": "trail_pct",     "label": "Trail % of Peak",  "type": "number",  "required": False, "default": 0.50,  "min": 0.1, "max": 0.99},
        ],
        "defaults": {
            "single_session_backtest": {
                "instrument": "NIFTY",
                "trade_date": None,
                "entry_time": "09:50",
                "capital": 2500000,
                "vix_guardrail_enabled": True,
                "vix_min": 14,
                "vix_max": 22,
            },
        },
        "visual_hints": {
            "badge": "Short Straddle",
            "assumption": "Fills use candle close price. Bid/ask not available. Results may slightly overestimate entry quality.",
            "summary_title": "Short Straddle",
            "summary_copy": "Sell ATM CE + ATM PE. Profit when spot stays near the strike. Loss accelerates if spot moves sharply in either direction.",
            "shape": "tent",
            "expiry_label": "Weekly (auto)",
            "exit_rule": "Target 30% / Stop 1.5% capital / Time 15:25",
            "constraint_fields": [
                {"label": "Target",        "value": "30%",    "hint": "of entry credit"},
                {"label": "Stop",          "value": "1.5%",   "hint": "of capital (₹37,500 at ₹25L)"},
                {"label": "Trail trigger", "value": "₹12,000","hint": "activate trailing stop"},
                {"label": "Trail %",       "value": "50%",    "hint": "lock in % of peak MTM"},
                {"label": "Time exit",     "value": "15:25",  "hint": ""},
                {"label": "VIX Min",       "value": "14",     "hint": ""},
                {"label": "VIX Max",       "value": "22",     "hint": ""},
            ],
            "legs": [
                {"side": "SELL", "option_type": "CE", "strike": "ATM", "expiry": "Weekly", "premium": "auto"},
                {"side": "SELL", "option_type": "PE", "strike": "ATM", "expiry": "Weekly", "premium": "auto"},
            ],
            "payoff_hint": "Tent-shaped payoff: max profit at ATM strike, unlimited loss on large moves.",
            "metrics": {"max_profit_ratio": 0.006, "max_risk_ratio": None, "margin_ratio": 0.07, "max_loss_text": "Unlimited"},
        },
        "notes": [
            "First strategy on the generic workbench executor (generic_v1).",
            "Adding new timed_entry strategies requires only a catalog entry — no new executor code.",
        ],
    },
    {
        "id": "short_straddle_profit_lock",
        "name": "Short Straddle — Profit Lock",
        "bias": "neutral",
        "status": "available",
        "executor": "straddle_adjustment_v1",
        "entry_rule_id": "timed_entry",
        "leg_template": [
            {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
            {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
        ],
        "exit_rule": {
            "stop_capital_pct": 0.015,
            "time_exit":        "15:25",
            "data_gap_exit":    True,
            "trail_trigger":    12000,
            "trail_pct":        0.50,
            "lock_trigger":     20000,
            "wing_width_steps": 2,
        },
        "sizing": {
            "model": "credit_selling",
        },
        "modes": ["single_session_backtest"],
        "family": "neutral",
        "playbook": (
            "Enter as Short Straddle. Once net MTM crosses the lock trigger, "
            "buy OTM wings mid-session to convert to Iron Condor and cap downside."
        ),
        "description": (
            "Sell ATM CE + PE at entry time. If the trade reaches the profit lock threshold "
            "intraday, buy OTM wings to protect the gain. Trailing stop manages the exit."
        ),
        "chips": ["Neutral", "Short vol", "Adaptive hedge", "Intraday"],
        "params_schema": [
            {"key": "trade_date",    "label": "Trade Date",         "type": "date",    "required": True},
            {"key": "entry_time",    "label": "Entry Time",         "type": "time",    "required": True, "default": "09:50"},
            {"key": "capital",       "label": "Capital",            "type": "number",  "required": True, "min": 100000, "max": 50000000},
            {"key": "lock_trigger",  "label": "Lock Trigger ₹",    "type": "number",  "required": False, "default": 20000, "min": 5000},
            {"key": "wing_width_steps", "label": "Wing Steps",      "type": "number",  "required": False, "default": 2, "min": 1, "max": 5},
            {"key": "trail_trigger", "label": "Trail Trigger ₹",   "type": "number",  "required": False, "default": 12000, "min": 0},
            {"key": "trail_pct",     "label": "Trail % of Peak",    "type": "number",  "required": False, "default": 0.50, "min": 0.1, "max": 0.99},
            {"key": "stop_capital_pct", "label": "Stop % Capital",  "type": "number",  "required": False, "default": 0.015, "min": 0.001, "max": 1},
            {"key": "vix_guardrail_enabled", "label": "VIX Guardrail", "type": "boolean", "required": False, "default": False},
            {"key": "vix_min", "label": "VIX Min", "type": "number", "required": False, "default": 14, "depends_on": "vix_guardrail_enabled"},
            {"key": "vix_max", "label": "VIX Max", "type": "number", "required": False, "default": 30, "depends_on": "vix_guardrail_enabled"},
        ],
        "defaults": {
            "single_session_backtest": {
                "instrument":    "NIFTY",
                "trade_date":    None,
                "entry_time":    "09:50",
                "capital":       2500000,
                "lock_trigger":  20000,
                "wing_width_steps": 2,
                "trail_trigger": 12000,
                "trail_pct":     0.50,
                "stop_capital_pct": 0.015,
                "vix_guardrail_enabled": False,
            },
        },
        "visual_hints": {
            "badge": "Profit Lock",
            "assumption": "Fills use candle close price. Wing purchase at lock minute close. Results may slightly overestimate entry/exit quality.",
            "summary_title": "Short Straddle — Profit Lock",
            "summary_copy": (
                "Sell ATM CE + PE. When net MTM crosses the lock trigger, buy OTM wings "
                "mid-session to cap further loss. Trailing stop manages profit exit."
            ),
            "shape": "tent",
            "expiry_label": "Weekly (auto)",
            "exit_rule": "Lock ₹20k → buy wings / Trail 50% of peak / Stop 1.5% / Time 15:25",
            "constraint_fields": [
                {"label": "Lock trigger", "value": "₹20,000", "hint": "buy wings when MTM crosses"},
                {"label": "Wing steps",   "value": "2",        "hint": "ATM ± steps"},
                {"label": "Trail trigger","value": "₹12,000",  "hint": "activate trailing stop"},
                {"label": "Trail %",      "value": "50%",      "hint": "lock in % of peak MTM"},
                {"label": "Stop",         "value": "1.5%",     "hint": "of capital"},
                {"label": "Time exit",    "value": "15:25",    "hint": ""},
            ],
            "legs": [
                {"side": "SELL", "option_type": "CE", "strike": "ATM",       "expiry": "Weekly", "premium": "auto"},
                {"side": "SELL", "option_type": "PE", "strike": "ATM",       "expiry": "Weekly", "premium": "auto"},
                {"side": "BUY",  "option_type": "CE", "strike": "ATM+steps", "expiry": "Weekly", "premium": "auto (on lock)"},
                {"side": "BUY",  "option_type": "PE", "strike": "ATM-steps", "expiry": "Weekly", "premium": "auto (on lock)"},
            ],
            "payoff_hint": "Starts as a tent (unlimited risk); converts to an Iron Condor once the lock fires.",
            "metrics": {"max_profit_ratio": 0.008, "max_risk_ratio": None, "margin_ratio": 0.07, "max_loss_text": "Capped post-lock"},
        },
        "notes": [
            "Uses straddle_adjustment_v1 executor which supports mid-session leg addition.",
            "Wings are bought at market price at the lock trigger minute.",
            "If wing prices are unavailable at lock time, the position stays as a plain straddle.",
        ],
    },
    {
        "id": "short_straddle_dual_lock",
        "name": "Short Straddle — Dual Lock",
        "bias": "neutral",
        "status": "available",
        "executor": "straddle_adjustment_v1",
        "entry_rule_id": "timed_entry",
        "leg_template": [
            {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
            {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
        ],
        "exit_rule": {
            "stop_capital_pct":  0.015,
            "time_exit":         "15:25",
            "data_gap_exit":     True,
            "trail_trigger":     12000,
            "trail_pct":         0.50,
            "lock_trigger":      20000,
            "loss_lock_trigger": 10000,
            "wing_width_steps":  2,
        },
        "sizing": {
            "model": "credit_selling",
        },
        "modes": ["single_session_backtest"],
        "family": "neutral",
        "playbook": (
            "Enter as Short Straddle. Buy OTM wings mid-session in either direction: "
            "profit lock at +₹20k to protect gains, loss lock at −₹10k to cap downside. "
            "Only the first threshold crossed fires — position becomes Iron Condor."
        ),
        "description": (
            "Sell ATM CE + PE at entry time. Two adaptive hedges: if the trade reaches "
            "+₹20k it buys OTM wings to lock in profit (tent → IC); if it slides to −₹10k "
            "it buys the same wings defensively to cap further loss. Trailing stop manages exit."
        ),
        "chips": ["Neutral", "Short vol", "Dual hedge", "Intraday"],
        "params_schema": [
            {"key": "trade_date",         "label": "Trade Date",        "type": "date",    "required": True},
            {"key": "entry_time",         "label": "Entry Time",        "type": "time",    "required": True,  "default": "09:50"},
            {"key": "capital",            "label": "Capital",           "type": "number",  "required": True,  "min": 100000, "max": 50000000},
            {"key": "lock_trigger",       "label": "Profit Lock ₹",    "type": "number",  "required": False, "default": 20000, "min": 5000},
            {"key": "loss_lock_trigger",  "label": "Loss Lock ₹",      "type": "number",  "required": False, "default": 10000, "min": 2000},
            {"key": "wing_width_steps",   "label": "Wing Steps",        "type": "number",  "required": False, "default": 2, "min": 1, "max": 5},
            {"key": "trail_trigger",      "label": "Trail Trigger ₹",  "type": "number",  "required": False, "default": 12000, "min": 0},
            {"key": "trail_pct",          "label": "Trail % of Peak",   "type": "number",  "required": False, "default": 0.50, "min": 0.1, "max": 0.99},
            {"key": "stop_capital_pct",   "label": "Stop % Capital",    "type": "number",  "required": False, "default": 0.015, "min": 0.001, "max": 1},
            {"key": "vix_guardrail_enabled", "label": "VIX Guardrail",  "type": "boolean", "required": False, "default": False},
            {"key": "vix_min", "label": "VIX Min", "type": "number", "required": False, "default": 14, "depends_on": "vix_guardrail_enabled"},
            {"key": "vix_max", "label": "VIX Max", "type": "number", "required": False, "default": 30, "depends_on": "vix_guardrail_enabled"},
        ],
        "defaults": {
            "single_session_backtest": {
                "instrument":         "NIFTY",
                "trade_date":         None,
                "entry_time":         "09:50",
                "capital":            2500000,
                "lock_trigger":       20000,
                "loss_lock_trigger":  10000,
                "wing_width_steps":   2,
                "trail_trigger":      12000,
                "trail_pct":          0.50,
                "stop_capital_pct":   0.015,
                "vix_guardrail_enabled": False,
            },
        },
        "visual_hints": {
            "badge": "Dual Lock",
            "assumption": "Fills use candle close price. Wings purchased at lock-minute close. One lock fires per session.",
            "summary_title": "Short Straddle — Dual Lock",
            "summary_copy": (
                "Sell ATM CE + PE. Buys OTM wings at +₹20k (profit lock) OR −₹10k (loss lock). "
                "The first threshold crossed converts the naked straddle to Iron Condor."
            ),
            "shape": "tent",
            "expiry_label": "Weekly (auto)",
            "exit_rule": "Profit lock ₹20k / Loss lock ₹10k → buy wings / Trail 50% of peak / Stop 1.5% / Time 15:25",
            "constraint_fields": [
                {"label": "Profit lock",  "value": "₹20,000", "hint": "buy wings when MTM reaches"},
                {"label": "Loss lock",    "value": "−₹10,000","hint": "buy wings when MTM drops to"},
                {"label": "Wing steps",   "value": "2",        "hint": "ATM ± steps"},
                {"label": "Trail trigger","value": "₹12,000",  "hint": "activate trailing stop"},
                {"label": "Trail %",      "value": "50%",      "hint": "lock in % of peak MTM"},
                {"label": "Stop",         "value": "1.5%",     "hint": "of capital"},
                {"label": "Time exit",    "value": "15:25",    "hint": ""},
            ],
            "legs": [
                {"side": "SELL", "option_type": "CE", "strike": "ATM",       "expiry": "Weekly", "premium": "auto"},
                {"side": "SELL", "option_type": "PE", "strike": "ATM",       "expiry": "Weekly", "premium": "auto"},
                {"side": "BUY",  "option_type": "CE", "strike": "ATM+steps", "expiry": "Weekly", "premium": "auto (on lock)"},
                {"side": "BUY",  "option_type": "PE", "strike": "ATM-steps", "expiry": "Weekly", "premium": "auto (on lock)"},
            ],
            "payoff_hint": "Tent (unlimited risk) until lock fires; becomes Iron Condor in either direction.",
            "metrics": {"max_profit_ratio": 0.008, "max_risk_ratio": None, "margin_ratio": 0.07, "max_loss_text": "Capped post-lock"},
        },
        "notes": [
            "Uses straddle_adjustment_v1 executor — same as Profit Lock but with loss_lock_trigger added.",
            "Only ONE lock fires per session — whichever threshold (profit or loss) is crossed first.",
            "If wing prices are unavailable at lock time, position stays as plain straddle.",
            "Backward-compat: setting loss_lock_trigger=0 disables loss lock, behaving like short_straddle_profit_lock.",
        ],
    },
    {
        "id": "short_strangle",
        "name": "Short Strangle",
        "bias": "neutral",
        "status": "research",
        "executor": None,
        "modes": ["historical_backtest"],
        "family": "neutral",
        "playbook": "Wider neutral premium selling with undefined wings.",
        "description": "Requires a stronger risk envelope and adjustment tooling.",
        "chips": ["Neutral", "Short vol", "Research"],
        "params_schema": [],
        "defaults": {},
        "visual_hints": {
            "badge": "Short Strangle",
            "assumption": "Fills use candle close price. Bid/ask not available. Results may overestimate entry quality.",
            "summary_title": "Short Strangle",
            "summary_copy": "Sell OTM CE + OTM PE. Profit when spot stays within short strikes. Loss if spot moves sharply beyond either strike.",
            "shape": "tent",
            "expiry_label": "Weekly (10 Apr)",
            "exit_rule": "Stop / Target / Time",
            "constraint_fields": [
                {"label": "Target %", "value": "45", "hint": "of max profit"},
                {"label": "Stop %", "value": "100", "hint": "of max loss"},
                {"label": "VIX Min", "value": "14", "hint": ""},
                {"label": "VIX Max", "value": "22", "hint": ""},
            ],
            "legs": [
                {"side": "SELL", "option_type": "CE", "strike": "ATM+200", "expiry": "Weekly", "premium": "auto"},
                {"side": "SELL", "option_type": "PE", "strike": "ATM-200", "expiry": "Weekly", "premium": "auto"},
            ],
            "payoff_hint": "Tent-shaped expiry payoff: premium decay wins, large moves hurt.",
            "metrics": {"max_profit_ratio": 0.02, "max_risk_ratio": 0.02, "margin_ratio": 0.28, "max_loss_text": "Unlimited"},
        },
        "notes": [],
    },
    {
        "id": "iron_butterfly",
        "name": "Iron Butterfly",
        "bias": "neutral",
        "status": "available",
        "executor": "generic_v1",
        "entry_rule_id": "timed_entry",
        "leg_template": [
            {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
            {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
            {
                "side": "BUY",
                "option_type": "CE",
                "strike_offset_steps_from_config": "wing_width_steps",
                "strike_offset_sign": 1,
                "default_strike_offset_steps": 2,
            },
            {
                "side": "BUY",
                "option_type": "PE",
                "strike_offset_steps_from_config": "wing_width_steps",
                "strike_offset_sign": -1,
                "default_strike_offset_steps": 2,
            },
        ],
        "exit_rule": {
            "target_pct":       0.30,
            "stop_capital_pct": 0.015,
            "time_exit":        "15:25",
            "data_gap_exit":    True,
            "trail_trigger":    0,
            "trail_pct":        0,
        },
        "sizing": {
            "model": "defined_risk_credit",
            "wing_width_steps_key": "wing_width_steps",
            "margin_floor_per_lot": 100000,
        },
        "modes": ["single_session_backtest"],
        "family": "neutral",
        "playbook": "Defined-risk neutral premium selling around a tight center strike.",
        "description": (
            "Sells the ATM call and put, buys symmetric wings, and exits on "
            "stop, target, time, or data-gap controls."
        ),
        "chips": ["Neutral", "Defined risk", "4 legs", "Executable"],
        "params_schema": [
            {"key": "instrument", "label": "Instrument", "type": "select", "required": True, "options": ["NIFTY", "BANKNIFTY"]},
            {"key": "trade_date", "label": "Trade Date", "type": "date", "required": True},
            {"key": "entry_time", "label": "Entry Time", "type": "time", "required": True, "default": "09:50"},
            {"key": "capital", "label": "Capital", "type": "number", "required": True, "min": 100000, "max": 50000000},
            {"key": "wing_width_steps", "label": "Wing Width Steps", "type": "number", "required": True, "default": 2, "min": 1, "max": 20},
            {"key": "vix_guardrail_enabled", "label": "VIX Guardrail", "type": "boolean", "required": False, "default": True},
            {"key": "vix_min", "label": "VIX Min", "type": "number", "required": False, "default": 14, "depends_on": "vix_guardrail_enabled"},
            {"key": "vix_max", "label": "VIX Max", "type": "number", "required": False, "default": 22, "depends_on": "vix_guardrail_enabled"},
            {"key": "target_amount", "label": "Target ₹", "type": "number", "required": False, "default": 12000, "min": 1},
            {"key": "stop_loss_amount", "label": "Stop ₹", "type": "number", "required": False, "default": 35000, "min": 1},
            {"key": "target_pct", "label": "Target % Credit", "type": "number", "required": False, "default": 0.30, "min": 0.01, "max": 1},
            {"key": "stop_capital_pct", "label": "Stop % Capital", "type": "number", "required": False, "default": 0.015, "min": 0.001, "max": 1},
        ],
        "defaults": {
            "single_session_backtest": {
                "instrument": "NIFTY",
                "trade_date": None,
                "entry_time": "09:50",
                "capital": 2500000,
                "wing_width_steps": 2,
                "vix_guardrail_enabled": True,
                "vix_min": 5,
                "vix_max": 30,
                "target_amount": 12000,
                "stop_loss_amount": 35000,
                "stop_capital_pct": 0.015,
                "target_pct": 0.30,
            },
        },
        "visual_hints": {
            "badge": "Iron Butterfly",
            "assumption": "Fills use candle close price for all four legs. Bid/ask and live margin are not available in the current warehouse replay.",
            "summary_title": "Iron Butterfly",
            "summary_copy": "Sell ATM CE + PE and buy symmetric wings. Profit when spot stays near the center strike, with capped loss beyond either wing.",
            "shape": "butterfly",
            "expiry_label": "Weekly (auto)",
            "exit_rule": "Stop / Target / Time / Data gap",
            "constraint_fields": [
                {"label": "Target", "value": "₹12,000", "hint": "intraday net MTM"},
                {"label": "Stop", "value": "₹35,000", "hint": "max loss"},
                {"label": "Wing steps", "value": "2", "hint": "ATM ± steps"},
                {"label": "Time exit", "value": "15:25", "hint": ""},
                {"label": "VIX Min", "value": "5", "hint": ""},
                {"label": "VIX Max", "value": "30", "hint": ""},
            ],
            "legs": [
                {"side": "SELL", "option_type": "CE", "strike": "ATM", "expiry": "Weekly", "premium": "auto"},
                {"side": "SELL", "option_type": "PE", "strike": "ATM", "expiry": "Weekly", "premium": "auto"},
                {"side": "BUY", "option_type": "CE", "strike": "ATM + N", "expiry": "Weekly", "premium": "auto"},
                {"side": "BUY", "option_type": "PE", "strike": "ATM - N", "expiry": "Weekly", "premium": "auto"},
            ],
            "payoff_hint": "Defined-risk tent: max profit near ATM, max loss capped by wing width minus net credit.",
            "metrics": {"max_profit_ratio": 0.008, "max_risk_ratio": 0.025, "margin_ratio": 0.08, "max_loss_text": "Defined risk"},
        },
        "notes": [
            "Runs on the generic timed-entry executor.",
            "Sizing estimates defined risk from wing width and entry net credit.",
        ],
    },
    {
        "id": "iron_condor",
        "name": "Iron Condor",
        "bias": "neutral",
        "status": "research",
        "executor": None,
        "modes": ["historical_backtest"],
        "family": "neutral",
        "playbook": "Defined-risk short volatility structure with wider wings.",
        "description": "Queued behind the generic multi-leg runtime and adjustment lab.",
        "chips": ["Neutral", "Defined risk", "Research"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
    },
]


def _materialize_strategy(strategy: dict) -> dict:
    item = deepcopy(strategy)
    if item["id"] == "orb_intraday_spread":
        item["defaults"] = _current_replay_defaults()
    elif item["id"] in {"short_straddle", "iron_butterfly"}:
        # Inject a live default trade_date (latest weekday)
        anchor = latest_weekday()
        defaults = deepcopy(item.get("defaults", {}))
        if "single_session_backtest" in defaults:
            defaults["single_session_backtest"]["trade_date"] = anchor.isoformat()
        item["defaults"] = defaults
    return item


def list_strategies() -> list[dict]:
    return [_materialize_strategy(item) for item in _STRATEGIES]


def get_strategy(strategy_id: str | None) -> dict | None:
    if not strategy_id:
        return None
    for strategy in _STRATEGIES:
        if strategy["id"] == strategy_id:
            return _materialize_strategy(strategy)
    return None


def supported_strategy_ids() -> set[str]:
    return {item["id"] for item in _STRATEGIES if item["status"] == "available"}

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
        "status": "research",
        "executor": None,
        "modes": ["historical_backtest"],
        "family": "neutral",
        "playbook": "Short volatility expression for compression days.",
        "description": "Deferred until the platform has richer neutral-risk controls and margin awareness.",
        "chips": ["Neutral", "Short vol", "Research"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
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
        "status": "research",
        "executor": None,
        "modes": ["historical_backtest"],
        "family": "neutral",
        "playbook": "Defined-risk neutral premium selling around a tight center strike.",
        "description": "Depends on a generic four-leg execution contract.",
        "chips": ["Neutral", "Defined risk", "Research"],
        "params_schema": [],
        "defaults": {},
        "notes": [],
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

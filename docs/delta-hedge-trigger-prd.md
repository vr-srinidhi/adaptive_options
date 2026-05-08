# PRD: Delta Hedge Trigger for AdaptiveOptions

Version: 1.0
Date: 7 May 2026
Status: Ready for Development
Instrument: NIFTY Short Straddle, slot-based

## Problem Statement

The current risk management system relies on price-based controls such as Profit Lock, Loss Lock, and Trail Trigger. These are reactive because they fire only after P&L damage has already accumulated. On trending days, a short straddle can become directionally skewed before the Loss Lock fires, leading to outsized drawdowns.

Example: on 7 May 2026, a 13-lot NIFTY 24350 short straddle entered at 10:15 reached an intraday MTM near -Rs 31k before partially recovering to a final P&L near -Rs 25,746, despite a Rs 25,000 Loss Lock.

This feature introduces a delta-aware hedging layer that acts proactively by monitoring directional skew in real time.

## Goals

- Detect delta imbalance in a short straddle position in real time.
- Automatically execute a hedge action when delta breaches a configurable threshold.
- Preserve the core trade where possible while capping directional risk.
- Operate as an optional, opt-in layer through a per-slot checkbox.
- Work inside the existing slot-based architecture without disrupting existing controls.

## Non-Goals for v1

- Multi-leg strategies beyond short straddles or strangles.
- Replacing Loss Lock. Delta Hedge is a prior layer of defense.
- Live Zerodha order execution. Phase 1 is paper mode only.
- Portfolio-level delta hedging across multiple slots.

## Key Concepts

Net Position Delta:

```text
Net Delta = (-1 * Delta_CE * CE_Qty) + (-1 * Delta_PE * PE_Qty)
```

Both entry legs are short. At ATM entry, net delta is approximately zero. In a rising market, CE delta grows and PE delta shrinks, making net delta increasingly negative.

Delta Threshold is the absolute net delta value beyond which the system considers the position directionally skewed and eligible for hedging.

## Slot-Level Feature Flag

Add a master checkbox at the top of a new Delta Hedge Settings section.

| Property | Detail |
| --- | --- |
| Label | Enable Delta Hedge |
| Type | Boolean checkbox |
| Default | Unchecked |
| Persisted | Yes, per slot |

When unchecked, the slot must run exactly as it does today. No delta should be computed, no hedge should be evaluated, and no additional delta events should be emitted.

When checked, delta hedge logic starts after entry and runs every refresh cycle.

## Configuration

```json
{
  "deltaHedge": {
    "enabled": false,
    "deltaThreshold": 150,
    "hedgeAction": "BUY_WING",
    "hedgeQtyMode": "PARTIAL",
    "maxHedgeTriggers": 3,
    "reentryBuffer": 50
  }
}
```

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| Delta Hedge Enabled | Boolean | OFF | Master feature flag |
| Delta Threshold | Number | 150 | Absolute net delta at which hedge fires |
| Hedge Action | Enum | BUY_WING | Action taken when threshold is breached |
| Hedge Qty Mode | Enum | PARTIAL | Full or partial imbalance hedge |
| Max Hedge Triggers | Integer | 3 | Maximum hedge fires per session per slot |
| Delta Re-entry Buffer | Number | 50 | Delta must normalize below threshold minus buffer before another trigger |

## Delta Calculation

Compute delta per slot on every refresh cycle using Black-Scholes for European options.

Inputs:

- Current option price from live or replay feed.
- Underlying spot price.
- Strike.
- Time to expiry.
- Implied volatility from Black-Scholes inversion.
- Risk-free rate fixed at 6.5%.

Formulas:

```text
d1 = [ln(S/K) + (r + sigma^2 / 2) * T] / (sigma * sqrt(T))
Delta_CE = N(d1)
Delta_PE = N(d1) - 1
```

## Hedge Actions

### BUY_WING

Default v1 action. Buy an OTM option on the tested side to cap directional loss.

- If net delta is negative, market is rising and the tested side is CE. Buy the CE wing at `ATM + wing_width_steps * strike_step`.
- If net delta is positive, market is falling and the tested side is PE. Buy the PE wing at `ATM - wing_width_steps * strike_step`.

### Later Actions

The PRD also lists REDUCE_LOTS, FUTURES_HEDGE, and FULL_EXIT as future actions. These are not part of the recommended Phase 1 implementation.

## Trigger State Machine

```text
MONITORING
  -> abs(Net Delta) > Delta Threshold
HEDGE_TRIGGERED
  -> Hedge action executed
HEDGED
  -> abs(Net Delta) < Delta Threshold - Re-entry Buffer
MONITORING
  -> trigger count >= Max Hedge Triggers
HEDGE_EXHAUSTED
```

Guard conditions:

- Position must be active.
- Current time must be between 09:20 and 15:20.
- Trigger count must be below Max Hedge Triggers.
- Minimum 5 minutes between consecutive hedge triggers.

## IV Source

1. Primary: implied volatility from option premium using Newton-Raphson.
2. Secondary: India VIX proxy when inversion fails.
3. Fallback: configurable default IV, for example 12%.

## Event Log

| Event Code | Description |
| --- | --- |
| DELTA_HEDGE_TRIGGERED | Net delta breached threshold; hedge action started |
| DELTA_HEDGE_EXECUTED | Hedge leg added successfully |
| DELTA_HEDGE_FAILED | Hedge action failed |
| DELTA_NEUTRAL_RESTORED | Net delta returned within safe range |
| DELTA_HEDGE_EXHAUSTED | Max triggers reached; Loss Lock remains primary stop |

Each event should log timestamp, net delta, action, side, strike, price, and quantity or lots involved.

## UI Summary

- Slot Configuration: add Delta Hedge Settings below Trail Trigger. Child fields are disabled when unchecked.
- Live Slot Card: show Net Delta below Net MTM, including hedge count.
- MTM Chart: show delta line on a secondary axis, threshold bands, and vertical markers for hedge triggers.
- Event Log: show delta hedge events with distinct color treatment.

## Phase Plan

Phase 1, Paper Mode:

- Black-Scholes delta calculation from premiums.
- BUY_WING action.
- Config UI with feature flag checkbox.
- Event log integration.
- Chart delta overlay.

Phase 2, Enhanced Actions:

- REDUCE_LOTS and FUTURES_HEDGE.
- Per-leg delta breakdown in UI.
- Broader replay/backtest analysis.

Phase 3, Live Mode:

- Zerodha order execution for hedge legs.
- Margin checks before firing.
- Push alerts.

## Success Metrics

- On a trending day such as 7 May 2026, delta hedge should fire around the large directional skew and target a 40-50% loss reduction against baseline.
- False trigger rate should stay below 20% over a 30-session sample.
- Hedge should not produce a worse outcome than baseline Loss Lock over a 30-session sample.

## Open Questions

1. Should BUY_WING use the existing Wing Width Steps or a separate delta hedge wing width?
2. Should REDUCE_LOTS allow re-shorting if delta normalizes?
3. Should future live mode use Zerodha WebSocket greeks if available, or keep computing greeks internally?

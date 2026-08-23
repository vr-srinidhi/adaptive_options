# PRD: Live Payoff Chart for AdaptiveOptions

Version: 1.0
Date: 8 May 2026
Status: Delivered
Instrument: NIFTY Short Straddle (all slots), live paper session view

## Problem Statement

The Live Paper Monitor (`/workbench/live`) shows real-time Net MTM and premium price charts for the straddle core legs (CE SELL, PE SELL). Two gaps existed:

1. **No at-expiry projection**: traders had no visual of the projected P&L across spot levels. The existing MTM chart shows time-series drawdown, not the payoff shape. On volatile days it was impossible to read at a glance whether the trade was structurally sound or had broken its neutrality.

2. **Wing/hedge premium charts missing data**: when a delta hedge fires and a BUY_WING leg is added (CE wing or PE wing), the premium chart for that leg showed no prices. The backend SSE broadcast was emitting only the straddle leg prices, and the historical replay query was filtering out `side == "SELL"` legs — excluding BUY legs from both live and replayed views.

## Goals

- Add a new **at-expiry payoff chart** on the Live Slot card that renders the profit-loss curve across a spot range and updates on every MTM tick.
- Automatically incorporate BUY legs (delta hedge wings) into the payoff curve whenever they are added to the position, so the chart reflects the adjusted strategy shape.
- Fix the backend SSE broadcast and the historical replay query so wing/hedge BUY leg prices appear in the CE Premium and PE Premium charts.
- Zero regression on the existing NIFTY Spot, MTM, CE Premium, and PE Premium charts.
- Full multi-slot isolation — no state bleed between concurrent slot tabs.
- Mobile-first layout — chart must render clearly on narrow viewports.

## Non-Goals

- Strategy builder or what-if scenario modelling.
- Greeks surface or probability cone visualisations.
- Modification of existing Spot / MTM / CE Premium / PE Premium charts.
- New backend database tables or API endpoints.
- Live Zerodha order execution.

## Key Concepts

### At-Expiry Payoff Formula

For each spot level S in the visible range, the payoff is computed by summing across all open legs:

```text
For SELL legs:
  leg_pnl = (entry_price − intrinsic(S)) × qty

For BUY legs:
  leg_pnl = (intrinsic(S) − entry_price) × qty

Where:
  intrinsic(S) for CE = max(0, S − strike)
  intrinsic(S) for PE = max(0, strike − S)
  qty = lot_size × approved_lots
```

Total payoff = Σ leg_pnl across all legs.

At ATM entry with only SELL legs, the curve is tent-shaped, peaking at the ATM strike. When BUY wings are added, the curve flattens and caps loss at the wing strikes — producing an Iron Butterfly payoff shape.

### Multi-Slot Isolation

The `liveData` state object in `LivePaperMonitor.jsx` is keyed by `session.id`. All new fields — `legs`, `wingCeData`, `wingPeData` — are stored inside `liveData[sessionId]` following the existing per-slot pattern. Each slot tab renders its own `PayoffChart` with a gradient SVG ID scoped to the current ATM strike (`payoffGrad-${atm}`) to prevent SVG rendering conflicts across tabs.

## Technical Design

### Frontend — New PayoffChart Component

A new self-contained `PayoffChart` component in `LivePaperMonitor.jsx`:

- Renders using Recharts `AreaChart` with a custom `linearGradient` fill.
- Computes 100 evenly-spaced spot levels across `[ATM − 500, ATM + 500]` (or tighter if the trading range is narrower).
- Calls `calcPayoffAtExpiry(legs, spot, qty)` at each spot level where `legs` is the array of open legs with `{ side, option_type, strike, entry_price }`.
- The gradient fill splits at the zero-crossing: green above, red below. The zero-crossing fraction is computed as `yDomain[1] / (yDomain[1] − yDomain[0])` to position the colour boundary accurately regardless of scale.
- `isAnimationActive={false}` on the `Area` component prevents animation jank on each SSE tick.
- Gradient SVG IDs are scoped per slot: `id={payoffGrad-${atm}}`.

### Frontend — SSE State Updates

Three new fields are added to each slot's state inside `liveData[sessionId]`:

| Field | Type | Set by event |
| --- | --- | --- |
| `legs` | Array of leg objects | `ENTRY`, `LOCK` |
| `wingCeData` | Array of `{ timestamp, price }` | `MTM` (when wing is active) |
| `wingPeData` | Array of `{ timestamp, price }` | `MTM` (when wing is active) |

The `ENTRY` event populates the two SELL legs (CE and PE at ATM) using `ce_price`, `pe_price`, `atm_strike`, `lot_size`, `approved_lots` fields that are now included in the broadcast.

The `LOCK` event appends BUY wing legs using `wing_ce_strike`, `wing_pe_strike`, `wing_ce_price`, `wing_pe_price` fields now included in the broadcast.

The `MTM` event appends to `wingCeData` and `wingPeData` when `wing_ce_price` and `wing_pe_price` are non-null in the broadcast.

### Backend — SSE Broadcast Fix (live_paper_engine.py)

Added missing fields to the `ENTRY` and `LOCK` broadcast payloads:

- `ENTRY`: `lot_size`, `approved_lots`
- `LOCK`: `wing_ce_strike`, `wing_pe_strike`
- `MTM`: `wing_ce_price` (the current CE wing price, null if wings not locked), `wing_pe_price` (the current PE wing price, null if wings not locked)

### Backend — Historical Replay Fix (live_paper.py)

`_get_mtm_series()` previously filtered to `side == "SELL"`, excluding BUY wing legs from the per-minute price lookup. The fix:

- Remove the `side` filter from the leg query so all legs are returned.
- Separate the SELL core leg lookup (indices 0 and 1) from the BUY wing lookup (indices 2 and 3).
- Include `wing_ce_price` and `wing_pe_price` in every MTM row (null until wings are added).

`_build_slot()` is similarly updated to query all legs (not just SELL) and return the `legs` array in `run_info` for payoff chart initialisation on page load.

## Data Flow

```
SSE live feed:
  SNAPSHOT → seed liveData[sessionId] with legs/wingCeData/wingPeData = []
  ENTRY     → set legs = [SELL CE, SELL PE] from broadcast fields
  LOCK      → append BUY CE, BUY PE wing legs to legs array
  MTM       → append wing prices to wingCeData / wingPeData when non-null

Historical reload (page refresh after session complete):
  GET /api/v2/live-paper/today
    → run_info.legs (all legs from strategy_run_legs)
    → mtm_series rows with wing_ce_price / wing_pe_price per minute
    → wingCeData / wingPeData reconstructed in loadToday() from mtm_series
```

## UI Layout

The `PayoffChart` is inserted **after** the existing CE Premium and PE Premium charts within the `SlotDetail` section. It is always visible once the trade has entered (legs array is non-empty). On mobile (`max-width: 767px`) the payoff chart stretches full width, matching the existing `.live-two-col` responsive grid pattern.

Chart header: **Payoff at Expiry** with a grey subtitle showing the spot range (e.g. `Spot range: 24000 – 24500`).

## Acceptance Criteria

1. On a session with no active trade, the payoff chart is not shown.
2. After ENTRY fires, a tent-shaped curve appears centred at the ATM strike.
3. After the lock wings are bought (LOCK event), the curve updates to the capped Iron Butterfly shape. A BUY_WING delta hedge is sized to the delta gap and is usually smaller than the straddle, so it caps only its own share of the tail; the chart weights every leg by its own `quantity` rather than assuming full size.
4. On every MTM tick, the current spot is marked on the x-axis with a vertical reference line.
5. The at-expiry P&L zero line is clearly visible on the y-axis.
6. Wing leg prices appear in the CE Premium and PE Premium charts after LOCK fires.
7. Historical page load reconstructs the correct payoff shape from the stored legs.
8. Opening two slots simultaneously shows independent payoff curves with no gradient ID conflict.
9. Existing Spot / MTM / CE Premium / PE Premium charts are visually unchanged.
10. Mobile view: chart is full-width and readable at 375px viewport width.

# SKILLS.md — Options Strategy Reference

Technical reference for all strategies implemented or catalogued in Adaptive Options, plus the regime detection framework, position sizing, and exit logic.

---

## Strategy Overview

### Live Strategies (V2 Workbench)

| ID | Name | Executor | Run Type | Bias | Status |
|----|------|----------|----------|------|--------|
| `orb_intraday_spread` | Opening Range Spread | `orb_v1` | paper_replay / historical_backtest | Directional | **available** |
| `short_straddle` | Short Straddle | `generic_v1` | single_session_backtest | Neutral | **available** |
| `iron_butterfly` | Iron Butterfly | `generic_v1` | single_session_backtest | Neutral | **available** |

### Synthetic Backtest Strategies

| Strategy | Regime | Bias | Max Profit | Max Loss |
|----------|--------|------|-----------|---------|
| Iron Condor | Neutral / High IV | Range-bound | Net credit received | Spread width − credit |
| Bull Put Spread | Bullish / Low IV | Directional up | Net credit received | Spread width − credit |
| Bear Call Spread | Bearish / Low IV | Directional down | Net credit received | Spread width − credit |

### Catalogued Strategies (Planned / Research)

The workbench catalog (`workbench_catalog.py`) lists 13 strategies total. Three are `available`; the rest are `planned` or `research`. Adding any of the planned strategies requires only a catalog entry — `generic_executor.py` handles execution.

| Name | Bias | Executor | Status |
|------|------|----------|--------|
| Opening Range Spread | Directional | orb_v1 | available |
| Short Straddle | Neutral | generic_v1 | **available** |
| Iron Butterfly | Neutral / Defined risk | generic_v1 | **available** |
| Iron Condor | Neutral | generic_v1 | planned |
| Bull Put Spread | Bullish | generic_v1 | planned |
| Bear Call Spread | Bearish | generic_v1 | planned |
| Long Straddle | Neutral (high vol) | generic_v1 | planned |
| Short Strangle | Neutral (low vol) | generic_v1 | planned |
| Calendar Spread | Time decay | generic_v1 | research |
| Ratio Back Spread | Directional | generic_v1 | research |
| Jade Lizard | Bullish slight | generic_v1 | research |
| … | … | generic_v1 | planned/research |

---

## Opening Range Breakout (ORB) Strategy

### Concept

After the first 15 minutes of trading (09:15–09:29), the high and low of that range define a **consolidation zone**. A breakout above or below this zone — confirmed over two consecutive candles — signals a directional bias for the day. The ORB engine then enters a **debit spread** in the direction of the breakout.

### Decision Flow

```
09:15 – 09:29   Collect opening range (15 candles)
09:30 onwards   Each minute:
                  If no trade → evaluate G1–G7 gate stack
                  If trade open → check exit conditions
15:20           Force square-off (EXIT_TIME)
```

### G1–G7 Gate Stack

| Gate | Rule | Fail Code |
|------|------|-----------|
| G1 | Opening range window complete (15 candles elapsed) | `OPENING_RANGE_NOT_READY` |
| G2 | No active trade already open | `ACTIVE_TRADE_EXISTS` |
| G3 | Close > OR high × 1.001 (bull) or < OR low × 0.999 (bear) | `NO_BREAKOUT_CONFIRMATION` |
| G4 | Previous candle also confirmed same direction | `FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH` |
| G5 | Both spread legs have valid market prices | `NO_HEDGE_AVAILABLE` |
| G6 | Max loss ≤ 2% of capital (approved_lots ≥ 1) | `RISK_EXCEEDS_CAP` |
| G7 | Max possible gain ≥ 0.5% of capital | `TARGET_NOT_VIABLE` |

G3–G4 together require a **two-candle confirmation** — preventing false breakout entries on a single spike candle.

### Spread Selection

Five strike pairs tried per direction, ATM-first (offsets 0, ±1, ±2 × 50 pts):

```
Bull breakout → Bull Call Spread: BUY ATM CE, SELL ATM+50 CE
Bear breakout → Bear Put Spread:  BUY ATM PE, SELL ATM-50 PE
```

First pair passing G5–G7 wins (Phase 2: ranked by `spread_selector.py`).

### Exit Conditions

| Condition | Trigger |
|-----------|---------|
| `EXIT_TARGET` | Total MTM ≥ 0.5% of capital |
| `EXIT_STOP` | Total MTM ≤ −max_loss |
| `EXIT_TIME` | Candle timestamp ≥ 15:20 |

### Round-trip Charges

```
brokerage  = 4 × ₹20                          (4 legs: buy+sell entry, buy+sell exit)
STT        = 0.05%  × sell-side premium × qty
exchange   = 0.053% × total premium turnover × qty
GST        = 18%    × (brokerage + exchange)
net_pnl    = gross_pnl − total_charges
```

---

## Generic Strategy Engine

### Architecture

Any strategy expressed as `leg_template + entry_rule_id + exit_rule` in the catalog runs on `generic_executor.py` with zero custom code. Adding a strategy = adding a catalog entry.

```python
# Example: Iron Condor on generic_v1
{
    "leg_template": [
        {"side": "SELL", "option_type": "PE", "strike_offset_steps": -2},
        {"side": "BUY",  "option_type": "PE", "strike_offset_steps": -4},
        {"side": "SELL", "option_type": "CE", "strike_offset_steps":  2},
        {"side": "BUY",  "option_type": "CE", "strike_offset_steps":  4},
    ],
    "entry_rule_id": "timed_entry",
    "exit_rule": {"target_pct": 0.45, "stop_multiple": 2.0, "time_exit": "15:25", "data_gap_exit": True},
}
```

### Entry Rules (`entry_rule_registry.py`)

| Rule ID | Class | Behaviour |
|---------|-------|-----------|
| `timed_entry` | `TimedEntryRule` | Enter exactly at `config["entry_time"]`; HOLD before, NO_TRADE after |

To add a conditional entry rule: subclass `BaseEntryRule`, implement `evaluate(minute_ts, config, *, trade_open) → EntrySignal`, register in `ENTRY_RULES` dict.

### Exit Conditions

| Code | Trigger | Formula |
|------|---------|---------|
| `TARGET_EXIT` | Profit target hit | `net_mtm >= entry_credit_total × target_pct` |
| `STOP_EXIT` | Loss limit hit | `net_mtm <= -(entry_credit_total × stop_multiple)` |
| `TIME_EXIT` | Square-off time reached | `minute_ts.time() >= exit_rule["time_exit"]` |
| `DATA_GAP_EXIT` | Stale option prices | any leg stale > 1 minute (configurable via `_MAX_STALE_MINUTES`) |

### MTM Formula (short positions)

```
gross_mtm_per_unit = Σ (entry_price_i − current_price_i)   for each SELL leg
gross_mtm_total    = gross_mtm_per_unit × lot_size × approved_lots
est_exit_charges   = compute_exit_charges_estimate(lots, lot_size, current_prices)
net_mtm            = gross_mtm_total − entry_charges − est_exit_charges
```

### Charges Service (`charges_service.py`)

Single source of truth for NSE F&O brokerage. Used by both generic executor and ORB engine.

```
brokerage  = N_orders × ₹20           (₹20 flat per order, Zerodha)
STT        = 0.05%  × sell premium × qty   (sell-side only)
exchange   = 0.053% × total premium × qty
GST        = 18%    × (brokerage + exchange)
total      = brokerage + STT + exchange + GST
```

### Lot Size History (`instrument_contract_specs`)

NSE changed NIFTY lot size in Nov 2024. The engine reads from the DB for historical accuracy:

| Instrument | Period | Lot Size | Strike Step |
|------------|--------|----------|-------------|
| NIFTY | up to 2024-11-20 | 50 | 50 |
| NIFTY | 2024-11-21 onwards | 75 | 50 |
| BANKNIFTY | up to 2024-11-20 | 25 | 100 |
| BANKNIFTY | 2024-11-21 onwards | 35 | 100 |

---

## Short Straddle Strategy

### Concept

Sell both ATM Call and ATM Put simultaneously. Collect premium from both sides; profit if spot stays range-bound until exit. Unlimited theoretical risk if spot moves sharply.

```
Payoff at expiry
        ▲ Profit
        │    /\
        │   /  \
────────┼──/────\──────── Spot
        │ /      \
        │/        \
       Loss (unlimited)
ATM-x  ATM     ATM+x
```

### Legs

| Action | Type | Strike | Rationale |
|--------|------|--------|-----------|
| SELL | CE | ATM | Collect call premium; profit if spot ≤ ATM at exit |
| SELL | PE | ATM | Collect put premium; profit if spot ≥ ATM at exit |

ATM = `round(spot_at_entry / strike_step) * strike_step`

### Parameters

| Key | Default | Description |
|-----|---------|-------------|
| `trade_date` | latest weekday | Date to run the session on |
| `entry_time` | `09:50` | Minute to enter — must have spot + both option prices |
| `capital` | — | Required; approved_lots = floor(capital / est_margin_per_lot) |
| `vix_guardrail_enabled` | `true` | Skip trade if VIX outside [vix_min, vix_max] |
| `vix_min` | 14 | Minimum VIX to allow entry |
| `vix_max` | 22 | Maximum VIX to allow entry |

### Exit Conditions

| Condition | Trigger |
|-----------|---------|
| `TARGET_EXIT` | Suppressed when trail is active — trail manages profit exit |
| `TRAIL_EXIT` | Activates once net_mtm ≥ ₹12,000; exits when net_mtm falls to 50% of peak. P&L locked at trail stop level (not candle close). |
| `STOP_EXIT` | net_mtm ≤ −1.5% of capital (capital-based, not credit-based) |
| `TIME_EXIT` | 15:25 |
| `DATA_GAP_EXIT` | option price stale > 1 minute |

---

## Iron Butterfly Strategy

### Concept

Sell the ATM straddle and buy OTM wings to cap maximum loss. Defined-risk version of the short straddle — collects premium from premium decay while capping tail risk.

```
Payoff at expiry
        ▲ Profit
        │     /\
        │    /  \
────────┼───/────\───────── Spot
        │__/      \__
       Max loss (capped)
BUY PE  SELL PE  SELL CE  BUY CE
ATM-N   ATM      ATM      ATM+N
```

### Legs

| Action | Type | Strike | Rationale |
|--------|------|--------|-----------|
| SELL | CE | ATM | Collect call premium |
| SELL | PE | ATM | Collect put premium |
| BUY  | CE | ATM + N×step | Cap upside loss |
| BUY  | PE | ATM − N×step | Cap downside loss |

N = `wing_width_steps` (user input); step = `strike_step` from `instrument_contract_specs`.

### Parameters

| Key | Default | Description |
|-----|---------|-------------|
| `trade_date` | latest weekday | Date to run |
| `entry_time` | `09:50` | Minute to enter |
| `capital` | — | Required |
| `wing_width_steps` | 2 | Number of strike steps for OTM wings (e.g. 2 × 50 = 100 pts for NIFTY) |
| `target_pct` | 0.30 | Exit when net_mtm ≥ target_pct × entry_credit_total |
| `stop_capital_pct` | 0.015 | Exit when net_mtm ≤ −stop_capital_pct × capital |
| `vix_guardrail_enabled` | true | Skip trade if VIX outside [vix_min, vix_max] |
| `vix_min` | 14 | Minimum VIX |
| `vix_max` | 22 | Maximum VIX |

### MTM Formula

Mixed SELL/BUY legs require sign-aware computation:

```
gross_mtm_per_unit = Σ (entry_price_i − current_price_i)   for SELL legs
                   + Σ (current_price_i − entry_price_i)   for BUY legs
gross_mtm_total    = gross_mtm_per_unit × lot_size × approved_lots
net_mtm            = gross_mtm_total − entry_charges − est_exit_charges
```

Margin sizing uses `_defined_risk_margin_per_lot()`:
```
max_loss_per_lot = wing_width_steps × strike_step × lot_size − net_credit_per_lot
approved_lots    = floor(capital / max_loss_per_lot)
```

### Exit Conditions

| Condition | Trigger |
|-----------|---------|
| `TARGET_EXIT` | net_mtm ≥ target_pct × entry_credit_total |
| `STOP_EXIT` | net_mtm ≤ −stop_capital_pct × capital |
| `TIME_EXIT` | 15:25 |
| `DATA_GAP_EXIT` | any option price stale > 1 minute |

Trail disabled by default. Can be enabled via `trail_trigger` / `trail_pct` config params.

### Charge Handling

Closing a BUY leg is a SELL order; closing a SELL leg is a BUY order:
- Entry: 4 orders (2 SELL + 2 BUY) — STT on SELL legs only
- Exit: 4 orders (2 BUY to close shorts + 2 SELL to close longs) — STT on SELL (close of BUY legs)

---

## Synthetic Backtest Strategies

All three are **defined-risk credit spreads** — you collect premium upfront and profit if the position expires worthless or is closed at a profit target. Maximum loss is capped by the long leg.

### Iron Condor

Combines a Bull Put Spread (below market) and a Bear Call Spread (above market) simultaneously.

```
Profit zone
     │
─────┼──────────────────────────────────────┼─────
     │                                      │
  Long Put      Short Put   Short Call   Long Call
  ATM−5×tick   ATM−3×tick  ATM+3×tick  ATM+5×tick
```

**Legs (Nifty example at ATM 22,000, tick=50)**

| Action | Type | Strike | Role |
|--------|------|--------|------|
| BUY | PE | 21,750 (ATM−5) | Protection (long put) |
| SELL | PE | 21,850 (ATM−3) | Premium collection |
| SELL | CE | 22,150 (ATM+3) | Premium collection |
| BUY | CE | 22,250 (ATM+5) | Protection (long call) |

**P&L Profile**

- **Max Profit** = net credit × lots × lot_size
- **Max Loss** = (spread_width − net_credit) × lots × lot_size
- **Break-evens** = short put strike − net_credit/unit, short call strike + net_credit/unit
- **Profit target** = 45% of max profit
- **Best in** = low volatility, range-bound markets (NEUTRAL regime, IV Rank ≥ 30)

**When Selected**

- NEUTRAL: EMAs intertwined (diff < 0.15%), RSI 40–60, IV Rank ≥ 30
- BULLISH: EMA5 > EMA20 (≥0.15%), RSI 40–70, IV Rank ≥ 30
- BEARISH: EMA5 < EMA20 (≥0.15%), RSI 30–60, IV Rank ≥ 30

---

### Bull Put Spread

A vertical credit spread using put options below the current market price. Profits if the index stays above the short put strike.

**Legs (Nifty example at ATM 22,000, tick=50)**

| Action | Type | Strike | Role |
|--------|------|--------|------|
| SELL | PE | 21,900 (ATM−2) | Premium collection (short put) |
| BUY | PE | 21,800 (ATM−4) | Protection (long put) |

**P&L Profile**

- **Max Profit** = net credit × lots × lot_size
- **Max Loss** = (spread_width − net_credit) × lots × lot_size
- **Break-even** = short put strike − net_credit/unit
- **Profit target** = 55% of max profit
- **Best in** = bullish or sideways markets with low IV (IV Rank < 30)

**When Selected**

- BULLISH: EMA5 > EMA20 (≥0.15%), RSI 40–70, IV Rank < 30

---

### Bear Call Spread

A vertical credit spread using call options above the current market price. Profits if the index stays below the short call strike.

**Legs (Nifty example at ATM 22,000, tick=50)**

| Action | Type | Strike | Role |
|--------|------|--------|------|
| SELL | CE | 22,100 (ATM+2) | Premium collection (short call) |
| BUY | CE | 22,200 (ATM+4) | Protection (long call) |

**P&L Profile**

- **Max Profit** = net credit × lots × lot_size
- **Max Loss** = (spread_width − net_credit) × lots × lot_size
- **Break-even** = short call strike + net_credit/unit
- **Profit target** = 55% of max profit
- **Best in** = bearish or sideways markets with low IV (IV Rank < 30)

**When Selected**

- BEARISH: EMA5 < EMA20 (≥0.15%), RSI 30–60, IV Rank < 30

---

## Regime Detection (Synthetic Backtest)

### EMA Crossover

**Exponential Moving Average** gives more weight to recent prices:

```
EMA(today) = price × k + EMA(yesterday) × (1 − k)
           where k = 2 / (period + 1)
```

- **EMA(5) > EMA(20) by ≥ 0.15%** → Bullish momentum
- **EMA(5) < EMA(20) by ≥ 0.15%** → Bearish momentum
- **Difference < 0.15%** → Neutral / intertwined

The 0.15% threshold prevents false signals from minor crossovers.

### RSI (Relative Strength Index)

Measures momentum by comparing average gains vs average losses over 14 periods:

```
RS  = avg_gain(14) / avg_loss(14)
RSI = 100 − (100 / (1 + RS))
```

| RSI Range | Interpretation | Impact |
|-----------|---------------|--------|
| < 30 | Oversold | → No Trade (hard override) |
| 30–40 | Weak/recovering | Bearish strategies only |
| 40–60 | Neutral | All strategies eligible |
| 60–70 | Strong/trending | Bullish strategies only |
| > 70 | Overbought | → No Trade (hard override) |

### IV Rank

Implied Volatility Rank (0–100) measures where current IV sits relative to its 52-week range:

```
IV Rank = (current IV − 52-week low) / (52-week high − 52-week low) × 100
```

| IV Rank | Strategy preference |
|---------|-------------------|
| ≥ 30 | **Iron Condor** — collect premium on both sides in elevated IV |
| < 30 | **Directional spread** — single-sided spread in low IV environment |

In the synthetic simulation, IV Rank is seeded pseudo-randomly per date (range 15–85).

---

## Position Sizing — 2% Risk Rule

The position sizer enforces that **no single trade risks more than 2% of capital**:

```
max_risk          = capital × 0.02
spread_width      = 2 ticks × lot_size   (width of each spread)
net_credit/lot    = (SELL premium − BUY premium) × lot_size
max_loss/lot      = spread_width − net_credit/lot
lots              = max(1, floor(max_risk / max_loss/lot))
```

**Example** — Nifty Bull Put Spread, capital ₹5,00,000:

```
max_risk           = 5,00,000 × 0.02 = ₹10,000
spread_width       = 2 × 50 × 50    = ₹5,000/lot
net_credit/lot     = (115 − 97) × 50 = ₹900/lot
max_loss/lot       = 5,000 − 900    = ₹4,100/lot
lots               = floor(10,000 / 4,100) = 2

Max profit         = ₹900  × 2 = ₹1,800
Max loss           = ₹4,100 × 2 = ₹8,200
Profit target (55%)              = ₹990
Hard stop (75% of max loss)      = ₹6,150
```

The minimum is always 1 lot — the floor prevents zero-lot edge cases.

---

## Exit Management

### Synthetic Backtest

| Exit Type | Condition | Reason |
|-----------|-----------|--------|
| Profit Target | P&L ≥ 45% of max profit (IC) / 55% (directional) | Lock in gains, avoid giving back premium |
| Hard Stop | P&L ≤ −75% of max loss | Limit catastrophic loss, cut before full width hit |
| End of Day | Candle index 360 (15:15) | Close before last 15 mins of volatile trading |

The 45%/55% profit targets reflect the asymmetric risk/reward:
- Iron Condor has limited upside (symmetric risk on both sides) → exit earlier at 45%
- Directional spreads have more edge when the thesis is correct → hold to 55%

### ORB Paper / Historical

| Exit Type | Condition |
|-----------|-----------|
| EXIT_TARGET | Total MTM ≥ 0.5% of capital |
| EXIT_STOP | Total MTM ≤ −max_loss |
| EXIT_TIME | Candle time ≥ 15:20 |

---

## Greeks Reference

| Greek | What it measures | Relevance |
|-------|-----------------|-----------|
| **Delta** | P&L change per ₹1 move in spot | Short puts: ~−0.28 (ATM−2), long puts: ~−0.12 (ATM−4) |
| **Theta** | Daily time decay (positive for credit spreads) | Main profit engine — credit spread sellers collect theta |
| **Vega** | P&L change per 1% IV change | Negative for credit spreads — hurt by IV expansion |
| **Gamma** | Rate of delta change | Risk increases near expiry for short options |

Credit spread sellers are:
- **Long Theta** (time works in your favour)
- **Short Vega** (IV spike hurts)
- **Short Gamma** (large moves hurt)

This is why IV Rank drives strategy selection — you want to sell premium when IV is elevated (mean-reversion edge) and avoid it when IV is low.

---

## Test Coverage Summary

| Layer | Runner | Files | Scope |
|-------|--------|-------|-------|
| Backend | pytest | `test_simulator.py` | Candle gen, EMA, RSI, IV rank, option pricing, day runner |
| Backend | pytest | `test_strategy.py` | All regime matrix cells, leg builder |
| Backend | pytest | `test_position_sizer.py` | 2% risk rule, minimum lot floor |
| Backend | pytest | `test_router_helpers.py` | `_trading_days`, `_to_dict` helpers |
| Backend | pytest | `test_workbench_services.py` | Catalog list/get, `visual_hints`, `resolve_strategy_identity`, `replay_payload` |
| Backend | pytest | `test_workbench_router.py` | HTTP-level `/api/v2/*` endpoints via ASGI transport |
| Backend | pytest | `test_contract_spec_service.py` | ATM strike rounding (incl. banker's rounding edge cases), leg template expansion |
| Backend | pytest | `test_charges_service.py` | Brokerage math: entry/exit/total charges, STT, GST, monotonicity |
| Backend | pytest | `test_generic_executor.py` | `validate_run` (7 tests) + `execute_run` (6 tests) via async fake DB and service patches |
| Backend | pytest | `test_strategy_replay_serializer.py` | 19 tests: CE/PE MTM grouping, MFE/MAE/drawdown, VIX forward-fill + source tagging, spot OHLC completeness, data quality warnings, legs shape (lots/lot_size), payload regression |
| Backend | pytest | `test_iron_butterfly_*.py` | 8 tests: catalog entry, config-driven wing offsets, 4-leg CE/PE MTM grouping (SELL+BUY per type), mixed BUY/SELL MTM signs, defined-risk margin sizing, mixed-leg entry/exit/round-trip charges, 9-section CSV with 4 contracts |
| Frontend | Vitest | `TopNav.test.jsx` | Primary + legacy nav links, workbench visibility rules, active state |
| Frontend | Vitest | `Backtest.test.jsx` | Form, API call, loading state |
| Frontend | Vitest | `Dashboard.test.jsx` | Data render, navigation, empty/error states |
| Frontend | Vitest | `RunsLibrary.test.jsx` | Table render; checkboxes only on strategy_run rows; export button appears after selection |
| Frontend | Vitest | `RegimeBadge / MetricCard / PnlChart` | Component rendering |
| Frontend | Vitest | `api/index.test.js` | Export contract, base URL, timeout, workbench API functions |

Run backend: `cd backend && python -m pytest tests/ -v`
Run frontend: `cd frontend && npm test`

---

## Deployment

The platform is hosted on **Railway** (free tier):

| Service | Railway type | Key env vars |
|---------|-------------|-------------|
| Backend (FastAPI) | Docker service | `DATABASE_URL` (auto), `PORT` (auto), `SECRET_KEY`, `BROKER_TOKEN_ENCRYPTION_KEY`, `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`, `ALLOWED_ORIGINS`, `ENVIRONMENT=production` |
| Frontend (nginx+React) | Docker service | `VITE_API_URL` (manual → backend URL), `PORT` (auto) |
| Database | PostgreSQL plugin | — auto-wired to backend |

CI/CD is handled by GitHub Actions (`.github/workflows/ci.yml`): tests gate every branch; Railway deployment runs on push to `main` only.

---

## Lot Sizes & Tick Sizes (NSE)

NSE revised lot sizes in November 2024. The engine reads from `instrument_contract_specs` for historical accuracy.

| Index | Period | Lot Size | Strike Step |
|-------|--------|----------|-------------|
| Nifty 50 | up to 2024-11-20 | 50 | 50 |
| Nifty 50 | 2024-11-21 onwards | **75** | 50 |
| Bank Nifty | up to 2024-11-20 | 25 | 100 |
| Bank Nifty | 2024-11-21 onwards | **35** | 100 |

Strike selection always snaps to the nearest valid step:
```python
atm = round(spot / strike_step) * strike_step
```
Python's `round()` uses banker's rounding (round-half-to-even). Test cases must avoid exact midpoints to prevent ambiguous assertions.

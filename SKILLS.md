# SKILLS.md — Options Strategy Reference

Technical reference for the three credit spread strategies implemented in Adaptive Options, plus the regime detection framework.

---

## Strategy Overview

All three strategies are **defined-risk credit spreads** — you collect premium upfront and profit if the position expires worthless or is closed at a profit target. Maximum loss is capped by the long leg.

| Strategy | Regime | Bias | Max Profit | Max Loss |
|----------|--------|------|-----------|---------|
| Iron Condor | Neutral / High IV | Range-bound | Net credit received | Spread width − credit |
| Bull Put Spread | Bullish / Low IV | Directional up | Net credit received | Spread width − credit |
| Bear Call Spread | Bearish / Low IV | Directional down | Net credit received | Spread width − credit |

---

## Iron Condor

### Structure

Combines a **Bull Put Spread** (below the market) and a **Bear Call Spread** (above the market) simultaneously.

```
Profit zone
     │
─────┼──────────────────────────────────────┼─────
     │                                      │
  Long Put      Short Put   Short Call   Long Call
  ATM−5×tick   ATM−3×tick  ATM+3×tick  ATM+5×tick
```

### Legs (Nifty example at ATM 22,000, tick=50)

| Action | Type | Strike | Role |
|--------|------|--------|------|
| BUY | PE | 21,750 (ATM−5) | Protection (long put) |
| SELL | PE | 21,850 (ATM−3) | Premium collection |
| SELL | CE | 22,150 (ATM+3) | Premium collection |
| BUY | CE | 22,250 (ATM+5) | Protection (long call) |

### P&L Profile

```
P&L
 ▲
 │     ┌─────────────────────────┐
 │     │    Max Profit           │
─┼─────┤                         ├──────
 │     │                         │
 │  Loss  21,850   22,150   Loss │
 └─────────────────────────────────────▶ Spot
```

- **Max Profit** = net credit × lots × lot_size
- **Max Loss** = (spread_width − net_credit) × lots × lot_size
- **Break-evens** = short put strike − net_credit/unit, short call strike + net_credit/unit
- **Profit target** = 45% of max profit (conservative — Iron Condor has bounded upside)
- **Best in** = low volatility, range-bound markets (NEUTRAL regime, IV Rank ≥ 30)

### When Selected

- NEUTRAL: EMAs intertwined (diff < 0.15%), RSI 40–60, IV Rank ≥ 30
- BULLISH: EMA5 > EMA20 (≥0.15%), RSI 40–70, IV Rank ≥ 30
- BEARISH: EMA5 < EMA20 (≥0.15%), RSI 30–60, IV Rank ≥ 30

---

## Bull Put Spread

### Structure

A **vertical credit spread** using put options below the current market price. Profits if the index stays above the short put strike.

```
                    ┌──────────── Max Profit (above short put)
                    │
────────────────────┼────────────────────
                    │  Profit zone
  Max Loss zone     │
─────────────────── ATM−4  ATM−2 ────────▶ Spot
   (long put)          (short put)
```

### Legs (Nifty example at ATM 22,000, tick=50)

| Action | Type | Strike | Role |
|--------|------|--------|------|
| SELL | PE | 21,900 (ATM−2) | Premium collection (short put) |
| BUY | PE | 21,800 (ATM−4) | Protection (long put) |

### P&L Profile

- **Max Profit** = net credit (short put premium − long put premium) × lots × lot_size
- **Max Loss** = (spread_width − net_credit) × lots × lot_size
- **Break-even** = short put strike − net_credit/unit
- **Profit target** = 55% of max profit
- **Best in** = bullish or sideways markets with low IV (IV Rank < 30)

### When Selected

- BULLISH: EMA5 > EMA20 (≥0.15%), RSI 40–70, IV Rank < 30

---

## Bear Call Spread

### Structure

A **vertical credit spread** using call options above the current market price. Profits if the index stays below the short call strike.

```
────────────────────────────────────────────────────
  Max Profit (below short call)    │  Max Loss zone
                                   │
           ATM+2    ATM+4          │
        (short call)(long call)    │
────────────────────────────────────────────────────▶ Spot
```

### Legs (Nifty example at ATM 22,000, tick=50)

| Action | Type | Strike | Role |
|--------|------|--------|------|
| SELL | CE | 22,100 (ATM+2) | Premium collection (short call) |
| BUY | CE | 22,200 (ATM+4) | Protection (long call) |

### P&L Profile

- **Max Profit** = net credit × lots × lot_size
- **Max Loss** = (spread_width − net_credit) × lots × lot_size
- **Break-even** = short call strike + net_credit/unit
- **Profit target** = 55% of max profit
- **Best in** = bearish or sideways markets with low IV (IV Rank < 30)

### When Selected

- BEARISH: EMA5 < EMA20 (≥0.15%), RSI 30–60, IV Rank < 30

---

## Regime Detection

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

In this simulation, IV Rank is seeded pseudo-randomly per date (range 15–85).

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

---

## Exit Management

| Exit Type | Condition | Reason |
|-----------|-----------|--------|
| Profit Target | P&L ≥ 45% of max profit (IC) / 55% (directional) | Lock in gains, avoid giving back premium |
| Hard Stop | P&L ≤ −75% of max loss | Limit catastrophic loss, cut before full width hit |
| End of Day | Candle index 360 (15:15) | Close before last 15 mins of volatile trading |

The 45%/55% profit targets reflect the asymmetric risk/reward:
- Iron Condor has limited upside (symmetric risk on both sides) → exit earlier at 45%
- Directional spreads have more edge when the thesis is correct → hold to 55%

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

| Layer | Runner | Count | Scope |
|-------|--------|-------|-------|
| Backend | pytest | 142 | Simulation engine, regime detection, position sizing, router helpers |
| Frontend | Vitest + RTL | 30 | Badge components, MetricCard, TopNav, PnlChart, API module, all 3 pages |

Run backend: `cd backend && python -m pytest tests/ -v`
Run frontend: `cd frontend && npm test`

---

## Deployment

The platform is hosted on **Railway** (free tier):

| Service | Railway type | Key env var |
|---------|-------------|-------------|
| Backend (FastAPI) | Docker service | `DATABASE_URL` (auto), `PORT` (auto) |
| Frontend (nginx+React) | Docker service | `VITE_API_URL` (manual → backend URL), `PORT` (auto) |
| Database | PostgreSQL plugin | — auto-wired to backend |

CI/CD is handled by GitHub Actions (`.github/workflows/ci.yml`): tests gate every branch; Railway deployment runs on push to `main` only.

---

## Lot Sizes & Tick Sizes (NSE)

| Index | Lot Size | Tick Size | ATM strike spacing |
|-------|----------|-----------|-------------------|
| Nifty 50 | 50 | ₹50 | ₹50 per tick |
| Bank Nifty | 25 | ₹100 | ₹100 per tick |

Strike selection always snaps to the nearest valid tick:
```python
atm = round(spot / tick_size) * tick_size
```

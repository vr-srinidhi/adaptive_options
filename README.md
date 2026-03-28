# Adaptive Options — MVP Backtesting Platform

A production-quality options strategy backtesting engine for Nifty 50 and Bank Nifty. Simulates Iron Condor, Bull Put Spread, and Bear Call Spread strategies with auto-regime detection using EMA crossover, RSI, and IV Rank. All results persist in PostgreSQL and are viewable through a dark-theme React dashboard.

> **For educational and backtesting purposes only. Not financial advice.**

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [System Design](#system-design)
- [Backend Deep Dive](#backend-deep-dive)
- [Frontend Deep Dive](#frontend-deep-dive)
- [Simulation Engine](#simulation-engine)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Strategy Logic](#strategy-logic)
- [Configuration](#configuration)
- [Development Setup](#development-setup)
- [Testing](#testing)
- [Railway Cloud Deployment](#railway-cloud-deployment)
- [CI/CD Pipeline](#cicd-pipeline)

---

## Quick Start

```bash
git clone https://github.com/vr-srinidhi/adaptive_options.git
cd adaptive_options
docker compose up -d
```

| Service   | URL                         | Description              |
|-----------|-----------------------------|--------------------------|
| Frontend  | http://localhost:3000       | React dashboard (3 screens) |
| Backend   | http://localhost:8000       | FastAPI REST API         |
| API Docs  | http://localhost:8000/docs  | Auto-generated OpenAPI   |
| Database  | localhost:5432              | PostgreSQL 15            |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Compose                           │
│                                                                 │
│  ┌──────────────────┐     ┌──────────────────┐                 │
│  │   Frontend       │     │    Backend        │                 │
│  │   React 18       │────▶│   FastAPI         │                 │
│  │   Vite           │     │   Python 3.11     │                 │
│  │   TailwindCSS    │     │   Uvicorn         │                 │
│  │   Recharts       │     │                  │                 │
│  │   nginx:80       │     │   port 8000       │                 │
│  │   port 3000      │     └────────┬─────────┘                 │
│  └──────────────────┘              │                            │
│                                    │ asyncpg                    │
│                                    ▼                            │
│                          ┌──────────────────┐                  │
│                          │   PostgreSQL 15   │                  │
│                          │   port 5432       │                  │
│                          │   pgdata volume   │                  │
│                          └──────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## System Design

### Request Flow

```
Browser
  │
  │  GET /backtest  (SPA route)
  ▼
nginx (port 3000)
  │
  ├── /api/*  ──────────────────────▶  FastAPI (port 8000)
  │                                         │
  │                                         │  POST /api/backtest/run
  │                                         ▼
  │                                   Simulation Engine
  │                                         │
  │                                    per trading day:
  │                                    1. generate_candles()
  │                                    2. compute_ema(5), ema(20)
  │                                    3. compute_rsi(14)
  │                                    4. get_iv_rank()
  │                                    5. select_strategy()
  │                                    6. build_legs()
  │                                    7. size_position()
  │                                    8. candle loop → exit
  │                                         │
  │                                         ▼
  │                                   PostgreSQL INSERT
  │                                   backtest_sessions
  │                                         │
  │◀────────────────────────────────────────┘
  │  JSON array of session objects
  ▼
React (redirects to Dashboard)
```

### Data Flow — Single Day Simulation

```
trade_date + instrument + capital
        │
        ▼
  _base_price()
  ┌─────────────────────────┐
  │ Try yfinance (^NSEI /   │
  │ ^NSEBANK) for real spot │
  │ Falls back to seeded    │
  │ synthetic base price    │
  └───────────┬─────────────┘
              │ spot
              ▼
  generate_candles()          ← seeded RNG (deterministic)
  375 × [open, high, low, close]
  volatility: 1.6× first 30, 0.65× mid, 1.4× last 15
              │
              ▼
  compute_ema(closes, 5)
  compute_ema(closes, 20)     ← exponential moving averages
  compute_rsi(closes, 14)     ← Wilder RSI with SMA seed
  get_iv_rank(date, inst)     ← seeded pseudo-random 15–85
              │
              │ at candle index 15 (09:30)
              ▼
  select_strategy(ema5, ema20, rsi, iv_rank)
  → (regime, strategy)        ← per decision matrix §7.3
              │
              ▼
  build_legs(spot, instrument, strategy, vol, remaining)
  → list of {act, typ, strike, delta, ep}
              │
              ▼
  size_position(capital, legs, lot_size, tick_size)
  → (lots, max_profit/lot, max_loss/lot)
              │
              ▼
  candle loop [idx 15 → 360]
  ┌────────────────────────────┐
  │ reprice each leg           │
  │ price_option(spot, strike, │
  │   daily_vol, remaining,    │
  │   opt_type)                │
  │                            │
  │ check PROFIT_TARGET        │
  │ check HARD_EXIT (75%)      │
  │ check END_OF_DAY (15:15)   │
  └────────────────────────────┘
              │
              ▼
  return session dict
  → stored in PostgreSQL
  → returned to client
```

---

## Backend Deep Dive

### Folder Structure

```
backend/
├── Dockerfile
├── requirements.txt
└── app/
    ├── __init__.py
    ├── main.py              # FastAPI app, CORS, startup hook
    ├── database.py          # Async engine, session factory, Base, init_db()
    ├── models/
    │   ├── __init__.py
    │   └── session.py       # BacktestSession SQLAlchemy model
    ├── routers/
    │   ├── __init__.py
    │   └── backtest.py      # All 5 REST endpoints
    └── services/
        ├── __init__.py
        ├── simulator.py     # Candle gen, indicators, option pricing, day runner
        ├── strategy.py      # Regime detection, leg builder
        └── position_sizer.py # 2% capital risk sizing
```

### Key Classes & Functions

| File | Function | Purpose |
|------|----------|---------|
| `simulator.py` | `generate_candles()` | 375 OHLC candles via seeded log-normal RNG |
| `simulator.py` | `compute_ema()` | Standard EMA with multiplier k = 2/(n+1) |
| `simulator.py` | `compute_rsi()` | Wilder RSI, SMA-seeded first 14 periods |
| `simulator.py` | `price_option()` | Simplified BS: intrinsic + time value decay |
| `simulator.py` | `run_day_simulation()` | Orchestrates full per-day backtest |
| `strategy.py` | `select_strategy()` | EMA/RSI/IV regime → strategy mapping |
| `strategy.py` | `build_legs()` | Constructs option leg list with entry prices |
| `position_sizer.py` | `size_position()` | Lots = floor(max_risk / max_loss_per_lot) |
| `routers/backtest.py` | `run_backtest()` | POST /run — validates, simulates, persists |

### Tech Stack — Backend

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | 0.111 |
| Server | Uvicorn | 0.30 |
| ORM | SQLAlchemy (async) | 2.0.30 |
| DB driver | asyncpg | 0.29 |
| Numerics | NumPy | 1.26 |
| Data | pandas | 2.2 |
| Market data | yfinance | 0.2.40 |
| Validation | Pydantic v2 | 2.7 |

---

## Frontend Deep Dive

### Folder Structure

```
frontend/
├── Dockerfile               # node:20-alpine build → nginx:1.25-alpine serve
├── nginx.conf               # SPA fallback + /api proxy to backend:8000
├── package.json
├── vite.config.js           # Dev proxy → localhost:8000
├── tailwind.config.js       # Dark theme, JetBrains Mono font
├── postcss.config.js
├── index.html
└── src/
    ├── main.jsx             # React root
    ├── App.jsx              # BrowserRouter, Routes, Footer
    ├── index.css            # CSS variables, scrollbar, animations
    ├── api/
    │   └── index.js         # axios wrappers for all 5 endpoints
    ├── components/
    │   ├── TopNav.jsx       # 48px nav bar, active-tab underline
    │   ├── MetricCard.jsx   # Reusable stat card (label / value / subtext)
    │   ├── RegimeBadge.jsx  # RegimeBadge, WLBadge, ActionBadge, TypeBadge
    │   └── PnlChart.jsx     # PnlProgressionChart + CumulativePnlChart (Recharts)
    └── pages/
        ├── Backtest.jsx     # Screen 1 — config form + strategy info panel
        ├── Dashboard.jsx    # Screen 2 — metrics, chart, results table
        └── TradeBook.jsx    # Screen 3 — market state, legs, 1-min chart
```

### Screen Map

```
/backtest  ──────────────────────────────────────────────────────
  ┌──────────────────────────────────────────────────────────┐
  │  INSTRUMENT  ▾  |  CAPITAL (₹)                          │
  │  START DATE     |  END DATE                             │
  │  [ Run Backtest ]  [ View Dashboard → ]                 │
  ├──────────────────────────────────────────────────────────┤
  │  STRATEGY AUTO-SELECTION LOGIC                          │
  │  [BULLISH]        [BEARISH]        [NEUTRAL]            │
  └──────────────────────────────────────────────────────────┘

/dashboard  ─────────────────────────────────────────────────────
  ┌──────────┬──────────┬──────────┬──────────┐
  │ TOTAL P&L│ WIN RATE │ BEST DAY │ WORST DAY│
  └──────────┴──────────┴──────────┴──────────┘
  ┌──────────────────────────────────────────────────────────┐
  │  CUMULATIVE P&L  (AreaChart, Recharts)                  │
  └──────────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────────┐
  │  Date │ Inst │ Regime │ Strategy │ Lots │ P&L │ Result  │
  │  ...clickable rows → /tradebook/:id                     │
  └──────────────────────────────────────────────────────────┘

/tradebook/:id  ─────────────────────────────────────────────────
  ┌─────────────────────────┬────────────────────────────────┐
  │  MARKET STATE AT ENTRY  │  POSITION SUMMARY              │
  │  EMA5 / EMA20 / RSI14  │  Lots / Capital / Max P&L      │
  │  IV Rank / Spot In/Out  │  Max Loss / Entry / Exit       │
  ├─────────────────────────┴────────────────────────────────┤
  │  OPTION LEGS (TRADE BOOK)                                │
  │  Action│Type│Strike│Delta│Entry Px│Exit Px│Lots│Leg P&L │
  ├──────────────────────────────────────────────────────────┤
  │  P&L PROGRESSION  (AreaChart, 1-min intervals)           │
  └──────────────────────────────────────────────────────────┘
```

### Tech Stack — Frontend

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | React | 18.3 |
| Build | Vite | 5.3 |
| Styling | TailwindCSS | 3.4 |
| Charts | Recharts | 2.12 |
| Routing | React Router | 6.24 |
| HTTP | axios | 1.7 |
| Server | nginx | 1.25-alpine |

---

## Simulation Engine

### Candle Generation (PRD §7.1)

- **375 candles/day** — 09:15 to 15:29 inclusive (1 candle per minute)
- **Deterministic** — seeded via `MD5(date_str + instrument)`, same inputs always produce same candles
- **Base price** — tries yfinance (`^NSEI` / `^NSEBANK`) for real historical close; falls back to synthetic drift from instrument base price
- **Volatility model** — log-normal daily vol clipped to 0.7%–2.0%
  - Opening (first 30 candles): `1.6×` vol multiplier
  - Mid-session: `0.65×` vol multiplier
  - Close (last 15 candles): `1.4×` vol multiplier

### Indicator Computation (PRD §7.2)

| Indicator | Method |
|-----------|--------|
| EMA(5) | Standard EMA, k = 2/6 |
| EMA(20) | Standard EMA, k = 2/21 |
| RSI(14) | Wilder smoothing, SMA seed for first 14 periods |
| IV Rank | Seeded pseudo-random per date, range 15–85 |

Regime assessed at **candle index 15 = 09:30** (15 min after market open).

### Regime Detection Matrix (PRD §7.3)

| EMA State | RSI Range | IV Rank | Strategy |
|-----------|-----------|---------|----------|
| EMA5 > EMA20 (≥0.15%) | 40–70 | ≥ 30 | Iron Condor |
| EMA5 > EMA20 (≥0.15%) | 40–70 | < 30 | Bull Put Spread |
| EMA5 < EMA20 (≥0.15%) | 30–60 | ≥ 30 | Iron Condor |
| EMA5 < EMA20 (≥0.15%) | 30–60 | < 30 | Bear Call Spread |
| Intertwined (< 0.15%) | 40–60 | ≥ 30 | Iron Condor |
| Intertwined (< 0.15%) | 40–60 | < 30 | No Trade |
| Any | > 70 or < 30 | Any | No Trade |

### Strike Selection (PRD §7.4)

```
ATM = round(spot / tick_size) × tick_size
```

| Strategy | Legs |
|----------|------|
| Iron Condor | SELL Call ATM+3, BUY Call ATM+5, SELL Put ATM−3, BUY Put ATM−5 |
| Bull Put Spread | SELL Put ATM−2, BUY Put ATM−4 |
| Bear Call Spread | SELL Call ATM+2, BUY Call ATM+4 |

Tick sizes: Nifty = 50, Bank Nifty = 100. Lot sizes: Nifty = 50, Bank Nifty = 25.

### Option Pricing (PRD §7.5)

Simplified Black-Scholes approximation:

```
intrinsic  = max(spot − strike, 0)          # for CE
           = max(strike − spot, 0)          # for PE

annual_vol = daily_vol × √252
T          = remaining_minutes / (375 × 252)
d          = |spot − strike| / spot

time_value = spot × annual_vol × √T × 0.45 × exp(−d / (annual_vol × 0.25))

price      = max(intrinsic + time_value, 0.50)
```

### Position Sizing (PRD §7.6)

```
max_risk          = capital × 0.02
spread_width      = 2 × tick_size × lot_size
net_credit/lot    = Σ(SELL premiums − BUY premiums) × lot_size
max_loss/lot      = spread_width − net_credit/lot
lots              = max(1, floor(max_risk / max_loss/lot))
```

### Exit Conditions (PRD §7.7)

Checked every candle from 09:30 (index 15) to 15:15 (index 360):

| Condition | Trigger |
|-----------|---------|
| `PROFIT_TARGET` | cumulative P&L ≥ max_profit × 0.45 (Iron Condor) or × 0.55 (directional) |
| `HARD_EXIT` | cumulative P&L ≤ −max_loss × 0.75 |
| `END_OF_DAY` | candle index reaches 360 (15:15) |

---

## Database Schema

### Table: `backtest_sessions`

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | Auto-generated primary key |
| `instrument` | VARCHAR(20) | NIFTY or BANKNIFTY |
| `session_date` | DATE | Trading date |
| `capital` | NUMERIC(12,2) | Capital used in run |
| `regime` | VARCHAR(20) | BULLISH / BEARISH / NEUTRAL |
| `iv_rank` | INTEGER | Simulated IV rank 15–85 |
| `strategy` | VARCHAR(30) | IRON_CONDOR / BULL_PUT_SPREAD / BEAR_CALL_SPREAD / NO_TRADE |
| `entry_time` | TIME | 09:30 in MVP |
| `exit_time` | TIME | Time of exit candle |
| `exit_reason` | VARCHAR(30) | PROFIT_TARGET / HARD_EXIT / END_OF_DAY / NO_SIGNAL |
| `spot_in` | NUMERIC(10,2) | Spot price at entry |
| `spot_out` | NUMERIC(10,2) | Spot price at exit |
| `lots` | INTEGER | Number of lots traded |
| `max_profit` | NUMERIC(10,2) | Maximum possible profit |
| `max_loss` | NUMERIC(10,2) | Maximum possible loss |
| `pnl` | NUMERIC(10,2) | Realised P&L |
| `pnl_pct` | NUMERIC(6,4) | P&L as % of capital |
| `wl` | VARCHAR(15) | WIN / LOSS / BREAK_EVEN / NO_TRADE |
| `ema5` | NUMERIC(10,2) | EMA(5) at entry |
| `ema20` | NUMERIC(10,2) | EMA(20) at entry |
| `rsi14` | NUMERIC(6,2) | RSI(14) at entry |
| `legs` | JSONB | Array of leg objects (see below) |
| `min_data` | JSONB | Array of 1-min {time, spot, pnl} |
| `created_at` | TIMESTAMPTZ | Auto-set on insert |

### JSONB: `legs[]`

```json
{
  "id": 1,
  "act": "SELL",
  "typ": "CE",
  "strike": 22250,
  "delta": 0.17,
  "ep": 112.26,
  "ep2": 63.16,
  "legPnl": 4910.0,
  "lots": 2
}
```

### JSONB: `min_data[]`

```json
{ "time": "09:30", "spot": 22094.29, "pnl": -240.5 }
```

---

## API Reference

Base URL: `http://localhost:8000/api`

### `POST /backtest/run`

Run a backtest over a date range. Simulates every trading day and persists to DB.

**Request body:**
```json
{
  "instrument": "NIFTY",
  "startDate": "2025-01-06",
  "endDate": "2025-01-31",
  "capital": 500000
}
```

**Validation:**
- `instrument`: `NIFTY` or `BANKNIFTY`
- `capital`: ₹50,000 – ₹10,000,000
- Date range: ≥ 1 and ≤ 60 trading days (weekdays only)

**Response:** Array of session objects (full, including `legs` and `min_data`).

---

### `GET /backtest/results`

Paginated list of all sessions, newest first.

**Query params:** `instrument`, `limit` (default 200), `offset` (default 0)

**Response:** Array of session summaries (no `min_data` for performance).

---

### `GET /backtest/results/:id`

Full session detail for a single day.

**Response:** Complete session object including `legs[]` and `min_data[]`.

---

### `GET /backtest/summary`

Aggregated statistics across all sessions.

**Query params:** `instrument`

**Response:**
```json
{
  "totalPnl": 1513.15,
  "winRate": 83,
  "totalTrades": 6,
  "totalSessions": 20,
  "bestDay": { ...session },
  "worstDay": { ...session }
}
```

---

### `DELETE /backtest/results`

Clears all sessions from the database.

**Response:** `{ "deleted": 20 }`

---

## Configuration

### Docker Compose Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@db:5432/adaptive_options` | Full async DB connection string |
| `POSTGRES_DB` | `adaptive_options` | Database name |
| `POSTGRES_USER` | `postgres` | DB user |
| `POSTGRES_PASSWORD` | `postgres` | DB password |

### Instrument Constants

| Instrument | Ticker (yfinance) | Base Price | Tick Size | Lot Size |
|------------|-------------------|------------|-----------|----------|
| NIFTY | `^NSEI` | ₹22,000 | 50 | 50 |
| BANKNIFTY | `^NSEBANK` | ₹48,000 | 100 | 25 |

---

## Development Setup

### Prerequisites

- Docker Desktop 4.x+
- (Optional for local dev) Python 3.11+, Node 20+, PostgreSQL 15

### Run with Docker (recommended)

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f backend
docker compose logs -f frontend

# Rebuild after code changes
docker compose build && docker compose up -d

# Stop
docker compose down

# Stop and remove data volume
docker compose down -v
```

### Local Backend (without Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/adaptive_options"
uvicorn app.main:app --reload --port 8000
```

### Local Frontend (without Docker)

```bash
cd frontend
npm install
npm run dev          # Vite dev server on :5173, proxies /api → localhost:8000
```

### API Explorer

FastAPI auto-generates interactive docs:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## Testing

### Backend (pytest)

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt
python -m pytest tests/ -v
```

| File | Tests | Coverage |
|------|-------|---------|
| `tests/test_simulator.py` | 56 | `_seed`, `generate_candles`, `compute_ema`, `compute_rsi`, `get_iv_rank`, `price_option`, `_idx_to_time`, `run_day_simulation` |
| `tests/test_strategy.py` | 40 | `select_strategy` (all PRD §7.3 matrix cells + RSI/EMA boundaries), `build_legs` |
| `tests/test_position_sizer.py` | 16 | `size_position` (PRD §7.6 worked example, 2% rule, minimum-1-lot floor) |
| `tests/test_router_helpers.py` | 30 | `_trading_days`, `_to_dict` (full vs summary mode, None-field handling) |

### Frontend (Vitest + React Testing Library)

```bash
cd frontend
npm ci
npm test          # run once
npm run test:watch  # watch mode
```

| File | Tests | Coverage |
|------|-------|---------|
| `RegimeBadge.test.jsx` | 2 | All 4 badge components, fallback colours |
| `MetricCard.test.jsx` | 2 | Render, click handler, optional props |
| `TopNav.test.jsx` | 8 | Brand, nav links, active state, extra-link guard |
| `PnlChart.test.jsx` | 3 | Empty guard, Y-axis domain, cumulative sort |
| `api/index.test.js` | 6 | Exports, `VITE_API_URL` fallback, timeout contract |
| `Backtest.test.jsx` | 2 | Successful run flow, API error state |
| `Dashboard.test.jsx` | 4 | Data render, navigation, empty state, API error |
| `TradeBook.test.jsx` | 3 | Full session render, empty legs/min_data, API error |

---

## Railway Cloud Deployment

The app is deployed on [Railway](https://railway.app) as three services: **backend**, **frontend**, and a managed **PostgreSQL** plugin.

### Architecture on Railway

```
Internet
   │
   ├─▶ Frontend service  (nginx, public URL)
   │     │  VITE_API_URL points to backend public URL
   │     │  (no nginx proxy needed in cloud)
   │
   └─▶ Backend service   (FastAPI, public URL)
         │  DATABASE_URL  injected by Railway PostgreSQL plugin
         ▼
       PostgreSQL plugin  (managed, private)
```

### One-time Setup

1. **Create a Railway project** at [railway.app](https://railway.app)

2. **Add PostgreSQL plugin** — Railway auto-sets `DATABASE_URL` on the backend service.

3. **Create the Backend service**
   - Source: this GitHub repo, root directory `backend/`
   - Railway picks up `backend/railway.toml` automatically
   - No extra env vars needed (Railway injects `PORT` and `DATABASE_URL`)

4. **Create the Frontend service**
   - Source: this GitHub repo, root directory `frontend/`
   - Railway picks up `frontend/railway.toml` automatically
   - Set one environment variable:
     ```
     VITE_API_URL = https://<your-backend-service>.up.railway.app
     ```
   - Railway injects `PORT` so nginx listens on the correct port

5. **Test the deployment**
   ```bash
   curl https://<backend-service>.up.railway.app/health
   # → {"status":"ok"}
   ```

### Environment Variables Reference

| Service | Variable | Where set | Description |
|---------|----------|-----------|-------------|
| Backend | `DATABASE_URL` | Auto (Railway plugin) | `postgresql://...` — scheme is normalised to `postgresql+asyncpg://` automatically |
| Backend | `PORT` | Auto (Railway) | Uvicorn binds to this port |
| Frontend | `VITE_API_URL` | Manual (Railway dashboard) | Backend public URL, baked into JS bundle at build time |
| Frontend | `PORT` | Auto (Railway) | nginx listens on this port via `envsubst` |

### Local vs Railway Behaviour

| Aspect | Local Docker Compose | Railway |
|--------|---------------------|---------|
| API calls | nginx proxies `/api/` → `backend:8000` | React calls `VITE_API_URL` directly |
| DB URL scheme | `postgresql+asyncpg://` (explicit) | `postgresql://` (converted automatically) |
| Port binding | Hardcoded 8000 / 80 | `$PORT` (injected per service) |

---

## CI/CD Pipeline

The GitHub Actions workflow at `.github/workflows/ci.yml` runs on every push and PR.

### Pipeline Stages

```
push / PR
    │
    ├─▶ backend-tests   (pytest, Python 3.11)
    │
    └─▶ frontend-tests  (vitest, Node 20)
            │
            │  only on push to main ↓
            ▼
    deploy-backend  (railway up → backend service)
            │
            ▼
    deploy-frontend (railway up → frontend service)
```

### Setting Up Secrets and Variables

In your GitHub repository → **Settings → Secrets and variables → Actions**:

| Type | Name | Value |
|------|------|-------|
| Secret | `RAILWAY_TOKEN` | Railway personal token (Account → API Tokens) |
| Variable | `RAILWAY_PROJECT_ID` | Found in Railway project settings |
| Variable | `RAILWAY_BACKEND_SERVICE_ID` | Railway backend service ID |
| Variable | `RAILWAY_FRONTEND_SERVICE_ID` | Railway frontend service ID |

### What Triggers What

| Event | Backend Tests | Frontend Tests | Deploy |
|-------|--------------|----------------|--------|
| Push to any branch | ✅ | ✅ | ❌ |
| PR to any branch | ✅ | ✅ | ❌ |
| Push to `main` | ✅ | ✅ | ✅ |

Deploy jobs are sequential (backend first, then frontend) so the backend URL is always live before the frontend is rebuilt.

---

## Out of Scope (Post-MVP)

- Live broker integration (Zerodha Kite, IBKR)
- Real-time WebSocket market data
- Trade execution wizard and order placement
- Push notifications and adjustment alerts
- Multi-user authentication
- Export to CSV / PDF
- Real NSE options chain data (currently uses synthetic pricing)

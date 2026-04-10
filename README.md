# Adaptive Options — Backtesting + Paper Trading Platform

A production-quality options strategy platform for Nifty 50 and Bank Nifty with two independent modules:

1. **Synthetic Backtest** — simulates Iron Condor, Bull Put Spread, and Bear Call Spread strategies using deterministic synthetic candle data with auto-regime detection (EMA/RSI/IV Rank).
2. **Paper Trading ORB Replay** — replays any historical trading day using **live Zerodha market data**, evaluates an Opening Range Breakout strategy through a seven-gate decision engine, and produces full per-minute audit logs, trade detail, and raw candle exports.

> **For educational and research purposes only. Not financial advice. No live order placement.**

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Paper Trading Module](#paper-trading-module)
- [Synthetic Backtest Module](#synthetic-backtest-module)
- [Backend Deep Dive](#backend-deep-dive)
- [Frontend Deep Dive](#frontend-deep-dive)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
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
cp .env.example .env          # add your Zerodha API key + secret
docker compose up -d
```

| Service   | URL                         | Description                      |
|-----------|-----------------------------|----------------------------------|
| Frontend  | http://localhost:3000       | React dashboard (6 screens)      |
| Backend   | http://localhost:8000       | FastAPI REST API                 |
| API Docs  | http://localhost:8000/docs  | Auto-generated OpenAPI (Swagger) |
| Database  | localhost:5432              | PostgreSQL 15                    |

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
│  │   Recharts       │     │                   │                 │
│  │   nginx:80       │     │   port 8000       │                 │
│  │   port 3000      │     └────────┬──────────┘                │
│  └──────────────────┘              │                            │
│                                    │ asyncpg                    │
│                                    ▼                            │
│                          ┌──────────────────┐                  │
│                          │   PostgreSQL 15   │                  │
│                          │   port 5432       │                  │
│                          └──────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
                                    │
                      (paper trading only)
                                    │ HTTPS
                                    ▼
                          Zerodha Kite API
                          Historical candle data
```

---

## Paper Trading Module

### What it does

The ORB (Opening Range Breakout) replay engine fetches real Zerodha 1-minute candle data for any past trading day and simulates whether the strategy would have entered a trade, and how it would have performed.

```
trade_date + instrument + capital + access_token
        │
        ▼
  Fetch spot candles (Zerodha historical API)
        │
        ▼
  Compute Opening Range  (OR high/low from first 15 candles: 09:15–09:29)
        │
        ▼
  Fetch instruments master → expiry + lot size
        │
        ▼
  Pre-fetch ALL candidate option series
  (5 Bull Call Spread + 5 Bear Put Spread candidates, deduplicated)
        │
        ▼
  Minute-by-minute replay  (09:15 → 15:29)
  ┌──────────────────────────────────────────────┐
  │  No open trade → run G1–G7 gate stack        │
  │  Open trade    → run exit engine             │
  └──────────────────────────────────────────────┘
        │
        ▼
  Persist: decisions + trade header/legs/marks + candle series
```

### G1–G7 Gate Stack

Each minute after OR closes, gates evaluate in sequence. The first failing gate short-circuits evaluation.

| Gate | Rule | Reason Code on Fail |
|------|------|---------------------|
| G1 | Opening range window complete (15 candles) | `OPENING_RANGE_NOT_READY` |
| G2 | No active trade open | `ACTIVE_TRADE_EXISTS` |
| G3 | Close breaks OR boundary by 0.1% | `NO_BREAKOUT_CONFIRMATION` |
| G4 | Previous candle also confirmed the same breakout | `FAILED_BREAKOUT_OR_NO_FOLLOWTHROUGH` |
| G5 | Both spread legs have valid market prices | `NO_HEDGE_AVAILABLE` |
| G6 | Max loss ≤ 2% of capital | `RISK_EXCEEDS_CAP` |
| G7 | Max possible gain ≥ session target (0.5% capital) | `TARGET_NOT_VIABLE` |

On pass → `ENTER_TRADE`. Strategy: **Bull Call Spread** (bullish) or **Bear Put Spread** (bearish).

### Candidate Spread Selection

Five strike pairs are tried per direction, ATM-first (offsets 0, ±1, ±2 × 50 pts):

```
Bull Call: [(base, base+50), (base-50, base), (base+50, base+100), ...]
Bear Put:  [(base, base-50), (base+50, base), (base-50, base-100), ...]
```

### Exit Conditions

| Condition | Trigger |
|-----------|---------|
| `EXIT_TARGET` | Total MTM ≥ 0.5% of capital |
| `EXIT_STOP` | Total MTM ≤ −max_loss |
| `EXIT_TIME` | Candle time reaches 15:15 |

### Round-trip Charges

```
brokerage = 4 × ₹20
STT       = 0.05%  × sell-side premium × qty
exchange  = 0.053% × total premium turnover × qty
GST       = 18%    × (brokerage + exchange)
net_pnl   = gross_pnl − total_charges
```

### Zerodha Access Token Flow

Tokens expire daily at 6 AM IST.

```bash
# 1. Get login URL
GET /api/auth/zerodha/login-url

# 2. Open URL → complete Zerodha login → copy request_token from redirect URL

# 3. Exchange for access token
POST /api/auth/zerodha/session
Body: {"request_token": "<token>"}
Returns: {"access_token": "..."}
```

### Session Detail Page (`/paper/session/:id`)

- Session summary (date, capital, status, minutes audited)
- **Trade detail** — contract breakdown table: BUY/SELL badge, full contract name (`NIFTY 22900 CE exp 13 Apr 2026`), lots, lot size, total qty, entry/exit price
- MTM progression chart (while trade was open)
- **Raw candle data** — scrollable OHLCV tables for SPOT + weekly and monthly option legs
- Minute audit log with action filter tabs; ENTER rows show inline contract details
- **↓ CSV** — full dataset including candle series sections
- **↓ PDF** — browser print with print-optimised stylesheet

---

## Synthetic Backtest Module

Simulates NSE options strategies over a date range using deterministic synthetic candle data. Same inputs always produce identical results (seeded RNG via `MD5(date + instrument)`).

### Regime Detection Matrix

| EMA State | RSI Range | IV Rank | Strategy |
|-----------|-----------|---------|----------|
| EMA5 > EMA20 (≥0.15%) | 40–70 | ≥ 30 | Iron Condor |
| EMA5 > EMA20 (≥0.15%) | 40–70 | < 30 | Bull Put Spread |
| EMA5 < EMA20 (≥0.15%) | 30–60 | ≥ 30 | Iron Condor |
| EMA5 < EMA20 (≥0.15%) | 30–60 | < 30 | Bear Call Spread |
| Intertwined (< 0.15%) | 40–60 | ≥ 30 | Iron Condor |
| Intertwined (< 0.15%) | 40–60 | < 30 | No Trade |
| Any | > 70 or < 30 | Any | No Trade |

### Exit Conditions

| Condition | Trigger |
|-----------|---------|
| `PROFIT_TARGET` | P&L ≥ max_profit × 0.45 (IC) or × 0.55 (directional) |
| `HARD_EXIT` | P&L ≤ −max_loss × 0.75 |
| `END_OF_DAY` | Candle index 360 (15:15) |

---

## Backend Deep Dive

### Folder Structure

```
backend/
├── Dockerfile
├── requirements.txt
└── app/
    ├── main.py              # FastAPI app, CORS, startup hook
    ├── database.py          # Async engine, session factory, Base, init_db()
    ├── models/
    │   ├── session.py       # BacktestSession model
    │   └── paper_trade.py   # 6 paper trading ORM models
    ├── routers/
    │   ├── backtest.py      # Synthetic backtest endpoints
    │   ├── paper_trading.py # Paper trading endpoints
    │   └── auth.py          # Zerodha OAuth
    └── services/
        ├── simulator.py       # Candle gen, indicators, option pricing, day runner
        ├── strategy.py        # Regime detection, leg builder
        ├── position_sizer.py  # 2% capital risk sizing
        ├── paper_engine.py    # ORB replay orchestrator
        ├── entry_gates.py     # G1–G7 gate evaluator
        ├── exit_engine.py     # MTM exit conditions
        ├── opening_range.py   # OR computation + candidate spread generators
        ├── option_resolver.py # Zerodha instrument token lookup
        ├── zerodha_client.py  # Zerodha API wrappers
        └── calendar.py        # NSE trading calendar helpers
```

### Tech Stack — Backend

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | 0.111 |
| Server | Uvicorn | 0.30 |
| ORM | SQLAlchemy (async) | 2.0.30 |
| DB driver | asyncpg | 0.29 |
| Broker API | kiteconnect | 5.x |
| Validation | Pydantic v2 | 2.7 |

---

## Frontend Deep Dive

### Screen Map

```
/backtest       Run synthetic backtest (instrument, capital, date range)
/dashboard      Metrics + cumulative P&L chart + results table
/tradebook/:id  Synthetic backtest day drill-down (legs, 1-min chart)
/paper          ORB replay launcher (instrument, capital, date, access token)
/paper/sessions Session list with status and decision counts
/paper/session/:id  Full session detail: audit log, trade, candles, CSV/PDF
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

## Database Schema

Schema auto-created at startup via `create_all`. No Alembic migrations.

### `backtest_sessions`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `instrument` | VARCHAR(20) | NIFTY / BANKNIFTY |
| `session_date` | DATE | |
| `capital` | NUMERIC(12,2) | |
| `strategy` | VARCHAR(30) | IRON_CONDOR / BULL_PUT_SPREAD / BEAR_CALL_SPREAD / NO_TRADE |
| `pnl` | NUMERIC(10,2) | |
| `legs` | JSONB | `[{act, typ, strike, delta, ep, ep2, legPnl, lots}]` |
| `min_data` | JSONB | `[{time, spot, pnl}]` per minute |

### Paper Trading Tables (6 tables)

| Table | Description |
|-------|-------------|
| `paper_sessions` | One row per replay run (status, decision_count) |
| `strategy_minute_decisions` | Full G1–G7 audit: action, reason_code, reason_text, candidate_structure JSONB |
| `paper_trade_headers` | Trade entry/exit, bias, strikes, lot_size, gross/net P&L, exit_reason |
| `paper_trade_legs` | LONG + SHORT legs with entry/exit prices |
| `paper_trade_minute_marks` | Per-minute MTM: spread value, mtm_per_lot, total_mtm, distance_to_target/stop |
| `paper_candle_series` | Raw OHLCV candles: `series_type` (SPOT / `{strike}_{type}_WEEKLY` / `_MONTHLY`) + `candles` JSONB |

---

## API Reference

Base URL: `http://localhost:8000/api`

### Zerodha Auth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/zerodha/login-url` | Returns Zerodha OAuth URL |
| POST | `/auth/zerodha/session` | Exchanges `request_token` → `access_token` |

### Paper Trading

| Method | Path | Description |
|--------|------|-------------|
| POST | `/paper/session/run` | Replay one day; bulk-insert all results |
| GET | `/paper/sessions` | List sessions (`instrument`, `limit` params) |
| GET | `/paper/session/{id}` | Session detail + action summary |
| GET | `/paper/session/{id}/decisions` | Minute audit log (`action`, `limit`, `offset` params) |
| GET | `/paper/session/{id}/trade` | Trade header + legs |
| GET | `/paper/session/{id}/trade/marks` | Per-minute MTM array |
| GET | `/paper/session/{id}/candles` | Raw OHLCV candle series |

**POST `/paper/session/run` body:**
```json
{
  "instrument": "NIFTY",
  "date": "2026-04-07",
  "capital": 2500000,
  "access_token": "<zerodha_token>"
}
```

### Synthetic Backtest

| Method | Path | Description |
|--------|------|-------------|
| POST | `/backtest/run` | Run simulation over date range |
| GET | `/backtest/results` | Paginated session list |
| GET | `/backtest/results/:id` | Full session (legs + min_data) |
| GET | `/backtest/summary` | Aggregated stats |
| DELETE | `/backtest/results` | Clear all sessions |

---

## Configuration

Copy `.env.example` to `.env`:

```
ZERODHA_API_KEY=your_api_key
ZERODHA_API_SECRET=your_api_secret
```

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@db:5432/adaptive_options` | Async DB URL |
| `ZERODHA_API_KEY` | — | Required for paper trading |
| `ZERODHA_API_SECRET` | — | Required for token exchange |
| `POSTGRES_DB` | `adaptive_options` | |
| `POSTGRES_USER` | `postgres` | |
| `POSTGRES_PASSWORD` | `postgres` | |

---

## Development Setup

### Prerequisites

- Docker Desktop 4.x+
- Zerodha Kite Connect API credentials (for paper trading)
- (Optional) Python 3.11+, Node 20+, PostgreSQL 15

### Docker (recommended)

```bash
cp .env.example .env
docker compose up -d
docker compose logs -f backend
docker compose build backend && docker compose up -d backend   # after Python changes
docker compose build frontend && docker compose up -d frontend # after JS changes
docker compose down -v   # stop + wipe DB
```

### Local Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/adaptive_options"
export ZERODHA_API_KEY="..." ZERODHA_API_SECRET="..."
uvicorn app.main:app --reload --port 8000
```

### Local Frontend

```bash
cd frontend && npm install && npm run dev   # :5173, proxies /api → localhost:8000
```

---

## Testing

### Backend (pytest)

```bash
cd backend && python -m pytest tests/ -v
```

| File | What's tested |
|------|--------------|
| `test_simulator.py` | Candle gen, EMA, RSI, IV rank, option pricing, day runner |
| `test_strategy.py` | All regime matrix cells, leg builder |
| `test_position_sizer.py` | 2% risk rule, minimum lot floor |
| `test_router_helpers.py` | `_trading_days`, `_to_dict` helpers |

### Frontend (Vitest)

```bash
cd frontend && npm test
```

| File | What's tested |
|------|--------------|
| `TopNav.test.jsx` | 4-link nav (Run, Dashboard, Replay, Sessions), active state |
| `Backtest.test.jsx` | Single-date form, API call, loading state |
| `Dashboard.test.jsx` | Data render, navigation, empty/error states |
| `RegimeBadge / MetricCard / PnlChart` | Component rendering |
| `api/index.test.js` | Export contract, base URL, timeout |

---

## Railway Cloud Deployment

### Architecture

```
Internet
   ├─▶ Frontend service  (nginx, public URL)
   │     VITE_API_URL → backend public URL
   └─▶ Backend service   (FastAPI, public URL)
         DATABASE_URL  ← Railway PostgreSQL plugin
         ZERODHA_API_KEY / SECRET ← set manually
         ▼
       PostgreSQL plugin  (managed, private)
```

### One-time Setup

1. Create a Railway project
2. Add PostgreSQL plugin — auto-sets `DATABASE_URL` on backend
3. Create **Backend** service — root dir `backend/`, set `ZERODHA_API_KEY` + `ZERODHA_API_SECRET`
4. Create **Frontend** service — root dir `frontend/`, set `VITE_API_URL = https://<backend>.up.railway.app`
5. Verify: `curl https://<backend>.up.railway.app/health` → `{"status":"ok"}`

### GitHub Secrets / Variables

| Type | Name | Value |
|------|------|-------|
| Secret | `RAILWAY_TOKEN` | Railway personal token |
| Variable | `RAILWAY_PROJECT_ID` | From Railway project settings |
| Variable | `RAILWAY_BACKEND_SERVICE_ID` | Railway backend service ID |
| Variable | `RAILWAY_FRONTEND_SERVICE_ID` | Railway frontend service ID |

---

## CI/CD Pipeline

```
push / PR
    ├─▶ backend-tests   (pytest, Python 3.11)
    └─▶ frontend-tests  (vitest, Node 20)
            │
            │  push to main only
            ▼
    deploy-backend  (railway up)
            │
            ▼
    deploy-frontend (railway up)
```

Deploy jobs run sequentially — backend first, then frontend — so the API is always live before the JS bundle is rebuilt.

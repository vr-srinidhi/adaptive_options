# Adaptive Options — Options Strategy Platform

A production-quality options strategy platform for Nifty 50 and Bank Nifty with four independent modules:

1. **V2 Workbench** — strategy-agnostic shell for running, replaying, and comparing any supported ORB strategy. Primary UI entry point.
2. **Synthetic Backtest** — simulates Iron Condor, Bull Put Spread, and Bear Call Spread strategies using deterministic synthetic candle data with auto-regime detection (EMA/RSI/IV Rank).
3. **Historical Backtest** — batch-runs any registered strategy over real Zerodha candle data stored in a local warehouse. Supports multi-day runs with full per-session audit trails.
4. **Paper Trading ORB Replay** — replays any historical trading day using **live Zerodha market data**, evaluates an Opening Range Breakout strategy through a seven-gate decision engine, and produces full per-minute audit logs, trade detail, and raw candle exports.

> **For educational and research purposes only. Not financial advice. No live order placement.**

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [V2 Workbench Module](#v2-workbench-module)
- [Historical Backtest Module](#historical-backtest-module)
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
| Frontend  | http://localhost:3000       | React SPA — defaults to Workbench |
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
                      (paper trading + historical only)
                                    │ HTTPS
                                    ▼
                          Zerodha Kite API
                          Historical candle data
```

---

## V2 Workbench Module

The workbench is a unified, strategy-agnostic shell that guides users through a four-step workflow for any supported ORB strategy.

```
Strategy Catalog → Run Builder → [Run] → Replay Analyzer
                                              │
                                         Runs Library
```

### Workflow

1. **Strategy Catalog** (`/workbench`) — browse all registered strategies (available, planned, research). Cards show badge, assumption, payoff hint, and leg diagram.
2. **Run Builder** (`/workbench/strategy/:id`) — configure instrument, capital, and date range. Validates historical data availability before enabling run submission.
3. **Replay Analyzer** (`/workbench/replay/:kind/:id`) — per-session charts, minute audit log, trade detail, and explainability block.
4. **Runs Library** (`/workbench/runs`) — all saved runs across all strategies and run types, with filtering and pagination.

### Strategy States

| State | Description |
|-------|-------------|
| `available` | Fully implemented; can run now |
| `planned` | On roadmap; UI shows "coming soon" |
| `research` | Under investigation; shown in catalog but not runnable |

Currently available: **orb_intraday_spread** (Opening Range Spread).

### Run Types

| Kind | Source | Notes |
|------|--------|-------|
| `paper_session` | Live Zerodha API → single-day replay | Requires Zerodha access token |
| `historical_batch` | Warehoused candle data → multi-day batch | Requires pre-ingested data |
| `historical_session` | Single session within a batch | Navigation only |

---

## Historical Backtest Module

Batch-runs a registered strategy over multiple days of real candle data stored in a local warehouse.

### Data Warehouse Ingestion

```bash
# Ingest spot + options candles for a date range
POST /api/historical/ingest
Body: {"instrument": "NIFTY", "start_date": "2026-01-01", "end_date": "2026-03-31"}

# Check warehouse coverage
GET /api/historical/coverage?instrument=NIFTY
```

### Batch Run

```bash
POST /api/backtests/batch
Body: {
  "instrument": "NIFTY",
  "strategy_id": "orb_intraday_spread",
  "start_date": "2026-01-01",
  "end_date": "2026-03-31",
  "capital": 2500000
}
```

The batch runner iterates over each trading day in the warehouse, runs the full gate stack + exit engine, and persists per-session results. Results are accessible via the Runs Library and Workbench History Detail pages.

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
| `EXIT_TIME` | Candle time reaches 15:20 |

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

### Session Detail Page (`/workbench/replay/paper_session/:id`)

- Session summary (date, capital, status, minutes audited)
- **Trade detail** — contract breakdown table: BUY/SELL badge, full contract name (`NIFTY 22900 CE exp 13 Apr 2026`), lots, lot size, total qty, entry/exit price
- MTM progression chart (while trade was open)
- **Raw candle data** — scrollable OHLCV tables for SPOT + weekly and monthly option legs
- Minute audit log with action filter tabs; ENTER rows show inline contract details
- **Explainability block** — gate-by-gate pass/fail trace for the entry candle

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
├── alembic.ini
└── app/
    ├── main.py              # FastAPI app, CORS, rate limiting, startup hook
    ├── database.py          # Async engine, session factory, Base, init_db()
    ├── migrations/          # Alembic migration versions
    ├── models/
    │   ├── session.py       # BacktestSession model
    │   ├── paper_trade.py   # 6 paper trading ORM models
    │   ├── historical.py    # Historical warehouse + batch ORM models
    │   ├── workbench.py     # WorkbenchRun model
    │   └── users.py         # User + BrokerToken models
    ├── routers/
    │   ├── backtest.py      # Synthetic backtest endpoints
    │   ├── backtests.py     # Historical batch backtest endpoints
    │   ├── paper_trading.py # Paper trading endpoints
    │   ├── auth.py          # Zerodha OAuth
    │   ├── historical.py    # Historical data ingestion + coverage
    │   ├── users.py         # User register/login/refresh/logout
    │   └── workbench.py     # V2 workbench endpoints (/api/v2/*)
    └── services/
        ├── simulator.py         # Candle gen, indicators, option pricing, day runner
        ├── strategy.py          # Regime detection, leg builder
        ├── strategy_config.py   # build_strategy_snapshot(), workbench constants, date helpers
        ├── position_sizer.py    # 2% capital risk sizing
        ├── paper_engine.py      # ORB replay orchestrator
        ├── entry_gates.py       # G1–G7 gate evaluator
        ├── exit_engine.py       # MTM exit conditions
        ├── opening_range.py     # OR computation + candidate spread generators
        ├── option_resolver.py   # Zerodha instrument token lookup
        ├── zerodha_client.py    # Zerodha API wrappers
        ├── calendar.py          # NSE trading calendar helpers
        ├── workbench_catalog.py # Strategy catalog + visual_hints
        └── workbench_views.py   # Serializers: replay_payload, library items, resolve_strategy_identity
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
| Rate limiting | slowapi | 0.1.9 |
| Auth | python-jose (JWT) + cryptography (Fernet) | — |
| Migrations | Alembic | 1.13 |

---

## Frontend Deep Dive

### Screen Map

**V2 Workbench (primary)**

```
/workbench                      Strategy Catalog — browse all strategies
/workbench/strategy/:id         Run Builder — configure + submit a run
/workbench/replay/:kind/:id     Replay Analyzer — session detail, charts, audit log
/workbench/runs                 Runs Library — all saved runs with pagination
/workbench/history/:batchId     Workbench History Detail — batch summary + sessions
```

**Historical Backtest**

```
/historical                     Historical batch launcher (instrument, dates, strategy)
/historical/batch/:id           Batch detail with per-session breakdown
```

**Legacy (Synthetic Backtest + Paper Trading)**

```
/backtest           Run synthetic backtest (instrument, capital, date range)
/dashboard          Metrics + cumulative P&L chart + results table
/tradebook/:id      Synthetic backtest day drill-down (legs, 1-min chart)
/paper              ORB replay launcher (instrument, capital, date, access token)
/paper/sessions     Session list with status and decision counts
/paper/session/:id  Full session detail: audit log, trade, candles, CSV/PDF
```

### Key Frontend Services

| File | Purpose |
|------|---------|
| `src/api/index.js` | All API calls — axios wrappers for every endpoint group |
| `src/pages/RunBuilder.jsx` | Run config form with `normalizeVisual()`, `countWeekdaysInRange()` |
| `src/pages/ReplayAnalyzer.jsx` | Gate-by-gate audit, MTM chart, explainability block |
| `src/components/TopNav.jsx` | Primary workbench nav + legacy links (hidden on workbench) |
| `src/index.css` | `wb-*` CSS token classes (wb-card, wb-kicker, wb-grid, wb-chip, etc.) |

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

Schema auto-created at startup via `create_all` + Alembic migrations for schema changes.

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

### Historical Data Warehouse (6 tables)

| Table | Description |
|-------|-------------|
| `trading_days` | NSE calendar: date, instrument, data availability flags |
| `spot_candles` | 1-min OHLCV spot candles per day |
| `vix_candles` | 1-min India VIX candles per day |
| `futures_candles` | 1-min near-month futures candles |
| `options_candles` | 1-min candles for each option series needed |
| `session_batches` | Batch run metadata: strategy, date range, status, aggregate stats |

---

## API Reference

Base URL: `http://localhost:8000/api`

### User Auth

| Method | Path | Description |
|--------|------|-------------|
| POST | `/users/register` | Register with email + password |
| POST | `/users/login` | Returns `access_token` (15 min) + sets HttpOnly `refresh_token` cookie |
| POST | `/users/refresh` | Reads cookie, returns new access_token |
| POST | `/users/logout` | Clears refresh cookie |
| GET | `/users/me` | Current user profile (requires Bearer) |

### Zerodha Auth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/zerodha/login-url` | Returns Zerodha OAuth URL |
| POST | `/auth/zerodha/session` | Exchanges `request_token` → encrypted token stored server-side |

### V2 Workbench

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v2/workspace/summary` | Workspace overview: run counts, recent activity |
| GET | `/v2/strategies` | Strategy catalog (public, no auth required) |
| GET | `/v2/strategies/:id` | Single strategy with `visual_hints` |
| GET | `/v2/runs` | Paginated runs list (`kind`, `limit`, `offset` params) |
| POST | `/v2/runs` | Create a new run (paper or historical) |
| GET | `/v2/runs/:kind/:id` | Run detail (`kind`: `paper_session`, `historical_batch`, `historical_session`) |
| GET | `/v2/runs/:kind/:id/replay` | Full replay payload: decisions, marks, candles, explainability |
| POST | `/v2/runs/compare` | Compare two runs side-by-side |

### Historical Backtest

| Method | Path | Description |
|--------|------|-------------|
| POST | `/backtests/batch` | Start a batch backtest run |
| GET | `/backtests/batches` | List all batches |
| GET | `/backtests/batch/:id` | Batch detail + per-session stats |
| GET | `/backtests/session/:id` | Single historical session detail |

### Historical Data Ingestion

| Method | Path | Description |
|--------|------|-------------|
| POST | `/historical/ingest` | Ingest candles for a date range |
| GET | `/historical/coverage` | Warehouse coverage by instrument |

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
SECRET_KEY=<openssl rand -hex 32>
BROKER_TOKEN_ENCRYPTION_KEY=<Fernet.generate_key()>
```

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@db:5432/adaptive_options` | Async DB URL |
| `SECRET_KEY` | — | JWT signing key (required in production) |
| `BROKER_TOKEN_ENCRYPTION_KEY` | — | Fernet key for broker token encryption (required in production) |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `ENVIRONMENT` | `development` | Set to `production` to enable HSTS + strict secret validation |
| `ZERODHA_API_KEY` | — | Required for paper trading |
| `ZERODHA_API_SECRET` | — | Required for token exchange |
| `POSTGRES_DB` | `adaptive_options` | |
| `POSTGRES_USER` | `postgres` | |
| `POSTGRES_PASSWORD` | `postgres` | |

> In production the app will **refuse to start** if `SECRET_KEY` is the default placeholder or `BROKER_TOKEN_ENCRYPTION_KEY` is unset.

---

## Development Setup

### Prerequisites

- Docker Desktop 4.x+
- Zerodha Kite Connect API credentials (for paper trading and historical ingestion)
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

### Apply Alembic Migrations

```bash
cd backend
alembic upgrade head       # apply pending migrations
alembic current            # show current revision
alembic revision --autogenerate -m "description"  # generate new migration
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
| `test_workbench_services.py` | `workbench_catalog` and `workbench_views` unit tests |
| `test_workbench_router.py` | HTTP-level workbench endpoint tests (ASGI transport) |

### Frontend (Vitest)

```bash
cd frontend && npm test
```

| File | What's tested |
|------|--------------|
| `TopNav.test.jsx` | Primary + legacy nav links, active state, workbench visibility rules |
| `Backtest.test.jsx` | Single-date form, API call, loading state |
| `Dashboard.test.jsx` | Data render, navigation, empty/error states |
| `RegimeBadge / MetricCard / PnlChart` | Component rendering |
| `api/index.test.js` | Export contract, base URL, timeout, workbench API functions |

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
         SECRET_KEY / BROKER_TOKEN_ENCRYPTION_KEY ← set manually
         ▼
       PostgreSQL plugin  (managed, private)
```

### One-time Setup

1. Create a Railway project
2. Add PostgreSQL plugin — auto-sets `DATABASE_URL` on backend
3. Create **Backend** service — root dir `backend/`, set `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`, `SECRET_KEY`, `BROKER_TOKEN_ENCRYPTION_KEY`, `ENVIRONMENT=production`, `ALLOWED_ORIGINS=https://<frontend>.up.railway.app`
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

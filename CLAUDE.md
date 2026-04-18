# CLAUDE.md — Adaptive Options Project

This file gives Claude Code full context about the project so it can assist effectively without re-reading every file.

---

## Project Summary

**Adaptive Options** is a full-stack options backtesting + paper-trading platform for NSE index options (Nifty 50, Bank Nifty). It has two distinct modules:

1. **Synthetic Backtest** — simulates Iron Condor, Bull Put Spread, and Bear Call Spread strategies using deterministic synthetic candle data with auto-regime detection (EMA/RSI/IV Rank).
2. **Paper Trading ORB Replay** — replays a real historical trading day using **live Zerodha market data**. Evaluates the Opening Range Breakout (ORB) strategy through a G1–G7 gate stack, records every minute decision, and produces full audit logs + candle data.

Scope: **backtesting and paper trading only** — no live order placement.

---

## Running the App

### Local (Docker Compose)

```bash
cd Adaptive_options/
cp .env.example .env          # fill in ZERODHA_API_KEY + ZERODHA_API_SECRET
docker compose up -d          # start all 3 containers
docker compose logs -f        # tail logs
docker compose down           # stop
docker compose down -v        # stop + wipe DB
```

| Service | Port | Container name |
|---------|------|----------------|
| Frontend (nginx) | 3000 | adaptive_options_ui |
| Backend (FastAPI) | 8000 | adaptive_options_api |
| Database (PostgreSQL 15) | 5432 | adaptive_options_db |

Health check: `curl http://localhost:8000/health`

### Cloud (Railway)

The app runs on Railway with 3 services: **backend**, **frontend**, and a **PostgreSQL plugin**.

Key env vars:
- Backend: `DATABASE_URL` (auto-injected by Railway plugin, scheme is normalised), `PORT` (auto by Railway), `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`
- Frontend: `VITE_API_URL` = backend public URL (set manually once); `PORT` (auto by Railway)

See README § Railway Cloud Deployment for full setup steps.

---

## CI/CD

GitHub Actions workflow: `.github/workflows/ci.yml`

- **Every push/PR**: runs backend tests (pytest) + frontend tests (vitest)
- **Push to `main` only**: deploys backend → then frontend to Railway

Required GitHub secrets/variables: `RAILWAY_TOKEN` (secret), `RAILWAY_PROJECT_ID`, `RAILWAY_BACKEND_SERVICE_ID`, `RAILWAY_FRONTEND_SERVICE_ID` (variables).

## Unit Tests

### Backend
```bash
cd backend && python -m pytest tests/ -v
```

### Frontend
```bash
cd frontend && npm test
```

---

## Directory Layout

```
Adaptive_options/
├── docker-compose.yml
├── .env.example              ← ZERODHA_API_KEY / ZERODHA_API_SECRET
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py              ← FastAPI app entry point
│       ├── database.py          ← async engine, Base, get_db(), init_db()
│       ├── models/
│       │   ├── session.py       ← BacktestSession SQLAlchemy model
│       │   └── paper_trade.py   ← 6 paper trading ORM models
│       ├── routers/
│       │   ├── backtest.py      ← synthetic backtest endpoints
│       │   ├── paper_trading.py ← paper trading endpoints
│       │   └── auth.py          ← Zerodha OAuth flow
│       └── services/
│           ├── simulator.py     ← candle gen, EMA, RSI, option pricing, day runner
│           ├── strategy.py      ← regime detection, leg builder
│           ├── position_sizer.py ← 2% capital risk sizing
│           ├── paper_engine.py  ← ORB replay orchestrator
│           ├── entry_gates.py   ← G1–G7 gate stack
│           ├── exit_engine.py   ← MTM exit conditions
│           ├── opening_range.py ← OR computation + candidate spread generators
│           ├── option_resolver.py ← Zerodha instrument token lookup
│           ├── zerodha_client.py ← Zerodha API wrappers
│           └── calendar.py      ← NSE trading calendar helpers
└── frontend/
    ├── Dockerfile
    ├── nginx.conf               ← SPA fallback + /api proxy
    └── src/
        ├── App.jsx              ← router + layout
        ├── api/index.js         ← axios wrappers
        ├── components/          ← TopNav, MetricCard, RegimeBadge, PnlChart
        └── pages/
            ├── Backtest.jsx     ← Synthetic backtest form
            ├── Dashboard.jsx    ← Backtest results dashboard
            ├── TradeBook.jsx    ← Per-day backtest drill-down
            ├── PaperTrading.jsx ← Paper trading session launcher
            ├── SessionMonitor.jsx ← Session list
            └── PaperTradeBook.jsx ← Session detail: audit log, candle data, CSV/PDF
```

---

## Critical Business Logic

### Synthetic Backtest — Deterministic RNG

The RNG seed is derived from `MD5(date_str + instrument)`. Same inputs → same candles every time. Do not change the seed formula without understanding the impact on reproducibility.

### Synthetic Option Pricing Formula

Uses a simplified Black-Scholes approximation (not real market data):

```python
annual_vol = daily_vol * sqrt(252)
T          = remaining_minutes / (375 * 252)
d          = abs(spot - strike) / spot
time_value = spot * annual_vol * sqrt(T) * 0.45 * exp(-d / (annual_vol * 0.25))
price      = max(intrinsic + time_value, 0.50)
```

Do not replace with a full BS implementation without updating the calibration constant (0.45).

### ORB Paper Trading — Gate Stack (G0–G7)

Each minute after OR window closes, `entry_gates.py::evaluate_gates()` runs in sequence:

| Gate | Rule |
|------|------|
| G0 | No new entries at or after `ENTRY_CUTOFF_TIME` (default `13:00`) |
| G1 | Opening range window complete (first 15 candles) |
| G2 | No active trade already open |
| G3 | Close > OR high × 1.001 (bullish) or < OR low × 0.999 (bearish) |
| G4 | Follow-through: **previous** candle also confirmed the same breakout |
| G5 | Both legs of the spread have valid prices |
| G6 | Max loss ≤ 2% of capital (approved_lots ≥ 1) |
| G7 | Max possible gain ≥ session target (0.5% of capital) |

Candidate spreads: 5 strike pairs per direction tried in ATM-first order (offsets 0, ±1, ±2). First pair passing G5–G7 wins.

### ORB Paper Trading — Exit Conditions (exit_engine.py)

| Condition | Trigger |
|-----------|---------|
| `EXIT_TARGET` | total MTM ≥ session target (0.5% capital) |
| `EXIT_STOP` | total MTM ≤ −max_loss (spread fully lost) |
| `EXIT_TRAIL` | trail armed at `TRAIL_ARM_PCT × target`, then MTM falls to `peak × (1 - TRAIL_GIVEBACK)` |
| `EXIT_TIME` | configured square-off time or end of candle data reached |

### ORB Paper Trading — Charges

`realized_net_pnl = realized_gross_pnl − charges`

Charges: 4 × ₹20 brokerage + STT (0.05% sell side) + exchange txn (0.053%) + GST (18% on brokerage + exchange).

### Regime Detection (Synthetic Backtest)

Strictly follows the table in `strategy.py::select_strategy()`. The RSI overbought/oversold check (>70 or <30) is a hard override that results in NO_TRADE regardless of EMA state.

### Position Sizing (Synthetic Backtest)

`lots = max(1, floor(capital × 0.02 / max_loss_per_lot))`

The minimum is always 1 lot. Never remove this floor.

### Candle Index to Time

- Index 0 = 09:15
- Index 15 = 09:30 (OR complete; first entry evaluation minute)
- Index 360 = 15:15 (EOD trigger)
- Index 374 = 15:29 (last candle)

---

## API Endpoints

All under `/api`:

### Synthetic Backtest

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/backtest/run` | Run simulation, persist, return sessions |
| GET | `/backtest/results` | Paginated session list |
| GET | `/backtest/results/:id` | Full session with legs + min_data |
| GET | `/backtest/summary` | Aggregated stats |
| DELETE | `/backtest/results` | Clear all sessions |

### Paper Trading (ORB Replay)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/paper/session/run` | Replay one trading day; bulk-insert results |
| GET | `/paper/sessions` | List all paper sessions |
| GET | `/paper/session/{id}` | Session detail + action summary stats |
| GET | `/paper/session/{id}/decisions` | Full minute audit log (paginated) |
| GET | `/paper/session/{id}/trade` | Trade header + legs |
| GET | `/paper/session/{id}/trade/marks` | Per-minute MTM array |
| GET | `/paper/session/{id}/candles` | Raw OHLCV candle series (SPOT + options) |

### Zerodha Auth

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/auth/zerodha/login-url` | Returns Zerodha OAuth URL |
| POST | `/auth/zerodha/session` | Exchanges request_token → access_token |

---

## Database

Schema is auto-created at startup via `init_db()` (SQLAlchemy `create_all`). No Alembic migrations.

### Synthetic backtest

Single table: `backtest_sessions`.

JSONB columns: `legs` (option leg objects), `min_data` (`{time, spot, pnl}` per minute).

### Paper Trading (6 tables)

| Table | Description |
|-------|-------------|
| `paper_sessions` | One row per replay run |
| `strategy_minute_decisions` | One row per market minute — full G1–G7 audit ledger |
| `paper_trade_headers` | One row per trade opened (entry/exit prices, P&L, bias) |
| `paper_trade_legs` | Long + short option legs with entry/exit prices |
| `paper_trade_minute_marks` | Per-minute MTM while trade is open |
| `paper_candle_series` | Raw 1-min OHLCV candles: SPOT + weekly/monthly option legs |

---

## Frontend Routes

| Path | Component | Screen |
|------|-----------|--------|
| `/backtest` | `Backtest.jsx` | Run synthetic backtest form |
| `/dashboard` | `Dashboard.jsx` | Metrics + chart + results table |
| `/tradebook/:id` | `TradeBook.jsx` | Per-day backtest drill-down |
| `/paper` | `PaperTrading.jsx` | Paper trade session launcher |
| `/paper/sessions` | `SessionMonitor.jsx` | Session list |
| `/paper/session/:id` | `PaperTradeBook.jsx` | Session detail: audit log, trade, candles, CSV/PDF |

The nginx config proxies `/api/*` to `backend:8000`. The SPA fallback handles all other routes via `try_files`.

---

## Security Architecture (SEC-1 / SEC-2)

### App Authentication

Email/password auth with JWT.

| Flow step | Endpoint | Notes |
|-----------|----------|-------|
| Register | POST `/api/users/register` | email + password (≥8 chars) |
| Login | POST `/api/users/login` | returns `access_token` (15 min) + sets HttpOnly `refresh_token` cookie (7 days) |
| Refresh | POST `/api/users/refresh` | reads cookie, returns new access_token |
| Logout | POST `/api/users/logout` | clears cookie |
| Profile | GET `/api/users/me` | requires Bearer token |

All business endpoints (`/backtest/*`, `/paper/*`, `/auth/zerodha/*`) require `Authorization: Bearer <access_token>`.

### Broker Token Storage

Zerodha access tokens are stored **server-side** — never sent to or stored in the browser.

1. Authenticate with app (login)
2. POST `/api/auth/zerodha/session` with `request_token` → backend encrypts + stores in `broker_tokens` table
3. POST `/api/paper/session/run` — no `access_token` in body; backend retrieves from DB for the current user

Encryption uses Fernet (symmetric). Key priority: `BROKER_TOKEN_ENCRYPTION_KEY` env var → fallback derived from `SECRET_KEY` (dev only).

### Required env vars (production)

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | JWT signing key — generate with `openssl rand -hex 32` |
| `BROKER_TOKEN_ENCRYPTION_KEY` | Fernet key — generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (e.g. `https://yourapp.railway.app`) |
| `ENVIRONMENT` | Set to `production` to enable HSTS + fail-fast on default secrets |

The app will **refuse to start** in production if `SECRET_KEY` is the default placeholder or `BROKER_TOKEN_ENCRYPTION_KEY` is unset.

### Alembic Migrations

`alembic.ini` + `app/migrations/` are set up. To apply migrations manually:

```bash
cd backend
alembic upgrade head       # apply pending migrations
alembic current            # show current revision
alembic revision --autogenerate -m "description"  # generate new migration
```

The runtime `create_all` + idempotent `ALTER TABLE IF NOT EXISTS` in `init_db()` remains as a safety net for Docker startup compatibility.

---

## What NOT to Change Without Care

- `_seed()` in `simulator.py` — changing breaks determinism guarantee
- `price_option()` calibration constants — affects all P&L calculations
- `ENTRY_CANDLE_IDX = 15` — entry is always at 09:30
- `EOD_CANDLE_IDX = 360` — end-of-day is always 15:15
- The `legs` JSONB schema — frontend `TradeBook.jsx` depends on field names `act`, `typ`, `strike`, `delta`, `ep`, `ep2`, `legPnl`, `lots`
- `MAX_RISK_PCT = 0.02` and `TARGET_PCT = 0.005` in `entry_gates.py` — these define the core ORB risk/reward parameters
- `OR_WINDOW_MINUTES = 15` in `opening_range.py` — opening range is always 09:15–09:29
- `N_CANDIDATE_SPREADS = 5` in `opening_range.py` — number of strike pairs tried per direction
- `ENTRY_CUTOFF_TIME`, `TRAIL_ARM_PCT`, and `TRAIL_GIVEBACK` in `strategy_config.py` — these define the rule-stack v2 entry/exit behavior

---

## Common Tasks

### Rebuild backend after Python changes
```bash
docker compose build backend && docker compose up -d backend
```

### Rebuild frontend after JS/CSS changes
```bash
docker compose build frontend && docker compose up -d frontend
```

### Inspect the database
```bash
docker exec -it adaptive_options_db psql -U postgres -d adaptive_options
\dt                                         # list tables
SELECT count(*) FROM backtest_sessions;
SELECT count(*) FROM paper_sessions;
SELECT session_date, status, decision_count FROM paper_sessions ORDER BY created_at DESC;
```

### Reset paper trading data
```bash
docker exec -i adaptive_options_db psql -U postgres -d adaptive_options -c "
TRUNCATE paper_candle_series, paper_trade_minute_marks, paper_trade_legs,
         paper_trade_headers, strategy_minute_decisions, paper_sessions CASCADE;
"
```

### Reset synthetic backtest data
```bash
curl -X DELETE http://localhost:8000/api/backtest/results
```

### Get Zerodha access token
```bash
# 1. Get login URL
curl http://localhost:8000/api/auth/zerodha/login-url
# 2. Open the URL in browser, complete login, copy request_token from redirect URL
# 3. Exchange for access token
curl -s -X POST http://localhost:8000/api/auth/zerodha/session \
  -H "Content-Type: application/json" \
  -d '{"request_token":"<token_from_redirect>"}'
```

Zerodha access tokens expire at 6 AM IST daily and must be refreshed each day.

### Run a paper trading session via API
```bash
curl -s -X POST http://localhost:8000/api/paper/session/run \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY","date":"2026-04-07","capital":2500000,"access_token":"<token>"}' \
  | python3 -m json.tool
```

### Run a synthetic backtest via API
```bash
curl -s -X POST http://localhost:8000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY","startDate":"2025-01-06","endDate":"2025-01-10","capital":500000}' \
  | python3 -m json.tool
```

---

## Out of Scope

Do not implement the following in this repo without updating the PRD:
- Live broker integration (real order placement)
- Real-time WebSocket market data
- Multi-user auth
- Email/push notifications

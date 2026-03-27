# CLAUDE.md — Adaptive Options Project

This file gives Claude Code full context about the project so it can assist effectively without re-reading every file.

---

## Project Summary

**Adaptive Options** is a full-stack MVP backtesting platform for NSE index options strategies (Nifty 50, Bank Nifty). It simulates Iron Condor, Bull Put Spread, and Bear Call Spread strategies using deterministic synthetic candle data with auto-regime detection.

Scope: **backtest only** — no live broker, no real order placement.

---

## Running the App

```bash
cd Adaptive_options/
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

---

## Directory Layout

```
Adaptive_options/
├── docker-compose.yml
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py              ← FastAPI app entry point
│       ├── database.py          ← async engine, Base, get_db(), init_db()
│       ├── models/session.py    ← BacktestSession SQLAlchemy model
│       ├── routers/backtest.py  ← all 5 REST endpoints
│       └── services/
│           ├── simulator.py     ← candle gen, EMA, RSI, option pricing, day runner
│           ├── strategy.py      ← regime detection, leg builder
│           └── position_sizer.py ← 2% capital risk sizing
└── frontend/
    ├── Dockerfile
    ├── nginx.conf               ← SPA fallback + /api proxy
    └── src/
        ├── App.jsx              ← router + layout
        ├── api/index.js         ← axios wrappers
        ├── components/          ← TopNav, MetricCard, RegimeBadge, PnlChart
        └── pages/
            ├── Backtest.jsx     ← Screen 1
            ├── Dashboard.jsx    ← Screen 2
            └── TradeBook.jsx    ← Screen 3
```

---

## Critical Business Logic

### Simulation is Deterministic

The RNG seed is derived from `MD5(date_str + instrument)`. Same inputs → same candles every time. Do not change the seed formula without understanding the impact on reproducibility.

### Option Pricing Formula

Uses a simplified Black-Scholes approximation (not real market data):

```python
annual_vol = daily_vol * sqrt(252)
T          = remaining_minutes / (375 * 252)
d          = abs(spot - strike) / spot
time_value = spot * annual_vol * sqrt(T) * 0.45 * exp(-d / (annual_vol * 0.25))
price      = max(intrinsic + time_value, 0.50)
```

This is intentionally simplified for educational purposes. Do not replace with a full BS implementation without updating the calibration constant (0.45).

### Regime Detection

Strictly follows the table in `strategy.py::select_strategy()`. The RSI overbought/oversold check (>70 or <30) is a hard override that results in NO_TRADE regardless of EMA state.

### Position Sizing

`lots = max(1, floor(capital × 0.02 / max_loss_per_lot))`

The minimum is always 1 lot. Never remove this floor.

### Candle Index to Time

- Index 0 = 09:15
- Index 15 = 09:30 (entry)
- Index 360 = 15:15 (EOD trigger)
- Index 374 = 15:29 (last candle)

---

## API Endpoints

All under `/api`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/backtest/run` | Run simulation, persist, return sessions |
| GET | `/backtest/results` | Paginated session list (no min_data) |
| GET | `/backtest/results/:id` | Full session with legs + min_data |
| GET | `/backtest/summary` | Aggregated stats |
| DELETE | `/backtest/results` | Clear all sessions |

---

## Database

Single table: `backtest_sessions`. Schema is auto-created at startup via `init_db()` (SQLAlchemy `create_all`). No Alembic migrations in MVP.

JSONB columns:
- `legs` — array of option leg objects
- `min_data` — array of `{time, spot, pnl}` for 1-min chart

---

## Frontend Routes

| Path | Component | Screen |
|------|-----------|--------|
| `/backtest` | `Backtest.jsx` | Run backtest form + strategy info |
| `/dashboard` | `Dashboard.jsx` | Metrics + chart + results table |
| `/tradebook/:id` | `TradeBook.jsx` | Per-day drill-down |

The nginx config proxies `/api/*` to `backend:8000`. The SPA fallback handles all other routes via `try_files`.

---

## What NOT to Change Without Care

- `_seed()` in `simulator.py` — changing breaks determinism guarantee
- `price_option()` calibration constants — affects all P&L calculations
- `ENTRY_CANDLE_IDX = 15` — entry is always at 09:30
- `EOD_CANDLE_IDX = 360` — end-of-day is always 15:15
- The `legs` JSONB schema — frontend `TradeBook.jsx` depends on field names `act`, `typ`, `strike`, `delta`, `ep`, `ep2`, `legPnl`, `lots`

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
\dt                                    # list tables
SELECT count(*) FROM backtest_sessions;
SELECT session_date, strategy, pnl FROM backtest_sessions ORDER BY session_date;
```

### Reset all data
```bash
curl -X DELETE http://localhost:8000/api/backtest/results
# or from the dashboard UI: "Clear All" button
```

### Run a test backtest via API
```bash
curl -s -X POST http://localhost:8000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY","startDate":"2025-01-06","endDate":"2025-01-10","capital":500000}' \
  | python3 -m json.tool
```

---

## Out of Scope

Do not implement the following in this repo without updating the PRD:
- Live broker integration
- Real-time market data (WebSocket)
- Multi-user auth
- Email/push notifications
- CSV/PDF export
- Real NSE options chain pricing

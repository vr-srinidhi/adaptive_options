# CLAUDE.md — Adaptive Options Project

This file gives Claude Code full context about the project so it can assist effectively without re-reading every file.

---

## Project Summary

**Adaptive Options** is a full-stack options backtesting + paper-trading platform for NSE index options (Nifty 50, Bank Nifty). It has five modules:

1. **Synthetic Backtest** — simulates Iron Condor, Bull Put Spread, and Bear Call Spread strategies using deterministic synthetic candle data with auto-regime detection (EMA/RSI/IV Rank).
2. **Paper Trading ORB Replay** — replays a real historical trading day using **live Zerodha market data**. Evaluates the Opening Range Breakout (ORB) strategy through a G1–G7 gate stack, records every minute decision, and produces full audit logs + candle data.
3. **Historical Backtest** — runs the ORB engine against a DB-backed warehouse of real 1-min candle data (spot + options). Batches span multiple trading days; results persist alongside paper trading sessions.
4. **V2 Workbench** — strategy-agnostic shell (Strategy Catalog → Run Builder → Replay Analyzer → Runs Library) aligned to the product PRD. Two executors are live: `orb_v1` (ORB paper/historical) and `generic_v1` (single-session backtest). 11 further strategies are catalogued as `planned`/`research`.
5. **Generic Strategy Engine** — declarative executor that powers any strategy defined in the catalog via `leg_template` + `entry_rule_id` + `exit_rule`. No new Python files needed to add a strategy. Short Straddle is the first live strategy on this engine.

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
│   ├── alembic.ini           ← Alembic migration config
│   └── app/
│       ├── main.py              ← FastAPI app entry point
│       ├── database.py          ← async engine, Base, get_db(), init_db()
│       ├── core/
│       │   ├── config.py        ← settings (env vars, CORS, secrets)
│       │   ├── security.py      ← JWT helpers, password hashing
│       │   └── rate_limit.py    ← slowapi limiter
│       ├── dependencies/
│       │   └── auth.py          ← get_current_active_user dependency
│       ├── middleware/
│       │   └── security_headers.py ← HSTS, CSP, X-Frame-Options
│       ├── migrations/
│       │   └── versions/        ← Alembic migration scripts
│       ├── models/
│       │   ├── session.py       ← BacktestSession SQLAlchemy model
│       │   ├── paper_trade.py   ← 6 paper trading ORM models
│       │   ├── historical.py    ← TradingDay, SpotCandle, VixCandle, FuturesCandle, OptionsCandle, SessionBatch
│       │   ├── user.py          ← User model
│       │   ├── broker_token.py  ← encrypted Zerodha token storage
│       │   └── audit_log.py     ← security audit events
│       ├── routers/
│       │   ├── backtest.py      ← synthetic backtest endpoints
│       │   ├── backtests.py     ← historical batch CRUD (/api/backtests/*)
│       │   ├── paper_trading.py ← paper trading endpoints
│       │   ├── historical.py    ← data ingestion + trading-days catalogue (/api/historical/*)
│       │   ├── workbench.py     ← v2 workbench endpoints (/api/v2/*)
│       │   ├── auth.py          ← Zerodha OAuth flow
│       │   └── users.py         ← user register/login/refresh/logout
│       └── services/
│           ├── simulator.py              ← candle gen, EMA, RSI, option pricing, day runner
│           ├── strategy.py               ← regime detection, leg builder
│           ├── strategy_config.py        ← central ORB config, build_strategy_snapshot(), latest_weekday()
│           ├── position_sizer.py         ← 2% capital risk sizing
│           ├── paper_engine.py           ← ORB replay orchestrator (run_paper_engine / run_paper_engine_core)
│           ├── entry_gates.py            ← G1–G7 gate stack
│           ├── exit_engine.py            ← MTM exit conditions
│           ├── opening_range.py          ← OR computation + candidate spread generators
│           ├── spread_selector.py        ← ranked candidate selection (Phase 2)
│           ├── option_resolver.py        ← Zerodha instrument token lookup
│           ├── zerodha_client.py         ← Zerodha API wrappers
│           ├── calendar.py               ← NSE trading calendar helpers
│           ├── batch_runner.py           ← historical batch executor (background task)
│           ├── historical_ingestion.py   ← CSV → DB ingestion
│           ├── historical_market_data.py ← load warehouse data for engine (incl. load_vix_candles)
│           ├── workbench_catalog.py      ← strategy catalog (13 strategies with visual_hints)
│           ├── workbench_views.py        ← serializers: replay_payload, paper_session_library_item, etc.
│           ├── charges_service.py        ← single source of truth for NSE F&O brokerage charges
│           ├── contract_spec_service.py  ← lot size, strike step, expiry, ATM strike resolution
│           ├── entry_rule_registry.py    ← pluggable entry rules (TimedEntryRule; add more here)
│           ├── generic_executor.py       ← validate_run + execute_run for generic_v1 strategies
│           ├── strategy_replay_serializer.py ← PRD §13 replay payload for strategy_run kind
│           ├── token_store.py            ← broker token encrypt/decrypt
│           └── audit.py                  ← audit log helpers
└── frontend/
    ├── Dockerfile
    ├── nginx.conf               ← SPA fallback + /api proxy
    └── src/
        ├── App.jsx              ← router + layout (default / → /workbench)
        ├── api/index.js         ← axios wrappers for all endpoints incl. /v2
        ├── components/          ← TopNav, MetricCard, RegimeBadge, PnlChart, BrandLogo, ProtectedRoute
        ├── contexts/
        │   └── AuthContext.jsx  ← JWT auth context
        ├── utils/
        │   └── workbench.js     ← fmtINR, fmtDateTime, runStatusTone, strategyStatusTone
        └── pages/
            ├── Login.jsx             ← email/password login
            ├── ZerodhaConnect.jsx    ← Zerodha OAuth connect
            ├── WorkspaceHome.jsx     ← Workbench home: recent runs, data health, quick start
            ├── StrategyCatalog.jsx   ← Browse strategy cards (Bullish/Bearish/Neutral/Others)
            ├── RunBuilder.jsx        ← Configure + launch a run (guided + advanced mode)
            ├── ReplayDesk.jsx        ← Paper session list + replay entry point
            ├── ReplayAnalyzer.jsx    ← Per-session replay: charts, decision stream, legs
            ├── RunsLibrary.jsx       ← Saved runs list with compare + history
            ├── WorkbenchHistoryDetail.jsx ← Batch or session history detail
            ├── Backtest.jsx          ← (legacy) Synthetic backtest form
            ├── Dashboard.jsx         ← (legacy) Backtest results dashboard
            ├── TradeBook.jsx         ← (legacy) Per-day backtest drill-down
            ├── PaperTrading.jsx      ← (legacy) Paper trading session launcher
            ├── SessionMonitor.jsx    ← (legacy) Session list
            ├── PaperTradeBook.jsx    ← (legacy) Session detail: audit log, candle data, CSV/PDF
            ├── Backtests.jsx         ← Historical batch list + create form
            ├── BacktestBatchDetail.jsx ← Batch progress + session drill-down
            └── HistoricalSessionDetail.jsx ← Historical session detail
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

### ORB Paper Trading — Gate Stack (G1–G7)

Each minute after OR window closes, `entry_gates.py::evaluate_gates()` runs in sequence:

| Gate | Rule |
|------|------|
| G1 | Opening range window complete (first 15 candles) |
| G2 | No active trade already open |
| G3 | Close > OR high × 1.001 (bullish) or < OR low × 0.999 (bearish) |
| G4 | Follow-through: **previous** candle also confirmed the same breakout |
| G5 | Both legs of the spread have valid prices |
| G6 | Max loss ≤ 2% of capital (approved_lots ≥ 1) |
| G7 | Max possible gain ≥ session target (0.5% of capital) |

Candidate spreads: 5 strike pairs per direction tried in ATM-first order (offsets 0, ±1, ±2). First pair passing G5–G7 wins (Phase 2: ranked by `spread_selector.py`).

### ORB Paper Trading — Exit Conditions (exit_engine.py)

| Condition | Trigger |
|-----------|---------|
| `EXIT_TARGET` | total MTM ≥ session target (0.5% capital) |
| `EXIT_STOP` | total MTM ≤ −max_loss (spread fully lost) |
| `EXIT_TIME` | 15:20 (strategy_config `square_off_time`) or end of candle data |

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

### Workbench Strategy Catalog

`workbench_catalog.py` is the single source of truth for strategy metadata. Each entry contains:
- `id`, `name`, `bias`, `status` (`available` | `planned` | `research`)
- `executor` — which backend engine handles it (`"orb_v1"` or `"generic_v1"`)
- `modes` — `paper_replay`, `historical_backtest`, and/or `single_session_backtest`
- `leg_template` — declarative list of `{side, option_type, strike_offset_steps}` (generic_v1 only)
- `entry_rule_id` — key into `entry_rule_registry.py` (`"timed_entry"` covers ~38/40 strategies)
- `exit_rule` — `{target_pct, stop_capital_pct, stop_multiple, trail_trigger, trail_pct, time_exit, data_gap_exit}` (generic_v1 only)
- `params_schema` — field definitions consumed by the Run Builder form
- `defaults` — live-computed via `_current_replay_defaults()` using `latest_weekday()`
- `visual_hints` — payoff shape, constraint fields, leg descriptions, metric ratios consumed by `RunBuilder.jsx` via `normalizeVisual()`

`supported_strategy_ids()` returns only `status == "available"` entries. The `create_run` endpoint rejects execution of non-available strategies.

### Generic Strategy Engine (generic_v1)

**Adding a new strategy requires only a catalog entry** — no new Python files:

```python
{
    "id": "buy_call",
    "executor": "generic_v1",
    "entry_rule_id": "timed_entry",
    "modes": ["single_session_backtest"],
    "leg_template": [{"side": "BUY", "option_type": "CE", "strike_offset_steps": 1}],
    "exit_rule": {"target_pct": 0.50, "stop_capital_pct": 0.01, "time_exit": "15:25", "data_gap_exit": True},
    ...
}
```

For a conditional entry (e.g., momentum check before entering):
1. Subclass `BaseEntryRule` in `entry_rule_registry.py`
2. Register it in `ENTRY_RULES` dict
3. Set `entry_rule_id` in the catalog entry

### Generic Executor Flow

```
validate_run(db, strategy, config)
  ├── trading_day exists + backtest_ready
  ├── contract spec (lot_size, strike_step) from instrument_contract_specs
  ├── spot at entry_time → ATM strike
  ├── resolve nearest expiry with CE + PE data
  ├── VIX guardrail check (if enabled)
  └── approved_lots = floor(capital / est_margin_per_lot)

execute_run(db, run_id, strategy, config, validation, user_id)
  ├── load spot_candles, vix_candles, option_candles from warehouse
  ├── minute loop:
  │     entry rule → ENTER or HOLD
  │       if prices missing at entry_time, retry for up to 5 min (grace window)
  │       actual entry timestamp persisted (may differ from configured entry_time)
  │     on ENTER: record leg prices, entry_credit, entry_charges
  │     on HOLD with trade open: compute MTM, check exits (in priority order):
  │       STOP_EXIT      net_mtm <= -(capital × stop_capital_pct)  [preferred]
  │                   or net_mtm <= -(entry_credit_total × stop_multiple)  [fallback]
  │       TRAIL_EXIT     trail active AND net_mtm <= trail_peak × trail_pct
  │       TARGET_EXIT    net_mtm >= entry_credit_total × target_pct
  │                      (suppressed when trail_trigger > 0 — trail manages profit exit)
  │       TIME_EXIT      minute_ts.time() >= time_exit
  │       DATA_GAP_EXIT  stale_minutes > 1
  └── persist all 6 tables in a single commit

Trailing stop: activates once net_mtm >= trail_trigger; thereafter tracks peak
and exits when net_mtm falls to trail_peak × trail_pct. When TRAIL_EXIT fires,
realized_net_pnl is locked at trail_stop_level (not the candle close, which may
gap through the stop).
```

MTM formula (short strategies):
```
gross_mtm_per_unit = Σ (entry_price_i − current_price_i) for SELL legs
gross_mtm_total    = gross_mtm_per_unit × lot_size × approved_lots
net_mtm            = gross_mtm_total − entry_charges − est_exit_charges
```

### Short Straddle — First generic_v1 Strategy

SELL ATM CE + SELL ATM PE at `entry_time`. Profits from premium decay when spot stays range-bound.

| Parameter | Value |
|-----------|-------|
| Entry rule | `timed_entry` — enter at `entry_time`; retries up to 5 min if prices missing |
| Target exit | Suppressed when trail is configured — trail manages profit exit |
| Stop exit | 1.5% of capital (e.g. ₹37,500 at ₹25L) |
| Trailing stop | Activates at ₹12,000 net MTM; exits if net_mtm drops to 50% of peak |
| Time exit | 15:25 |
| Lot sizes | 75 (NIFTY post-Nov 2024), 50 (NIFTY pre-Nov 2024) |

### ReplayAnalyzer — strategy_run chart payload

`GET /api/v2/runs/strategy_run/{id}/replay` returns:

```
{
  run:              { id, trade_date, entry_time, exit_time, status, exit_reason, ... }
  legs:             [{ leg_index, side, option_type, strike, expiry_date, entry_price, exit_price }]
  spot_series_full: [{ timestamp, close }]   ← full day 09:15–15:29 (375 rows)
  mtm_series:       [{ timestamp, gross_mtm, net_mtm, trail_stop_level, event_code }]  ← trade window only
  shadow_mtm_series:[{ timestamp, net_mtm }] ← exit_time+1 → 15:25, hypothetical "if held"
  minute_table:     flat join spot + leg prices per minute
  events:           ENTRY / HOLD / EXIT events with payload_json
}
```

`ReplayAnalyzer.jsx` uses `spot_series_full` as the x-axis backbone for both the Spot and MTM charts so the full trading day (09:15–15:29) is always visible. The MTM line renders only for the trade window (null before entry, null after exit, `connectNulls={false}`). The shadow MTM line (`#a78bfa` dashed) continues from exit to 15:25. `ReferenceLine` marks entry (green IN) and exit (red OUT) on both charts.

### instrument_contract_specs

Seeded at startup in `init_db()`. Stores lot size history with date ranges so historical backtests use the correct lot size:

| Instrument | From | To | Lot Size | Strike Step |
|------------|------|----|----------|-------------|
| NIFTY | 2020-01-01 | 2024-11-20 | 50 | 50 |
| NIFTY | 2024-11-21 | — | 75 | 50 |
| BANKNIFTY | 2020-01-01 | 2024-11-20 | 25 | 100 |
| BANKNIFTY | 2024-11-21 | — | 35 | 100 |

### build_strategy_snapshot

`strategy_config.py::build_strategy_snapshot(instrument, capital, *, strategy_id, strategy_name, run_type, input_config)` is the canonical way to freeze strategy config at run creation. Both `backtests.py` and `workbench.py` must use it — do not add a third local copy.

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

### Historical Backtest

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/backtests/batches` | Create + optionally launch a batch |
| GET | `/backtests/batches` | List all batches |
| GET | `/backtests/batches/{id}` | Batch detail + progress counters |
| POST | `/backtests/batches/{id}/run` | (Re-)trigger batch execution |
| DELETE | `/backtests/batches/{id}` | Cancel / delete batch |
| GET | `/backtests/batches/{id}/sessions` | Sessions belonging to a batch |
| GET | `/backtests/sessions/{id}` | Historical session detail |
| GET | `/backtests/sessions/{id}/decisions` | Minute audit log (paginated) |
| GET | `/backtests/sessions/{id}/trade` | Trade header + legs |
| GET | `/backtests/sessions/{id}/trade/marks` | Per-minute MTM |

### Historical Data Ingestion

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/historical/ingest/day/{date}` | Ingest one day from CSV files |
| POST | `/historical/ingest/bulk` | Queue bulk ingestion (background) |
| POST | `/historical/catalogue/sync` | Scan disk → populate trading_days rows |
| GET | `/historical/trading-days` | List trading_days catalogue |

### V2 Workbench

All under `/api/v2`. Strategy catalog endpoints are intentionally public (no auth); all run/replay endpoints require Bearer token.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v2/workspace/summary` | Home metrics, recent runs, data readiness, featured strategies |
| GET | `/v2/strategies` | Strategy catalog (public) |
| GET | `/v2/strategies/{id}` | Strategy detail with visual_hints and defaults (public) |
| GET | `/v2/runs` | Unified run list: `paper_session`, `historical_batch`, `strategy_run` (`kind`, `limit`, `offset`) |
| POST | `/v2/runs` | Create and execute a run (`run_type` ∈ `paper_replay`, `historical_backtest`, `single_session_backtest`) |
| POST | `/v2/runs/validate` | Dry-run validation — resolves contract, expiry, lots; no DB writes |
| GET | `/v2/runs/{kind}/{id}` | Run detail; `kind` ∈ `paper_session`, `historical_batch`, `historical_session`, `strategy_run` |
| GET | `/v2/runs/{kind}/{id}/replay` | Full replay payload; `strategy_run` kind returns PRD §13 shape |
| GET | `/v2/runs/compare` | Compare up to 4 runs by comma-separated `refs` (`kind:uuid,...`); supports `paper_session`, `historical_batch`, `strategy_run` |

### Zerodha Auth

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/auth/zerodha/login-url` | Returns Zerodha OAuth URL |
| POST | `/auth/zerodha/session` | Exchanges request_token → access_token |

### User Auth

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/users/register` | Register email + password |
| POST | `/users/login` | Returns access_token + sets HttpOnly refresh cookie |
| POST | `/users/refresh` | Exchange refresh cookie for new access_token |
| POST | `/users/logout` | Clears refresh cookie |
| GET | `/users/me` | Profile (requires Bearer token) |

---

## Database

Schema is auto-created at startup via `init_db()` (SQLAlchemy `create_all`). Alembic migrations in `app/migrations/versions/` handle additive changes.

### Synthetic backtest

Single table: `backtest_sessions`.

JSONB columns: `legs` (option leg objects), `min_data` (`{time, spot, pnl}` per minute).

### Paper Trading (6 tables)

| Table | Description |
|-------|-------------|
| `paper_sessions` | One row per replay run (`session_type`: `paper_replay` or `historical_backtest`) |
| `strategy_minute_decisions` | One row per market minute — full G1–G7 audit ledger |
| `paper_trade_headers` | One row per trade opened (entry/exit prices, P&L, bias) |
| `paper_trade_legs` | Long + short option legs with entry/exit prices |
| `paper_trade_minute_marks` | Per-minute MTM while trade is open |
| `paper_candle_series` | Raw 1-min OHLCV candles: SPOT + weekly/monthly option legs |

### Historical Data Warehouse (6 tables)

| Table | Description |
|-------|-------------|
| `trading_days` | One row per trading date — availability flags, ingestion status, `backtest_ready` |
| `spot_candles` | 1-min OHLCV for NIFTY spot |
| `vix_candles` | 1-min OHLCV for India VIX |
| `futures_candles` | 1-min OHLCV+OI for NIFTY futures |
| `options_candles` | 1-min OHLCV+ltp+OI for NIFTY options (~67 M rows); lookup index on `(trade_date, expiry_date, option_type, strike, timestamp)` |
| `session_batches` | Groups multiple historical backtest sessions into one batch run |

### Generic Strategy Engine (7 tables)

| Table | Description |
|-------|-------------|
| `instrument_contract_specs` | Lot size + strike step history per instrument (date-range aware; seeded at startup) |
| `strategy_runs` | One row per `single_session_backtest` run — header, capital, P&L, status |
| `strategy_run_legs` | One row per option leg — entry/exit prices, gross P&L |
| `strategy_run_mtm` | One row per minute while trade is open — spot, VIX, gross/net MTM |
| `strategy_leg_mtm` | One row per leg per minute — individual leg price + stale_minutes |
| `strategy_run_events` | ENTRY, EXIT, HOLD, NO_TRADE events with payload JSON |

---

## Frontend Routes

### V2 Workbench (primary navigation — default entry point)

| Path | Component | Screen |
|------|-----------|--------|
| `/` | → `/workbench` | Redirect |
| `/workbench` | `WorkspaceHome.jsx` | Home: recent runs, data readiness, quick start |
| `/workbench/strategies` | `StrategyCatalog.jsx` | Browse strategy cards by bucket |
| `/workbench/run` | `RunBuilder.jsx` | Configure + launch a run |
| `/workbench/replay` | `ReplayDesk.jsx` | Paper session list + replay entry |
| `/workbench/replay/:kind/:id` | `ReplayAnalyzer.jsx` | Per-session: full-day spot + MTM charts with IN/OUT markers, shadow MTM, decisions, legs |
| `/workbench/history` | `RunsLibrary.jsx` | All saved runs, sorted date desc, infinite scroll (20 rows at a time) |
| `/workbench/history/:kind/:id` | `WorkbenchHistoryDetail.jsx` | Batch or session detail |

### Historical Backtest

| Path | Component | Screen |
|------|-----------|--------|
| `/backtests` | `Backtests.jsx` | Batch list + create form |
| `/backtests/:batchId` | `BacktestBatchDetail.jsx` | Batch progress + session list |
| `/backtests/sessions/:sessionId` | `HistoricalSessionDetail.jsx` | Session detail |

### Legacy (still accessible via TopNav LEGACY section)

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

All business endpoints (`/backtest/*`, `/paper/*`, `/auth/zerodha/*`, `/backtests/*`, `/api/v2/runs*`, `/api/v2/workspace/*`) require `Authorization: Bearer <access_token>`. The strategy catalog endpoints (`/api/v2/strategies`) are intentionally public.

### Broker Token Storage

Zerodha access tokens are stored **server-side** — never sent to or stored in the browser.

1. Authenticate with app (login)
2. POST `/api/auth/zerodha/session` with `request_token` → backend encrypts + stores in `broker_tokens` table
3. POST `/api/paper/session/run` or POST `/api/v2/runs` — no `access_token` in body; backend retrieves from DB for the current user (or exchanges a fresh `request_token` inline if provided in the workbench run config)

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
- `build_strategy_snapshot()` signature in `strategy_config.py` — both `backtests.py` and `workbench.py` depend on it; the snapshot schema is stored in DB
- `_STRATEGIES` list in `workbench_catalog.py` — strategy IDs are stored in `strategy_config_snapshot` JSONB in the DB; renaming an `id` will break `resolve_strategy_identity()` lookups on old rows
- `visual_hints` keys in `workbench_catalog.py` — `RunBuilder.jsx::normalizeVisual()` maps snake_case keys directly; renaming requires updating both files
- `leg_template` / `exit_rule` keys in `workbench_catalog.py` — `generic_executor.py` reads these directly by name
- `instrument_contract_specs` seed in `database.py` — date ranges must be accurate; the Nov-2024 lot size change (50→75 NIFTY, 25→35 BANKNIFTY) is baked in
- `_MAX_STALE_MINUTES = 1` in `generic_executor.py` — controls DATA_GAP_EXIT sensitivity; changing affects all generic_v1 strategies and the shadow MTM stale cap in `workbench.py::_compute_shadow_mtm`
- `stop_capital_pct` vs `stop_multiple` in exit_rule — `stop_capital_pct` takes precedence when present; `stop_multiple` is the fallback for backward-compat with old catalog entries. Do not remove the fallback.
- Exit check order in `generic_executor.py` — STOP → TRAIL → TARGET → TIME → DATA_GAP. TARGET is intentionally checked *after* TRAIL and is suppressed when `trail_trigger > 0`. Do not reorder.
- `trail_stop_at_exit` lock in `generic_executor.py` — TRAIL_EXIT P&L is locked at `trail_stop_level`, not the candle close. Removing this causes gap-through slippage to inflate reported losses.

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
SELECT trade_date, backtest_ready, ingestion_status FROM trading_days ORDER BY trade_date DESC LIMIT 10;
SELECT count(*) FROM options_candles;
```

### Ingest historical data
```bash
# Sync catalogue (scan data directory)
curl -X POST http://localhost:8000/api/historical/catalogue/sync \
  -H "Authorization: Bearer <token>"

# Ingest a single day
curl -X POST http://localhost:8000/api/historical/ingest/day/2026-04-07 \
  -H "Authorization: Bearer <token>"

# Bulk ingest all available dates (background)
curl -X POST http://localhost:8000/api/historical/ingest/bulk \
  -H "Authorization: Bearer <token>"
```

### Run a historical backtest batch via API
```bash
curl -s -X POST http://localhost:8000/api/backtests/batches \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"NIFTY Apr replay","instrument":"NIFTY","capital":2500000,"start_date":"2026-04-01","end_date":"2026-04-15","autorun":true}' \
  | python3 -m json.tool
```

### Run a workbench paper replay via v2 API
```bash
curl -s -X POST http://localhost:8000/api/v2/runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"run_type":"paper_replay","strategy_id":"orb_intraday_spread","config":{"instrument":"NIFTY","date":"2026-04-07","capital":2500000}}' \
  | python3 -m json.tool
```

### Validate a Short Straddle session (dry-run, no DB writes)
```bash
curl -s -X POST http://localhost:8000/api/v2/runs/validate \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"run_type":"single_session_backtest","strategy_id":"short_straddle","config":{"instrument":"NIFTY","trade_date":"2026-04-07","entry_time":"09:50","capital":500000}}' \
  | python3 -m json.tool
```

### Run a Short Straddle single-session backtest
```bash
curl -s -X POST http://localhost:8000/api/v2/runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"run_type":"single_session_backtest","strategy_id":"short_straddle","config":{"instrument":"NIFTY","trade_date":"2026-04-07","entry_time":"09:50","capital":500000,"vix_guardrail_enabled":true,"vix_min":14,"vix_max":22}}' \
  | python3 -m json.tool
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

### Run a paper trading session via legacy API
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
- Animated minute-by-minute replay player (PRD Phase 1 gap — next workbench milestone)
- Adjustment Lab (PRD Phase 2)
- Expiry-cycle backtests (PRD Phase 3)

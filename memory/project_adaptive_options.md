---
name: Adaptive Options Project — current state
description: Full-stack NSE options backtesting + paper trading platform. Six modules including Live Paper Trading, Railway-deployed, CI on GitHub Actions.
type: project
---

Full-stack NSE index options platform. Docker Compose locally; Railway (3 services) in production.
Frontend: React 18 + Vite at localhost:3000. Backend: FastAPI at localhost:8000. DB: PostgreSQL 15.

**Why:** Educational backtesting and paper trading for Nifty/BankNifty strategies. No live order placement.

**How to apply:** When suggesting changes or additions, check whether the work fits inside an existing executor (generic_v1 just needs a catalog entry) before proposing new files. For strategies with configurable wing widths, use `strike_offset_steps_from_config` + `strike_offset_sign` in leg_template — the executor resolves the offset from `config[key] × sign` at runtime.

---

## Six Modules

1. **V2 Workbench** — primary UI (Strategy Catalog → Run Builder → Replay Analyzer → Runs Library). Two live executors: `orb_v1` (ORB paper/historical) and `generic_v1` (generic single-session backtest). Route: `/workbench/...`

2. **Generic Strategy Engine** — `generic_executor.py`. Any strategy expressed as `leg_template + entry_rule_id + exit_rule` in `workbench_catalog.py` runs with zero custom code. Short Straddle and Iron Butterfly are live; 10 more catalogued.

3. **Historical Backtest** — batch-runs against a warehoused 1-min candle DB. Multi-day. Results accessible from Runs Library.

4. **Paper Trading ORB Replay** — live Zerodha candle data, G1–G7 gate stack, Bull Call / Bear Put spreads. Zerodha tokens expire daily at 6 AM IST.

5. **Synthetic Backtest** — deterministic RNG (seed = MD5(date+instrument)), Iron Condor / Bull Put / Bear Call via EMA/RSI/IV Rank regime detection. Legacy module.

6. **Live Paper Trading** — self-driving intraday engine (`live_paper_engine.py`) for Short Straddle Dual Lock on live Zerodha data. APScheduler fires at 09:14 IST weekdays. UI at `/workbench/live` (`LivePaperMonitor.jsx`) is SSE-only viewer. Sessions write to `strategy_runs` (run_type=`live_paper_session`) so ReplayAnalyzer works unchanged. Two new tables: `live_paper_configs`, `live_paper_sessions`.

---

## Strategy Replay V2 (feat/replay-v2, merged into main)

Upgraded `strategy_run` replay into a full trade diagnosis screen:

- **Serializer** (`strategy_replay_serializer.py`): CE/PE MTM split (`ce_mtm`/`pe_mtm` per minute), VIX forward-fill with `vix_source` tagging, MFE/MAE/max_drawdown in `run`, `data_quality` warnings, full OHLC in `spot_series_full`, `lots`/`lot_size` in legs. 19 unit tests in `test_strategy_replay_serializer.py`.
- **Replay JSON payload** (`GET /api/v2/runs/strategy_run/{id}/replay`): spot OHLC full day, VIX full day, CE/PE MTM series, leg_candles OHLC, shadow MTM.
- **CSV export** (`GET /api/v2/runs/strategy_run/{id}/replay/csv`): 9-section human-readable CSV with UTF-8 BOM. Sections: Trade Summary, Execution Summary, Contracts, MTM Series, CE Premium, PE Premium, NIFTY Spot OHLC, India VIX, Decision Log.
- **Bundle export** (`POST /api/v2/runs/strategy_run/export-bundle`): multi-run. ≤20 → single stacked CSV; >20 → ZIP. Raises 404 if any IDs not found/owned. Filename: `{strategy}_{from}_to_{to}_bundle.{ext}`. ZIP entries unique via `_{id[:8]}`.
- **Runs Library** (`RunsLibrary.jsx`): compare panel removed (user request). Checkbox multi-select on strategy_run rows only. Select-all with indeterminate state. Export button appears after selection; turns blue+ZIP for >20 runs. Status bar shows "N selected (M visible)" + Clear button.
- **Single source of truth**: `_build_strategy_run_replay_payload()` in `workbench.py` is called by both JSON and CSV endpoints. `_write_run_sections_to_csv()` is called by both single-run and bundle endpoints — no drift.

---

## Live Paper Trading (PR #29, feat/live-paper-trading)

- New files: `backend/app/models/live_paper.py`, `backend/app/services/live_paper_engine.py`, `backend/app/services/scheduler.py`, `backend/app/routers/live_paper.py`, `frontend/src/pages/LivePaperMonitor.jsx`
- Token bypass: `POST /api/auth/zerodha/token` stores access_token directly (for dev when OAuth redirect can't complete)
- Key bug fixed: all timestamps inserted into `strategy_run_events`/`strategy_run_mtm`/`strategy_leg_mtm` must use `.replace(tzinfo=None)` — those columns are `TIMESTAMP(timezone=False)`
- Recovery: `check_and_resume_sessions()` re-launches interrupted sessions on startup but only for status `waiting`/`entered`; error sessions must be manually reset to `waiting` in DB

**Why:** First live market data execution; positions this as the final step before flip to live orders.
**How to apply:** When debugging live paper issues, always check (1) token validity, (2) session status in DB, (3) whether the running task holds a stale token (restart backend to reload fresh token).

---

## Iron Butterfly (PR #28, pending merge)

- 4-leg defined-risk neutral strategy: SELL ATM CE/PE + BUY OTM CE/PE wings
- Wing width is runtime config (`wing_width_steps`) via `strike_offset_steps_from_config` in leg_template
- MTM formula is sign-aware: SELL `entry−current`, BUY `current−entry`
- Margin sizing via `_defined_risk_margin_per_lot()` (wing_width × step × lot_size − net_credit)
- Exit charges flip correctly: closing BUY leg = SELL order, closing SELL leg = BUY order
- `ce_mtm`/`pe_mtm` sum ALL legs of that option_type (SELL + BUY) — serializer unchanged
- 8 new focused tests covering catalog, wing resolution, MTM signs, charges, CSV

---

## Test Counts (current, after IB merge)

- Backend: **~237 passed** (229 baseline + 8 IB tests)
- Frontend: **49 passed** (`npm test`)

---

## Key File Locations

- Strategy catalog + visual_hints: `backend/app/services/workbench_catalog.py`
- Replay serializer: `backend/app/services/strategy_replay_serializer.py`
- Workbench router (CSV, bundle, replay): `backend/app/routers/workbench.py`
- Generic executor: `backend/app/services/generic_executor.py`
- Live paper engine: `backend/app/services/live_paper_engine.py`
- Live paper scheduler: `backend/app/services/scheduler.py`
- Live paper router: `backend/app/routers/live_paper.py`
- Live paper UI: `frontend/src/pages/LivePaperMonitor.jsx`
- Replay UI: `frontend/src/pages/ReplayAnalyzer.jsx`
- Runs Library: `frontend/src/pages/RunsLibrary.jsx`
- API wrappers: `frontend/src/api/index.js`

---

## Repository Notes

- `Adaptive_options/` has its own embedded `.git` repo — commits go inside it, not the outer Claude-test repo.
- Main branch: `main`. Feature branches merged via PR. CI gates every PR (pytest + vitest).
- After Python changes: `docker compose build --no-cache backend && docker compose up -d --force-recreate backend`
- After JS changes: `docker compose build --no-cache frontend && docker compose up -d --force-recreate frontend`
- If 502 after restart: `docker compose restart frontend` (nginx may have cached old backend IP).

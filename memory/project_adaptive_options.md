---
name: Adaptive Options Project
description: Full-stack options backtesting + paper trading MVP. Docker Compose, FastAPI + React, runs at localhost:3000
type: project
---

Full-stack NSE options platform with two independent modules:

## Backtest Module (synthetic data)
- Single-day, regime-based strategy (10 regimes, signal engine, R-multiple stops)
- Synthetic candle generator (deterministic RNG from date+instrument)
- EMA9/21/50, RSI14, ATR14 indicators
- Frontend: Dashboard (results table with regime_detail, signal_type, signal_score, atr14, r_multiple), TradeBook (per-day drill-down)

## Paper Trading Module (real Zerodha data)
- ORB (Opening Range Breakout) strategy — PRD v2 logic
- G1–G7 gate stack evaluated every minute from 09:30
- Bull Call Spread (bullish) / Bear Put Spread (bearish)
- Position sizing: spread_debit × lot_size, max 2% capital risk, 0.5% target
- Full minute audit log: ~375 rows/session in strategy_minute_decisions table
- Access token passed per-request (never stored), expires 6 AM IST daily
- Expiry resolution: scans instruments master for actual nearest expiry >= trade_date (handles expired contracts and holiday shifts)
- Frontend: /paper (form), /paper/sessions (list), /paper/session/:id (audit log + MTM chart)

## Key technical notes
- Zerodha tokens expire daily at 6 AM IST — user must get fresh token via /api/auth/zerodha/login-url flow
- April 10, 2026 = NSE holiday (no spot data); April 9 = last Thursday expiry for that week
- docker-compose uses .env file for ZERODHA_API_KEY / ZERODHA_API_SECRET (never committed)
- Adaptive_options/ has its own embedded .git repo — commits go inside that repo, not the outer Claude-test repo

## DB tables
- backtest_sessions (existing)
- paper_sessions, strategy_minute_decisions, paper_trade_headers, paper_trade_minute_marks, paper_trade_legs (paper trading)

## Running
cd Adaptive_options/ && docker compose up -d
Frontend: localhost:3000 | Backend: localhost:8000 | DB: localhost:5432

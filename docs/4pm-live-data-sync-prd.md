# 4 PM Live Data Warehouse Sync PRD

## Objective

Productize an automated 4 PM IST backend sync that stores the current trading day's NIFTY market data in the existing historical warehouse, then surfaces read-only sync status on the Live Paper page.

The sync must be backend-owned. It must not depend on a frontend button, a new warehouse module, live order placement, or any change to strategy/backtest logic.

## Scope

- Run automatically at 16:00 IST every weekday.
- Validate that a current-day Zerodha token is available before fetching data.
- Sync the current day's NIFTY spot 1-minute candles.
- Sync India VIX 1-minute candles.
- Sync NIFTY near-month futures 1-minute candles.
- Sync NIFTY options for the first three valid expiries on or after the trade date.
- Persist every sync attempt in an audit table.
- Expose today's latest sync status through the Live Paper API.
- Display a compact read-only status card on `/workbench/live`.
- Preserve the existing 09:14 live-paper scheduler behavior.

## Out Of Scope

- Live order placement.
- New strategy logic.
- Backtest engine changes.
- Manual retry button.
- Admin UI for choosing a system token.
- BankNifty or other symbols.
- Update/upsert semantics beyond existing conflict-safe warehouse inserts.

## Backend Requirements

### Scheduler

Add a second APScheduler job:

- Job ID: `daily_live_data_ingestion`
- Cron: Monday-Friday at 16:00 IST
- Misfire grace: 900 seconds
- Entrypoint: `run_daily_live_data_sync()`

The existing `daily_live_paper` 09:14 IST job must remain unchanged.

### Token Handling

The sync is system-owned, while broker tokens are user-scoped. The orchestration should use the latest available Zerodha token for the current IST trade date.

Required token states:

- `SKIPPED_TOKEN_MISSING`: no Zerodha token row exists.
- `SKIPPED_TOKEN_EXPIRED`: latest token is older than the IST trade date.
- `FAILED_TOKEN_DECRYPTION`: encrypted token cannot be decrypted.
- `FAILED_TOKEN_VALIDATION`: Zerodha rejects the decrypted token.
- `VALID`: token decrypts and validates successfully.

The decrypted token must never be returned through an API response or written to logs.

### Live Ingestion

Reuse and extend the existing live ingestion service.

Required warehouse writes:

- `spot_candles`
- `vix_candles`
- `futures_candles`
- `options_candles`
- `trading_days`

Futures selection:

- Fetch the NFO instrument master.
- Filter to `name == "NIFTY"` and `instrument_type == "FUT"`.
- Keep expiries on or after the trade date.
- Select the nearest expiry.
- Insert through the existing conflict-safe bulk insert path.

Options expiry selection:

- Use the NFO instrument master.
- Select the first three distinct valid NIFTY option expiries on or after the trade date.
- Do not hardcode expiry weekdays.

Readiness rule:

- `backtest_ready = spot_available AND options_available`.
- VIX and futures failures should not block backtest readiness.

### Audit Table

Add `live_data_sync_runs` with:

- trade date
- start and completion timestamps
- trigger source
- token status
- sync status
- spot, VIX, futures, and options row counts
- option contract count
- expiries
- failed item list
- notes
- error message

Required sync statuses:

- `STARTED`
- `SUCCESS`
- `PARTIAL_SUCCESS`
- `FAILED`
- `SKIPPED_TOKEN_MISSING`
- `SKIPPED_TOKEN_EXPIRED`
- `FAILED_TOKEN_DECRYPTION`
- `FAILED_TOKEN_VALIDATION`

## API Requirement

Add:

```http
GET /api/v2/live-paper/data-sync/today
```

Response should include:

- trade date
- scheduled time
- status
- token status
- backtest readiness
- last attempt time
- completion time
- row counts
- option contract count
- expiries
- notes
- error message

If today's sync has not run, return `NOT_RUN` with current token status and zero row counts.

## Frontend Requirement

On `/workbench/live`, add a compact read-only `Data Warehouse Sync` section near the live-paper token and scheduler area.

It should show:

- sync status
- scheduled time
- last attempt
- token status
- backtest readiness
- row counts for spot, VIX, futures, options
- option contract count
- selected expiries
- notes or error message

Status styling:

- success: green
- partial/skipped: amber
- failed: red
- not run: neutral
- started: blue

## Acceptance Criteria

- The sync runs automatically at 4 PM IST on weekdays.
- Missing, expired, undecryptable, or invalid Zerodha tokens are audited and visible in the Live page status.
- NIFTY spot, VIX, futures, and option candles are inserted into the existing warehouse tables.
- Option expiries are selected from the instrument master, not hardcoded weekdays.
- Re-runs do not create duplicate warehouse rows.
- A day is backtest-ready only when spot and options are available.
- The Live page shows sync status without requiring user action.
- Existing live-paper session scheduling remains unchanged.

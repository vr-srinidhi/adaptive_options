# Spec: Option Order-Book Depth Capture

Version: 1.0
Date: 29 August 2026
Status: In Review (PR #66)
Scope: `live_paper_engine` capture path only — **no strategy, sizing, or execution behaviour changes**
Implements: `execution-realism-plan.md` §5 Phase A
Related: `delta-hedge-sizing-spec.md` (previous change to the same engine)

## Problem Statement

Every P&L number this system has produced assumes **we transact at the price we observe**. `live_paper_engine` records `straddle_entry_prices = [s_ce_price, s_pe_price]` — a quote, treated as a fill. The backtest does the same with the candle close.

Real fills happen at the **bid** when selling and the **ask** when buying. That spread is paid on every leg of every session — 4 to 10 legs a day. It has never been measured, so the strategy's edge is overstated by an unknown amount.

On a strategy whose full live-paper record is **−₹30,946 over 71 days** with a −₹3,21,059 max drawdown, an unmeasured execution cost is not a rounding detail. It could be the whole result.

Two facts make this urgent rather than merely desirable:

1. **Zerodha does not serve historical depth.** A session that is not captured live can never be analysed later. Every day without capture is permanently unmeasurable.
2. **We are already receiving it and throwing it away.** `kite.quote()` returns a 5-level ladder; `fetch_live_quote` reads `last_price` and discards the rest.

## Goals

- Record what was actually on offer at each decision minute, for every option the engine quotes.
- Add **zero** Zerodha API calls.
- Make it impossible for capture to delay or break a trading session.
- Ship before the next trading session, so the A/B trial is retrospectively measurable.

## Non-Goals

- Modelling fills, partial fills, or rejections (Phase C).
- Changing how any P&L figure is currently computed. Captured data is **not** read by any trading path.
- Real order placement (Phase E, behind its own review).
- Backfilling history. It does not exist and cannot be obtained.

## What Changed

| File | Change |
|---|---|
| `models/option_depth.py` | **new** — `OptionDepthSnapshot` |
| `services/depth_capture.py` | **new** — bounded queue, background writer, validation |
| `services/zerodha_client.py` | `fetch_quote_with_depth()`; `fetch_live_quote` untouched |
| `services/live_paper_engine.py` | **+15/−4** — the only change to the live path |
| `main.py` | flush the writer on shutdown |
| `database.py` | register the model |

## Core Design

### No additional API calls

`fetch_quote_with_depth(symbols, access_token)` issues the **same** `kite.quote()` request with the same symbol list, and returns both the price map the engine already used and the raw payload it previously discarded.

```
returns (Dict[str, float], Dict[str, dict])
         prices as before   raw quote incl. depth ladder
```

`fetch_live_quote` is left byte-for-byte unchanged; nothing else that calls it is affected.

This matters beyond politeness: Kite's quote rate limit is **unverified** (believed ~1/sec), and next week goes from 2 enabled slots to 4 — from 0.6 to 1.2 calls/sec. Adding capture-driven calls on top of an unverified limit risks throttling the **real** slots, which is the exact failure isolation is meant to prevent.

### Capture cannot delay trading

The trading loop calls `capture_depth()`, which is **synchronous and non-blocking**: it puts one item on a bounded queue and returns. A single background task drains it.

```python
capture_depth(opt_raw, now, trade_date, meta={...}, expiry_date=..., session_id=...)
```

Rejected: `asyncio.create_task(persist_depth(...))` per poll. This was the shipped first draft and it is **not** safe. Task-per-poll is unbounded — under a slow database, writers accumulate without limit, and because they share the trading connection pool (default 5 + 10 overflow) they can starve hedge and square-off writes.

**One writer is the load-bearing property:** analytics holds **at most one** pooled connection no matter how slow the database becomes, rather than a number that grows with elapsed time.

### Drop, never block

Queue bound is 500 items — roughly 20 minutes of backlog at 4 rows per 10s poll. When full, the snapshot is **dropped** and logged (throttled to every 50th drop, so a stalled database cannot flood the log).

This is a deliberate trade: **losing analytics is always preferable to delaying a trade.**

### Strike and type are passed in, not parsed

The engine resolves every strike from the instruments master before it quotes:

```python
ce_symbol = find_option_symbol(instruments, instrument, expiry_date, "CE", atm_strike)
```

So `strike`, `option_type` and `expiry_date` are handed to `capture_depth()` via `meta`. Re-deriving what the caller already knows is strictly worse, and was the source of the bug below.

Parsing survives only as a fallback for symbols without metadata, and must handle **both** NSE formats:

```
monthly   NIFTY25AUG24300CE     ->  yy=25  mon=AUG    strike=24300
weekly    NIFTY2690124300CE     ->  yy=26  m=9 dd=01  strike=24300
```

The weekly form runs the expiry straight into the strike with no separator. **A naive trailing-digit match (`(\d+)(CE|PE)$`) yields 2690124300** — a wrong strike that also exceeds PostgreSQL `INTEGER` and fails the insert. Since the strategy trades the current weekly expiry, this would have fired on the first live session.

Rules:
- Monthly is tried **first** — its three-letter month is unambiguous, whereas the weekly pattern can mis-split a monthly symbol.
- Weekly month codes are `1`–`9` for Jan–Sep, then `O`, `N`, `D`.
- Any strike outside `INTEGER` range is rejected (stored `NULL`), never passed to the driver.

### One bad quote costs one row

Each quote is validated into a row individually; invalid ones are skipped with symbol-specific logging, and only the validated set is committed. Every field is bounded to its column: strike and quantities to `INTEGER`, volume/OI to `BIGINT`, prices to `Numeric(12,2)` with `NaN` and infinity rejected.

If the batch commit still fails, rows are retried **individually** so only the offending row is lost. That path should be unreachable after validation; it exists so the documented failure mode is true rather than approximately true.

## Schema

`option_depth_snapshots`:

| Column | Type | Note |
|---|---|---|
| `trade_date` | Date | |
| `timestamp` | DateTime (naive) | column is `timezone=False` |
| `symbol` | String(60) | e.g. `NFO:NIFTY2690124300CE` |
| `strike` | Integer | from `meta`; parse fallback |
| `option_type` | String(5) | `CE` / `PE` |
| `expiry_date` | Date | passed in by the engine |
| `last_price` | Numeric(12,2) | what P&L currently assumes |
| `bid`, `ask` | Numeric(12,2) | what a real fill actually uses |
| `bid_qty`, `ask_qty` | Integer | size at the touch |
| `depth_json` | JSONB | full 5-level ladder, for the Phase C fill model |
| `volume`, `open_interest` | BigInteger | context |
| `session_id` | String(40) | which slot captured it; **not** an FK, so deleting a session never blocks on market data |

Index: `ix_option_depth_date_symbol_ts (trade_date, symbol, timestamp)`.

Volume: 4 symbols × 6/min × 375 min ≈ **9k rows per slot per day**. Acceptable; retention revisited after a month.

## Testing

`tests/test_depth_capture.py` — 21 tests:

| Area | Cases |
|---|---|
| Symbol parsing | monthly, weekly, `O`/`N`/`D` month codes, BANKNIFTY, bare (no `NFO:`) prefix, unparseable, `INTEGER` overflow |
| Top of book | best bid/ask extraction, full ladder retained, missing ladder tolerated |
| Persistence | metadata precedence over parsing, non-numeric fields dropped not raised, empty payload no-op |
| Isolation | malformed quote does not lose valid rows; batch-commit failure isolates the bad row; dead pool returns 0 without raising |
| Queue | writer drains and receives the payload, drop-on-full returns `False` without raising, no-running-loop no-op, idempotent shutdown |

`tests/test_live_paper_engine.py` now **asserts** the capture call and its payload — including that the engine hands over its known strikes and expiry. The original mock asserted nothing and would have passed if capture never ran.

**Full suite: 572 passing.**

### Mutation verification

Green tests were not trusted on their own. Each of these mutations **fails** the suite:

| Mutation | Result |
|---|---|
| Restore the old trailing-digit regex | 3 tests fail |
| Ignore caller `meta`, always parse | fails |
| Remove the `INTEGER` bound on strike | fails |
| Remove drop-on-full | fails |
| Remove batch-failure row isolation | fails |
| Remove per-row isolation (one bad quote aborts poll) | fails |

One mutation deliberately **survives**: making `_build_row` raise on a non-dict quote. Its blanket guard catches that too and the row is skipped either way — an equivalent mutant with identical observable behaviour, recorded here so the survivor is not later mistaken for a coverage gap.

*Method note:* an initial mutation run reported all mutants surviving. That was a harness fault — `docker exec` without `-i` does not forward stdin, so the mutations silently never applied. Mutation results are only meaningful once the mutated file is confirmed on disk.

## Verification Performed

End-to-end against local Postgres, single event loop, through the real queue and writer:

| Input | Result |
|---|---|
| `NFO:NIFTY2690124300CE` (weekly) | strike **24300** — previously 2690124300, which would have failed the insert |
| `NFO:NIFTY25AUG24200PE` (monthly, meta supplied) | strike 24200, expiry stored |
| `"malformed"` (non-dict) | skipped with symbol-specific log; **both** valid rows still written |
| Writer shutdown | flushed and stopped cleanly |

Observed spread on the captured rows: **0.70 on a 65.80 option = 1.06% of premium.**

## What Is Not Proven

**This has never run against a live market.** All verification is against synthetic payloads and the local database. First real proof is the next live session.

Until then, any spread or execution-cost figure derived from this table is **unconfirmed**, and Phase B's inputs should be treated as provisional.

## Open Questions for Review

1. **Retention.** 9k rows/slot/day × 4 slots is ~36k rows/day. No policy set; revisit after a month of real data.
2. **Latency is still unmodelled.** Depth is captured at decision time, but a real order fills some milliseconds later. On a fast move that gap can exceed the spread. Phase C question, noted here so it is not forgotten.
3. **Rate limit still unverified.** This change adds no calls, so it does not depend on the answer — but Phase C's shadow poller would, and the number should be confirmed before that design is accepted.

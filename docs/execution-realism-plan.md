# Plan: Execution Realism and the Virtual Broker

Version: 0.2
Date: 29 August 2026
Status: §4 decided, Phase A built. Phases B–E still proposal.
Scope: **Additive only.** No change to `short_straddle_dual_lock`, `live_paper_engine`, or the four A/B slots — with one exception called out in §4, which needs an explicit decision.

## 1. Problem

Every P&L number this system has ever produced assumes **we transact at the price we observe**. `live_paper_engine` records `straddle_entry_prices = [s_ce_price, s_pe_price]` — a quote, treated as a fill. The backtest does the same with the candle close.

Real execution differs in three ways:

1. **Spread.** You sell at the bid and buy at the ask. Paid on every leg, every session — 4 to 10 legs a day.
2. **Depth.** Only so many contracts sit at the touch; the rest fill worse.
3. **Rejection and partial fill.** Orders fail or fill incompletely.

None of this is currently modelled, so the strategy's measured edge is systematically overstated by an unknown amount. On a strategy whose full live-paper record is **−₹30,946 over 71 days**, an unknown execution cost is not a rounding detail — it could be the whole result.

## 2. What was verified before writing this

Facts established by inspection, not assumption:

| Claim | Finding |
|---|---|
| Is order size a problem? | **No.** 975 qty vs 0.4–6.2M contracts traded per minute on the ATM strike — **0.0–0.2% of one minute's volume.** We will not sweep the book. |
| Do we have depth data? | **No.** `options_candles` stores OHLC/volume/OI/ltp. No bid, ask, or depth. |
| Can we get it historically? | **No.** Zerodha does not serve historical depth. It must be captured live or not at all. |
| Are we already receiving it? | **Yes.** `kite.quote()` returns 5-level depth; `fetch_live_quote` reads `last_price` and discards the rest. |
| Is the broker API available? | **Yes.** `kiteconnect` exposes `place_order`, `orders`, `positions`, `margins`. This repo uses none of them. |
| Is live execution wired at all? | **No.** `execution_mode='live'` raises HTTP 422 at `live_paper.py:358` and `:584`. There is zero order-placement code. |
| Evidence of API throttling today? | **None.** No 429s in 7 days of production logs; zero `MISSING_LEG_PRICE` or `DELTA_HEDGE_FAILED` events since 24 Aug. |

**Conclusion: the realistic cost is spread, not market impact.** That reframes the work — we are measuring a per-leg tax, not modelling book exhaustion.

## 3. Isolation

Frozen, byte-for-byte: `straddle_adjustment_executor.py`, `live_paper_configs`, `live_paper_sessions`, `strategy_runs` and children, the `short_straddle_dual_lock` catalog entry, and the 09:14 scheduler job.

New and additive: a `app/services/execution/` package, new tables, a new scheduler job, and a new strategy id for shadow runs. The shadow **never places an order and never writes to the live tables**, so no failure mode in it can affect a real slot.

## 4. The one decision needed: where depth gets captured

Each enabled slot makes **three** `quote()` calls per 10s poll — spot, VIX, options.

| Configuration | Quote calls/sec |
|---|---|
| Today (2 slots enabled) | 0.6 |
| Next week (4 slots, the A/B) | 1.2 |
| Plus an independent shadow | **2.4** |

Kite's quote endpoint is rate-limited (documented figure to be confirmed — believed ~1/sec). We have no evidence of throttling today, but today is 0.6/sec; 2.4/sec is four times the only load ever tested. **If throttling occurs it degrades the real slots** — missed hedges, missed square-off — which is exactly the outcome isolation is meant to prevent.

Three options:

**(a) Capture inside the existing poll.** ~5 additive lines in `live_paper_engine`: keep the depth already arriving in the reply and write it to a new table. **Zero extra API calls.** Touches the frozen file.

**(b) Independent shadow poller.** True isolation, but doubles API load against an unverified limit.

**(c) Consolidate first, then (b).** The engine's three `quote()` calls could be **one** — `quote()` accepts a mixed list of symbols across segments. That alone cuts load 3×, creating ample headroom. Bigger change to the frozen file, but leaves the system better than it found it.

**Recommendation: (a).** Smallest possible change to the live path, no added API load, and it starts collecting immediately. (c) is attractive but is an optimisation of working code before next week's trial — wrong time.

**Decided: (a),** approved 29 Aug. Implemented as +15/−4 lines in `live_paper_engine`. (c) remains available later if the shadow poller in Phase C needs headroom.

## 5. Phases

Merged per the revised approach: the shadow fills from **live** depth, so it does not need a history to operate. Measurement therefore comes free with the build rather than as a separate gate.

### Phase A — Depth capture — **BUILT** *(29 Aug)*

- `fetch_quote_with_depth()` in `zerodha_client` (additive; existing `fetch_live_quote` untouched).
- Table `option_depth_snapshots`: `trade_date, timestamp, symbol, strike, option_type, last_price, bid, ask, bid_qty, ask_qty, depth_json, volume, open_interest, session_id`.
  - `expiry_date` and `captured_by` from the v0.1 sketch were dropped: expiry is derivable from `symbol`, and `session_id` identifies the capturing slot more usefully than a free-text label. `volume`/`open_interest` were added since the payload already carries them.
- Written from the existing poll, per §4(a), via `asyncio.create_task` — fire-and-forget, so capture can never delay the trading loop.
- Every failure path in `depth_capture.py` swallows and returns `None`. Verified with a malformed payload.
- Retention: full depth is verbose — 4 symbols × 6/min × 375 min ≈ 9k rows/slot/day. Acceptable; revisit after a month.

**Deliverable:** every trial session from Monday is retrospectively analysable. This was the only time-critical phase.

**Not yet proven:** capture has been verified against a synthetic payload and the local DB, but has **never run against a live market session**. First real proof comes Monday; until then treat Phase B's inputs as unconfirmed.

### Phase B — Measurement *(continuous, no build)*

From captured data, answer:
- Spread as % of premium at 09:50 / 10:15, and how it moves into 15:25.
- Lots resting at the touch versus our 13.
- Whether spread widens materially on the hedge strikes when they are moving.

**Exit criterion:** a single number — expected execution cost per session. If that is small relative to typical P&L, Phase C is informational; if large, it is the most important work in the project.

### Phase C — Virtual broker + shadow engine

```
app/services/execution/
  broker_interface.py    # abstract: place / cancel / status / positions
  virtual_broker.py      # fills from captured depth; partial fills; rejections
  fill_model.py          # walk the book -> quantity-weighted average price
  shadow_engine.py       # mirrors the strategy, routes through the broker
```

New tables `virtual_orders`, `virtual_fills`. New scheduler job. New strategy id (e.g. `short_straddle_dual_lock_shadow`) so shadow runs are never confused with live ones.

**Design constraint:** the shadow mirrors the *same decisions* as the paper slot rather than re-deriving them independently. Only then does the difference isolate execution cost rather than confounding it with different triggers. How to guarantee identical decisions without coupling to the live engine is an open question — see §7.

### Phase D — Leg sequencing *(deferred, but this is the safety phase)*

Encodes the protocol discussed:
- Ordered leg sequences: protection bought first, sold last.
- **Exit rule: buy back shorts before selling longs.** A failure then leaves long options (defined risk) rather than naked shorts.
- Per-leg fill timeout with automatic unwind of already-filled legs.
- NSE freeze-quantity splitting (975 qty may exceed the per-order cap and require splitting, which multiplies leg count and therefore leg risk).

**Note the strategy-level gap:** this strategy *enters naked* — both entry legs are SELLs, so there is no protective leg to buy first. Ordering cannot fix entry; only abort-and-unwind, or changing entry to a butterfly (buy wings first), removes the exposure. That is a strategy decision, not an execution one.

### Phase E — Real orders, 1 lot

Swap `virtual_broker` for `zerodha_broker` behind the same interface. Nothing else changes. Sizing 1 lot, not 13.

## 6. How the fill model gets validated

The uncomfortable part, stated plainly: **until Phase E there is no ground truth.** A simulator calibrated on captured depth is a model, and a confident wrong model is worse than none because it will be trusted.

Partial mitigations:
1. Bound it — compare fills at bid/ask against fills at mid. The truth lies between; if both bounds are tolerable, precision does not matter.
2. Sanity-check the shadow's implied spread cost against the raw spread observed in Phase B.
3. At Phase E, log predicted versus actual fill on every real order and report the error. That is the only real validation, and it arrives last.

Until then, shadow output should be quoted as a **range**, never a point estimate.

## 7. Open questions

1. ~~**§4 decision**~~ — resolved: (a), built.
2. **Decision mirroring** — how does the shadow guarantee identical decisions to the paper slot without coupling to it? Options: re-run the same logic from the same inputs, or have the paper slot emit a decision stream the shadow consumes. The latter is cleaner but touches the frozen file.
3. **Confirm Kite's quote rate limit** before any design that increases call volume.
4. **Retention policy** for depth snapshots beyond one month.
5. Should the shadow also model **latency** — the gap between decision and fill? Real orders are not instantaneous, and on a fast move that gap can exceed the spread.

## 8. Explicitly out of scope

- Any change to strategy logic, thresholds, or sizing.
- Real order placement (Phase E only, and behind its own review).
- The `expiry_offset` experiment — parked, per the 24 Aug analysis.
- Alerting and operational hardening. **Separately urgent:** the Zerodha token expired silently on five days this month, killing both data sync and trading, with no notification. That is a bigger near-term risk to live trading than execution modelling and deserves its own ticket.

## 9. Immediate actions

1. ~~Decide §4~~ — done.
2. ~~Build Phase A~~ — done; needs deploying to production before Monday to be of any use.
3. **Re-enable the two FULL slots** — both are currently `enabled = false` and will not run.
4. **Rename the slots.** `9:50 Delta Next Exp` and `10:15 Delta Next Exp` now run `expiry_offset = 0` with `hedge_qty_mode = FULL`. The labels describe an experiment that no longer exists and will mislead any later analysis.

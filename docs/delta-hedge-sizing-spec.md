# Spec: Delta Hedge Sizing for AdaptiveOptions

Version: 1.0
Date: 23 August 2026
Status: In Review (PR #65)
Scope: `short_straddle_dual_lock` only
Supersedes: sizing behaviour implied by `delta-hedge-trigger-prd.md` §Configuration

## Problem Statement

The Delta Hedge Trigger shipped with a `hedge_qty_mode` setting offering `PARTIAL` and `FULL`. The value is parsed into `DeltaHedgeSettings`, surfaced as a dropdown in the Live Monitor, defaulted to `PARTIAL` in the strategy catalog, and asserted in a unit test. **No execution path ever reads it.** Both the live engine and the backtest executor size every hedge as `lot_size * approved_lots` — the full straddle size — regardless of how small the delta imbalance is.

The consequence is that the hedge routinely overshoots delta-neutral and inverts the position's direction.

Worked example, live session 12 Aug 2026 (run `a238160f`):

| | |
|---|---|
| Net delta at trigger | +152.05 (threshold 150) |
| Hedge bought | 13 lots of 24300 PE @ 110.70 |
| Net delta after hedge | **−293.44** |
| Spot then | rallied ~96 points into the close |
| Session result | −Rs 30,208 |

The correction needed roughly 4 lots. The engine bought 13. A position that was mildly long became firmly short at the day's low, and the reversal did the rest. NIFTY closed 41 points from the strike we sold — a textbook short-straddle win that finished as a loss.

Across 62 live trading days since 10 May 2026, the core straddle earned +Rs 9,11,138 in gross leg P&L while the delta hedge gave back −Rs 5,73,885 of it, and turned 8 otherwise-winning days into losses.

## Goals

- Size each hedge to the actual delta imbalance rather than the full position.
- Make it structurally impossible for a hedge to invert the book's direction.
- Make `hedge_qty_mode` mean what the UI already claims it means.
- Keep the live engine and the backtest executor in agreement, so replays predict live behaviour.
- Change nothing for any strategy other than `short_straddle_dual_lock`.

## Non-Goals

- Changing when the hedge fires. Threshold, cooldown, re-entry buffer and max-trigger logic are untouched.
- Changing the hedge instrument. It still buys the OTM wing on the tested side; ratio and debit-spread hedges are out of scope (see Rejected Alternatives).
- Fixing the stale ATM strike on later slots. Tracked separately; see Out of Scope.
- Any change to the profit lock, loss lock, trail, or stop rules.

## Core Rule

```
PARTIAL:  lots = floor( |net_delta| / (|option_delta| * lot_size) )
          clamped to [0, approved_lots]
FULL:     lots = approved_lots        (previous behaviour, retained)
```

`option_delta` is the signed per-unit Black-Scholes delta of the hedge instrument at the trigger minute, from the existing IV chain (BS inversion → VIX proxy → `default_iv`).

**Rounding down is load-bearing, not a detail.** Flooring guarantees the residual delta keeps the sign it started with, so the hedge can approach neutral but never cross it. Round-to-nearest would reintroduce the inversion this spec exists to prevent. Verified across gaps from 60 to 5,000.

Applied to 12 Aug: implied per-unit delta −0.4569 → **4 lots**, residual delta **+14.98**, with 31% of the premium committed.

## Undersized Gaps

When the gap cannot fill even one lot, the hedge is skipped. The skip:

- does **not** consume a trigger from `max_hedge_triggers`
- does **not** disarm `delta_reentry_armed`
- emits `DELTA_HEDGE_SKIPPED_UNDERSIZED` **once per episode**, re-armed on `DELTA_NEUTRAL_RESTORED`

Rationale: the condition can persist for hours at a 10-second poll interval, and hedging anyway would overshoot — the exact failure this spec removes. Logging per tick would write unbounded event rows.

`DELTA_HEDGE_TRIGGERED` is emitted only for hedges that actually proceed, so the event log no longer implies an action that never happened.

## Per-Leg Quantity

Hedge legs may now be smaller than the straddle. Every site that prices, charges, marks or persists a hedge leg must read that leg's own size via `_hedge_lots(hedge, approved_lots)`; legacy rows without a size fall back to full, preserving old runs.

Sites covered:

| Area | Live engine | Backtest executor |
|---|---|---|
| Net delta computation | `_compute_net_delta` per-leg qty | same |
| Gross MTM | `_hedge_gross()` | `_hedge_gross()` |
| Entry charges | per hedge leg | per hedge leg |
| Exit charge estimate | `_hedge_exit_charges()` | `_hedge_exit_charges()` |
| Per-leg MTM rows | `_write_mtm(leg_lots=...)` | `leg_mtm_rows` |
| Final P&L and charges | per hedge leg | per hedge leg |
| Persisted `StrategyRunLeg.quantity` | `lot_size * hedge_lots` | `lot_size * hedge_lots` |
| Crash resume | derives lots from `leg.quantity` | n/a |

Charge splitting per leg is exact: `charges_service._charges` is additive across legs (brokerage is per-order, STT/exchange are linear in premium×qty, GST is linear in both), so summing per-leg calls equals a single combined call to within sub-paisa rounding.

Downstream consumers were updated for the same reason: `_compute_shadow_mtm`, `strategy_replay_serializer` (`lots` now derived from the leg, not the run), the Live Monitor payoff chart, and the `DELTA_HEDGE` SSE payload which now carries `lots` and `quantity`.

## Strategy Scoping

Delta hedging is pinned to `short_straddle_dual_lock` via `_DELTA_HEDGE_STRATEGY_ID` in both engines. `straddle_adjustment_v1` is shared with `short_straddle_profit_lock`, which never exposes the feature; if it ever requests hedging, settings are forced off with a warning rather than relying on config discipline.

## Engine Parity Fixes

Found while making the two engines agree. Required for replay results to predict live behaviour.

1. **`STOP_EXIT` now precedes the hedge block in the backtest.** `CLAUDE.md` documented this invariant as holding in both engines; it only ever held in live. The backtest computed `fired_event` after the hedge block and could buy a wing on the minute the position stopped out. `CLAUDE.md` corrected to describe actual behaviour.
2. **Hedge `leg_index` always starts at 4**, matching live. The backtest previously used 2 when wings never locked, so the same trade produced different leg numbering in replay versus live.
3. Null-check parity on `hedge_strike`; removed a dead `net_mtm` mutation; `trigger_count` added to the backtest's `DELTA_HEDGE_EXECUTED` payload.

## Validation

Replay of every backtest-ready day 11 May – 18 Aug 2026 (63 days), single 09:50 slot, Rs 25,00,000, identical config — only `hedge_qty_mode` differs.

**Control:** `FULL` reproduces the 12 Aug live session at −Rs 32,844 against the actual −Rs 30,208; the gap is explained by the live slot entering 09:53 rather than 09:50. The replay tracks live, so the comparison below is meaningful.

| Across 63 days | FULL (today) | PARTIAL (this spec) |
|---|---|---|
| Total net P&L | +Rs 2,23,034 | +Rs 2,71,617 |
| Std deviation, daily | 27,405 | **11,805** |
| Max drawdown | −Rs 1,41,279 | **−Rs 39,264** |
| Return ÷ risk | 0.129 | **0.365** |
| Losing days | 17 | 16 |
| Total lost on those days | −Rs 4,93,494 | **−Rs 1,97,217** |
| Hedge fires / lots committed | 74 / 962 | 100 / **584** |
| Hedge P&L | −Rs 3,19,606 | −Rs 1,29,803 |
| Best single day | +Rs 1,09,251 | +Rs 17,020 |

Behavioural observations:

- **More hedges, less size.** PARTIAL fires more often (100 vs 74) on 39% fewer lots. A right-sized hedge returns delta near zero and re-arms, so it can correct again; an oversized one inverts and sits.
- **Six stop-outs avoided.** On 5, 15, 16, 18 Jun, 9 Jul and 29 May, FULL hit the 1.5% stop between −Rs 37,839 and −Rs 39,924. PARTIAL reached `STOP_EXIT` on none of them; four finished positive.
- **Lock cascade quiets.** FULL locked wings on 55 of 63 days for a net −Rs 24,526; PARTIAL on 43 days for +Rs 32,369.

### How to read the headline number

The +Rs 48,584 total improvement is the residual of +Rs 4,52,729 better on 27 days against −Rs 4,04,145 worse on 20, with 16 days identical because the hedge never fired. **Three days — 2 Jun, 24 Jun, 4 Aug — account for Rs 2,59,923 of everything given up.** That total is sample-dependent and should not be the basis for approval.

The robust claim is the risk profile: comparable return at 2.8× the return-per-unit-risk, with worst drawdown cut by 72%. FULL's best days (+Rs 67,794 / +Rs 97,330 / +Rs 1,09,251) are not premium decay — they are a 13-lot directional bet that happened to be right, and it lost the same way on five days clustered against the stop. A hedge should reduce variance; the oversized version increased it.

**Known cost:** 17 Aug (−Rs 8,350 → −Rs 28,661) is a day the larger hedge genuinely protected better. This will recur; it is the accepted price of not taking a directional bet.

## Testing

`cd backend && python -m pytest tests/ -v` → **389 passing** (main: 387 passing, 1 failing).

- `test_delta_hedge.py` — 12 sizing cases, no mocks: the 12 Aug reconstruction, a parametrised no-sign-flip property across gaps 60→5,000, CE/PE symmetry, zero-lot skip, `max_lots` clamp, near-zero and `None` option delta, `FULL` passthrough, and unit-vs-position delta consistency.
- `test_straddle_adjustment_executor_more.py` — 4 executor cases: hedge leg strictly smaller than the straddle, `FULL` still buys all 13, other strategies on this executor never hedge, and a regression pinning stop-before-hedge.
- `test_strategy_replay_serializer.py` — leg `lots` derived from quantity, not run size.
- `test_live_paper_engine.py` — its main end-to-end test pinned expiry to a fixed May-2026 date while the engine uses `date.today()`, so it had been failing on `main` every day since. Now relative.

## Rejected Alternatives

**Round to nearest instead of down.** Reintroduces sign flips on any gap that rounds up. The whole guarantee rests on flooring.

**Hedge only the excess above the threshold.** Leaves the book at roughly ±150 by construction and fires more often for less effect.

**Ratio spread hedge** (sell 2 further OTM to finance buying 1). Reduces hedge cost, but adds naked short options on the tested side. On the 8 days the core straddle genuinely lost, the market trended — 60–82% of the day's range in the net move; 18 May moved 269 points on a 337-point range. A put ratio blows out precisely there, converting the worst days from bad to unrecoverable, and raises margin mid-session, which the engine does not model. If hedge cost needs reducing later, a **1:1 debit spread** keeps it defined-risk.

## Out of Scope: Stale ATM Strike

Not addressed here, and it is the larger remaining issue for later slots.

`_RESOLVE_TIME = time(9, 49)` plus the `atm_strike is None` latch freezes ATM once per session, so every slot resolves at ~09:49 regardless of its own `entry_time`. Later slots therefore enter on a stale strike and are *born* with delta:

| Slot | Sessions | Avg points off ATM | Beyond ½ strike | Avg P&L |
|---|---|---|---|---|
| 09:50 | 64 | 13.6 | 12% | +Rs 4,286 |
| 10:15 | 70 | 30.5 | 53% | −Rs 839 |
| 10:30 | 25 | 41.9 | 64% | −Rs 3,508 |

Worst observed: 156 points off, three strikes away. 39 of 101 hedged sessions fired within 5 minutes of entry — the engine immediately correcting a skew that entry created.

**A backtest cannot measure this fix.** `generic_executor.validate_run` already resolves ATM at the slot's own `entry_time`, so only live has the bug. It requires a separate PR against the live entry path and a paper-trading day of observation.

## Open Questions for Review

1. Should `PARTIAL` be forced on, or remain a per-slot choice? Leaving `FULL` selectable preserves the ability to reproduce historical behaviour, but also preserves the footgun.
2. The undersized-skip currently logs once per episode. Is per-episode the right granularity, or should it be silent?
3. 17 Aug shows the accepted cost of smaller hedges. Is that trade-off acceptable as policy, or should the sizing floor be raised above one lot?
4. Should the trigger threshold gain a buffer or dwell requirement? 12 Aug fired on a 2-point breach (152.05 vs 150) on a day whose entire range was 145 points; `reentry_buffer` governs only re-arming, never the first trigger.

# Proposal: Scenario-Based Hedge Sizing

Version: 0.1 — proposal, nothing built
Date: 7 September 2026
Scope: `short_straddle_dual_lock`, delta hedge sizing only
Status: **Needs a decision on Phase 1 before any of this is worth building**

## The ask

Today the FULL slot beat PARTIAL on both entry times (+₹6,083 vs +₹910 at 9:50; +₹12,399 vs +₹3,554 at 10:15). The request: stop choosing PARTIAL or FULL up front, and instead evaluate scenarios at the moment of hedging — where is delta, what does the wing cost, what happens if the market continues or reverts — and pick the size that comes out best.

The instinct is right that a fixed rule leaves something on the table. What follows is what the data supports, what it does not, and the smallest build that would settle it.

## What today actually showed

The 9:50 hedge log is the whole story:

```
PARTIAL   10:13   5 lots @ 37.15   -> residual delta +23
          11:04   4 lots @ 47.45   -> residual delta +28
          12:30   4 lots @ 46.05   -> residual delta +20
FULL      10:14  13 lots @ 37.85   -> delta -184, never re-triggered
```

The 23750 PE closed at 38.60. PARTIAL's second and third hedges lost ₹2,655 and ₹2,235; its hedging cost **−₹4,346** in total. FULL bought once and its single leg finished **+₹1,950**. That ₹6,296 swing is essentially the entire gap between the two.

The apparent mechanism: PARTIAL rounds down and deliberately leaves residual delta, so on a sustained move delta re-breaches and it buys protection again, later and worse.

## Why that explanation does not survive contact with the data

It is a good story and it is wrong as a general rule. Across the 72-day set:

| | Days | PARTIAL − FULL |
|---|---:|---:|
| PARTIAL hedged **more** than FULL | 26 | **+₹230,869** (+8,880/day) |
| Equal hedge count | 41 | +₹197,945 (+4,828/day) |
| PARTIAL hedged **fewer** | 5 | +₹119,545 |

**On the days PARTIAL hedged more, it won by more.** Incremental hedging is not a cost centre; today is the exception, not the pattern. PARTIAL sessions taking five hedges average **+₹49,220**; FULL sessions taking five average **−₹13,509**.

Building an adaptive rule on today's mechanism would be fitting to one session against 72 that say the opposite.

## The constraint that governs everything here

**There is no directional edge at the hedge trigger.** Measured across all 130 delta-150 triggers since 10 May:

| Horizon | Continued | Average |
|---|---|---|
| +30 min | 47% | −5.7 pts |
| +60 min | 52% | −6.1 pts |
| At close | 45% | −9.8 pts |

Average best continuation +68 points; average reversal **−77 points**.

This matters more than it first appears. If we cannot tell whether the move continues, and the option is fairly priced, then **choosing a different size does not change expected return — it changes the risk profile.** Buying more of a fairly priced option has an expected value of roughly zero, whatever the size.

So "pick the most profitable size" is not achievable by sizing alone. What *is* achievable:

1. Find out whether the current sizing point is simply **wrong**, not whether it can be predicted.
2. Make the risk of each choice **explicit**, so the tradeoff is chosen rather than inherited.

## The scenario frame, applied to today

For the 10:13 decision, position priced at expiry:

| Spot at expiry | No hedge | 5 lots (partial) | 13 lots (full) |
|---|---:|---:|---:|
| 23,550 | −153,368 | −92,299 | **+5,411** |
| 23,650 | −55,868 | −32,299 | **+5,411** |
| 23,750 | **+41,632** | +27,701 | +5,411 |
| **23,779 (actual)** | **+69,908** | +55,976 | +33,686 |
| 23,850 (unchanged) | **+139,132** | +125,201 | +102,911 |

Full hedging only wins below **23,712** — a 126-point fall. Spot bottomed at 23,738 and closed 23,779, so on this frame **not hedging at all** was the best choice today, and full was the worst.

Yet FULL made the most money. The frame is wrong, not the arithmetic: these were **same-day exits at 15:12 with a day of time value left**, not expiry outcomes. Any scenario engine must model the intraday mark, not the expiry payoff, or it will systematically recommend the wrong thing.

That is a design requirement, and it is the reason this cannot be a simple payoff calculator.

## Proposal

### Phase 1 — Does sizing have any headroom at all? *(cheap, decisive)*

Before building anything adaptive, test whether **any fixed multiplier** beats the current rule. Sweep the delta-neutral size by 0.5×, 1× (today), 1.5×, 2×, and FULL across the 72-day set.

- If 1× is already the best fixed point, adaptive sizing is fitting noise and this stops here.
- If a different multiplier wins consistently, that is a one-line change worth far more than a scenario engine.

This is a backtest sweep of work already tooled. **Nothing else should be built until it returns.**

### Phase 2 — Scenario evaluator, shadow only

At each hedge decision, for every candidate size 0…max_lots:

- Compute the position's **intraday mark** across a spot grid, at the current IV and time remaining — not the expiry payoff.
- Weight by the distribution implied by current IV, which is the market's own forecast and the only unbiased one available.
- Report the three explicit cases asked for: continues, reverts, unchanged.
- Record what it *would* have chosen. **It does not act.**

Output per decision: chosen size, the fixed rule's size, and the P&L difference had each been taken.

### Phase 3 — Gate on measured performance

Only if the shadow choices beat the fixed rule **out of sample** does any of it reach the live path. The bar should be set before looking at the numbers: the shadow must win over a period it was not tuned on.

### Phase 4 — Act, one slot only

Behind a config flag, on one entry time, with the fixed-rule slot running alongside as the control.

## What I would not do

**Do not switch to FULL on today's evidence.** Three trending days have gone FULL's way, which is a pattern worth watching, but the 72-day record is PARTIAL **+₹681,287** against FULL **+₹132,928**, with 3 stop-outs against 11. Three days does not overturn that.

**Do not build a directional filter into the sizing.** The trigger has no directional edge; a size that leans on continuation is a bet on a 45–52% signal.

**Do not use expiry payoffs to choose an intraday size.** Today's table shows that frame recommending "no hedge" on a day where hedging made money.

## Open questions

1. **What is the objective?** Maximum expected P&L, minimum drawdown, or best return-per-risk. These select different sizes and the answer is a preference, not a calculation. It has to be stated before Phase 2.
2. **PARTIAL's P&L by hedge count is bimodal** — 2 to 3 hedges average −₹3,529 and −₹4,342, while 5 hedges average +₹49,220. If something distinguishes those cases *at the second hedge*, that is a more promising signal than sizing. Worth a look during Phase 1.
3. **Does the trigger threshold matter more than the size?** 150 was never tuned. It may be the bigger lever, and it is testable the same way.

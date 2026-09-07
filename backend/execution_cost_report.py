"""
Execution cost report: assumed P&L vs what the order book would really have given.

    python execution_cost_report.py 2026-09-01
    python execution_cost_report.py 2026-09-01 2026-09-05 --json

Read-only. Joins recorded fills (strategy_run_events + strategy_run_legs)
against captured depth (option_depth_snapshots) and re-prices every fill.

Reports a range, never a point estimate:
    mid      fill at (bid+ask)/2                 optimistic bound
    walked   fill from the touch, walking size   pessimistic bound

Depth exists only for sessions captured live -- Zerodha does not serve it
historically -- so this can only ever look forward from the day capture
shipped.
"""
import argparse
import asyncio
import json
import sys
from datetime import date, datetime, time as dtime

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.services.execution_cost import cost_session

# Events whose payload describes an executed hedge (as opposed to a trigger
# or a re-arm notice, which carry no quantity).
_HEDGE_EXECUTED_KEYS = ("quantity", "strike", "price")

# Automatic exits are written with the fired code as both event_type and
# reason_code; only a manual stop uses the generic 'EXIT'.
_TERMINAL_EVENT_TYPES = {
    "EXIT", "TIME_EXIT", "TRAIL_EXIT", "STOP_EXIT", "TARGET_EXIT",
    "DATA_GAP_EXIT", "MANUAL_STOP_EXIT", "NO_TRADE",
}


def _opposite(side: str) -> str:
    return "BUY" if str(side).upper() == "SELL" else "SELL"


async def _load_runs(db, start: date, end: date):
    rows = await db.execute(text("""
        SELECT id, trade_date, entry_time, exit_time, status, exit_reason,
               lot_size, approved_lots, realized_net_pnl, strategy_id
        FROM strategy_runs
        WHERE run_type = 'live_paper_session'
          AND trade_date BETWEEN :start AND :end
        ORDER BY trade_date, id
    """), {"start": start, "end": end})
    return [dict(r._mapping) for r in rows]


async def _load_legs(db, run_id):
    rows = await db.execute(text("""
        SELECT leg_index, side, option_type, strike, quantity, expiry_date,
               entry_timestamp, entry_price, exit_price
        FROM strategy_run_legs WHERE run_id = :rid ORDER BY leg_index
    """), {"rid": run_id})
    return [dict(r._mapping) for r in rows]


async def _load_events(db, run_id):
    # reason_code is load-bearing and HOLD rows must be kept: the engine records
    # a lock as event_type='HOLD', reason_code='WINGS_LOCKED', so filtering HOLD
    # discards the only timestamp the wing legs have.
    rows = await db.execute(text("""
        SELECT timestamp, event_type, reason_code, payload_json
        FROM strategy_run_events
        WHERE run_id = :rid
        ORDER BY timestamp
    """), {"rid": run_id})
    return [dict(r._mapping) for r in rows]


async def _load_depth(db, trade_date):
    rows = await db.execute(text("""
        SELECT timestamp, symbol, strike, option_type, expiry_date, last_price,
               bid, ask, bid_qty, ask_qty, depth_json
        FROM option_depth_snapshots WHERE trade_date = :d
    """), {"d": trade_date})
    out = []
    for r in rows:
        m = dict(r._mapping)
        for k in ("bid", "ask", "last_price"):
            m[k] = float(m[k]) if m[k] is not None else None
        out.append(m)
    return out


def _build_fills(run, legs, events):
    """Reconstruct every fill the session made, with its timestamp.

    Opening timestamps come from events; closing fills all happen at exit.
    A leg whose opening event cannot be identified gets no timestamp, so it
    is reported as unpriced rather than being matched to an arbitrary book.
    """
    entry_ts = next((e["timestamp"] for e in events if e["event_type"] == "ENTRY"), None)

    # Terminal events are not uniformly typed. A manual stop is written as
    # event_type='EXIT'/reason_code='MANUAL_STOP_EXIT', but every automatic exit
    # is written with the fired code as BOTH type and reason -- 'TIME_EXIT',
    # 'TRAIL_EXIT', 'STOP_EXIT' and so on. Matching only on 'EXIT' therefore
    # missed every normal session and fell back to the run's minute-only
    # exit_time, pricing the close against the book at the start of that minute.
    exit_ts = None
    run_reason = (run.get("exit_reason") or "").upper()
    for ev in reversed(events):
        et = (ev.get("event_type") or "").upper()
        rc = (ev.get("reason_code") or "").upper()
        if run_reason and rc == run_reason:
            exit_ts = ev["timestamp"]; break
        if et in _TERMINAL_EVENT_TYPES or rc in _TERMINAL_EVENT_TYPES:
            exit_ts = ev["timestamp"]; break
    if exit_ts is None and run.get("exit_time"):
        # Last resort: minute precision only, so the match is coarser.
        try:
            hh, mm = str(run["exit_time"]).split(":")
            exit_ts = datetime.combine(run["trade_date"], dtime(int(hh), int(mm)))
        except (ValueError, TypeError):
            exit_ts = None

    # Executed hedges, in order, so they can be matched to their legs.
    hedges = [
        e for e in events
        if e["event_type"] == "DELTA_HEDGE"
        and isinstance(e.get("payload_json"), dict)
        and all(k in e["payload_json"] for k in _HEDGE_EXECUTED_KEYS)
    ]
    lock_ts = next((e["timestamp"] for e in events
                    if (e.get("reason_code") or "").upper() == "WINGS_LOCKED"), None)

    used_hedges = set()
    fills = []
    for leg in legs:
        idx = int(leg["leg_index"])
        strike, opt = int(leg["strike"]), str(leg["option_type"])

        if leg.get("entry_timestamp") is not None:
            # Most reliable: the leg records when it was actually entered.
            # The backtest sets this for wings and hedges; the live engine sets
            # it for hedges only, hence the event fallbacks below.
            open_ts = leg["entry_timestamp"]
        elif idx <= 1:
            open_ts = entry_ts
        elif idx <= 3:
            open_ts = lock_ts                 # wings bought when the lock fired
        else:
            open_ts = None                    # delta hedge -- match by contract
            for i, h in enumerate(hedges):
                p = h["payload_json"]
                if i in used_hedges:
                    continue
                if int(p.get("strike", -1)) == strike and str(p.get("option_type")) == opt:
                    open_ts, _ = h["timestamp"], used_hedges.add(i)
                    break

        if leg.get("entry_price") is not None:
            fills.append({
                "label": f"leg{idx} open", "side": leg["side"], "option_type": opt,
                "strike": strike, "expiry_date": leg.get("expiry_date"),
                "quantity": int(leg["quantity"] or 0),
                "price": float(leg["entry_price"]), "timestamp": open_ts,
            })
        if leg.get("exit_price") is not None:
            fills.append({
                # Closing a SELL is a BUY order, and vice versa.
                "label": f"leg{idx} close", "side": _opposite(leg["side"]), "option_type": opt,
                "strike": strike, "expiry_date": leg.get("expiry_date"),
                "quantity": int(leg["quantity"] or 0),
                "price": float(leg["exit_price"]), "timestamp": exit_ts,
            })
    return fills


def _print_session(run, sc, verbose):
    s = sc.summary()
    pnl = run.get("realized_net_pnl")
    pnl_s = f"{float(pnl):>12,.0f}" if pnl is not None else f"{'n/a':>12}"
    print(f"\n  run {str(run['id'])[:8]}  {run['trade_date']}  "
          f"entry {run.get('entry_time') or '--:--'}  {run.get('exit_reason') or run.get('status')}")
    print(f"    reported net P&L        {pnl_s}")
    if s["fills_priced"] == 0:
        why = sc.fills[0].note if sc.fills else "no fills recorded"
        print(f"    NOT COSTED              {why} ({s['fills_total']} fills)")
        return
    print(f"    fills priced            {s['fills_priced']}/{s['fills_total']}"
          f"  (coverage {s['coverage']:.0%}, qty {s['quantity_coverage']:.0%})")
    mid = s["cost_mid"]
    print(f"    execution cost          "
          f"{(f'{mid:>12,.0f}' if mid is not None else f'{chr(45)*3:>12}')}  (mid, optimistic)")
    print(f"                            {s['cost_walked']:>12,.0f}  (walked, pessimistic)")
    if not s["complete"]:
        # Subtracting a partial cost from the full reported P&L would treat
        # every unpriced fill and unfilled lot as costless -- exactly the
        # flattering result this report exists to avoid.
        reasons = []
        if s["fills_priced"] < s["fills_total"]:
            reasons.append(f"{s['fills_total'] - s['fills_priced']} fill(s) unpriced")
        if s["shortfall_fills"]:
            reasons.append(f"{s['shortfall_fills']} fill(s) exceeded visible depth")
        if mid is None:
            reasons.append("no midpoint on some fills")
        print(f"    PARTIAL COST ONLY       no realistic P&L: {'; '.join(reasons)}")
    elif pnl is not None:
        print(f"    realistic net P&L       {float(pnl) - s['cost_walked']:>12,.0f}"
              f"  ..  {float(pnl) - mid:,.0f}")
    if verbose:
        for f in sc.fills:
            note = f"  [{f.note}]" if f.note else ""
            if f.priced:
                qty = (f"x{f.filled_qty}/{f.quantity}" if f.shortfall_qty else f"x{f.quantity}")
                print(f"      {f.label:<12} {f.side:<4} {f.strike} {f.option_type} {qty:<12}"
                      f" assumed {f.assumed_price:>8.2f}  walked {f.walked_price:>8.2f}"
                      f"  cost {f.cost_walked:>9,.0f}{note}")
            else:
                print(f"      {f.label:<12} {f.side:<4} {f.strike} {f.option_type} x{f.quantity:<5}"
                      f" -- unpriced{note}")


async def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("start", help="YYYY-MM-DD")
    ap.add_argument("end", nargs="?", help="YYYY-MM-DD (defaults to start)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every fill")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else start

    results = []
    async with AsyncSessionLocal() as db:
        runs = await _load_runs(db, start, end)
        if not runs:
            print(f"No live paper sessions between {start} and {end}.")
            return 0
        depth_cache = {}
        for run in runs:
            td = run["trade_date"]
            if td not in depth_cache:
                depth_cache[td] = await _load_depth(db, td)
            legs = await _load_legs(db, run["id"])
            events = await _load_events(db, run["id"])
            fills = _build_fills(run, legs, events)
            sc = cost_session(run_id=run["id"], trade_date=td,
                              fills=fills, depth_rows=depth_cache[td])
            results.append((run, sc))

    if args.json:
        print(json.dumps([{**sc.summary(),
                           "reported_net_pnl": float(r["realized_net_pnl"]) if r.get("realized_net_pnl") is not None else None}
                          for r, sc in results], indent=2))
        return 0

    print(f"\nEXECUTION COST REPORT   {start} .. {end}")
    print("=" * 72)
    for run, sc in results:
        _print_session(run, sc, args.verbose)

    costed = [(r, sc) for r, sc in results if sc.priced_fills]
    print("\n" + "=" * 72)
    if not costed:
        print("Nothing could be costed -- no captured depth overlaps these sessions.")
        print("Depth exists only from the day capture shipped; it cannot be backfilled.")
        return 0
    n = len(costed)
    tot_walked = sum(sc.cost_walked for _, sc in costed)
    mids = [sc.cost_mid for _, sc in costed]
    tot_mid = sum(mids) if all(m is not None for m in mids) else None
    mid_s = f"{tot_mid:>12,.0f}" if tot_mid is not None else f"{'---':>12}"
    print(f"{n} session(s) costed of {len(results)}")
    print(f"  total execution cost    {mid_s}  ..  {tot_walked:,.0f}")
    if tot_mid is not None:
        print(f"  per session             {tot_mid / n:>12,.0f}  ..  {tot_walked / n:,.0f}")
    else:
        print(f"  per session             {'---':>12}  ..  {tot_walked / n:,.0f}")

    # Only sessions costed end-to-end can support a P&L adjustment. Mixing in
    # partially-costed ones would credit their uncosted fills with zero cost.
    complete = [(r, sc) for r, sc in costed if sc.complete]
    partial = [(r, sc) for r, sc in costed if not sc.complete]
    pnls = [float(r["realized_net_pnl"]) for r, sc in complete
            if r.get("realized_net_pnl") is not None]
    if pnls and len(pnls) == len(complete):
        gross = sum(pnls)
        c_walked = sum(sc.cost_walked for _, sc in complete)
        c_mid = sum(sc.cost_mid for _, sc in complete)
        print(f"\n  fully costed sessions   {len(complete)}")
        print(f"  reported net P&L        {gross:>12,.0f}")
        print(f"  realistic net P&L       {gross - c_walked:>12,.0f}  ..  {gross - c_mid:,.0f}")
    if partial:
        # Not a "lower bound": execution cost can be negative -- a fill better
        # than the observed price is a credit -- so the uncosted fills can move
        # the eventual total in either direction.
        print(f"\n  {len(partial)} session(s) only partially costed -- execution cost above is an")
        print(f"  incomplete subtotal for those, and no realistic P&L is shown for them.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

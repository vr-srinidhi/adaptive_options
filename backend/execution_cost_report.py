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
        SELECT leg_index, side, option_type, strike, quantity, entry_price, exit_price
        FROM strategy_run_legs WHERE run_id = :rid ORDER BY leg_index
    """), {"rid": run_id})
    return [dict(r._mapping) for r in rows]


async def _load_events(db, run_id):
    rows = await db.execute(text("""
        SELECT timestamp, event_type, payload_json
        FROM strategy_run_events
        WHERE run_id = :rid AND event_type <> 'HOLD'
        ORDER BY timestamp
    """), {"rid": run_id})
    return [dict(r._mapping) for r in rows]


async def _load_depth(db, trade_date):
    rows = await db.execute(text("""
        SELECT timestamp, symbol, strike, option_type, last_price,
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
    exit_ts = next((e["timestamp"] for e in events if e["event_type"] == "EXIT"), None)
    if exit_ts is None and run.get("exit_time"):
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
                    if e["event_type"] in ("LOCK", "WING_LOCK", "PROFIT_LOCK", "LOSS_LOCK")), None)

    used_hedges = set()
    fills = []
    for leg in legs:
        idx = int(leg["leg_index"])
        strike, opt = int(leg["strike"]), str(leg["option_type"])

        if idx <= 1:
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
                "strike": strike, "quantity": int(leg["quantity"] or 0),
                "price": float(leg["entry_price"]), "timestamp": open_ts,
            })
        if leg.get("exit_price") is not None:
            fills.append({
                # Closing a SELL is a BUY order, and vice versa.
                "label": f"leg{idx} close", "side": _opposite(leg["side"]), "option_type": opt,
                "strike": strike, "quantity": int(leg["quantity"] or 0),
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
          f"  (coverage {s['coverage']:.0%})")
    print(f"    execution cost          {s['cost_mid']:>12,.0f}  (mid, optimistic)")
    print(f"                            {s['cost_walked']:>12,.0f}  (walked, pessimistic)")
    if pnl is not None:
        print(f"    realistic net P&L       {float(pnl) - s['cost_walked']:>12,.0f}"
              f"  ..  {float(pnl) - s['cost_mid']:,.0f}")
    if s["shortfall_fills"]:
        print(f"    !! {s['shortfall_fills']} fill(s) exceeded visible book depth")
    if verbose:
        for f in sc.fills:
            note = f"  [{f.note}]" if f.note else ""
            if f.priced:
                print(f"      {f.label:<12} {f.side:<4} {f.strike} {f.option_type} x{f.quantity:<5}"
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
    tot_mid = sum(sc.cost_mid for _, sc in costed)
    tot_walked = sum(sc.cost_walked for _, sc in costed)
    n = len(costed)
    print(f"{n} session(s) costed of {len(results)}")
    print(f"  total execution cost    {tot_mid:>12,.0f}  ..  {tot_walked:,.0f}")
    print(f"  per session             {tot_mid / n:>12,.0f}  ..  {tot_walked / n:,.0f}")
    pnls = [float(r["realized_net_pnl"]) for r, _ in costed if r.get("realized_net_pnl") is not None]
    if pnls:
        gross = sum(pnls)
        print(f"  reported net P&L        {gross:>12,.0f}")
        print(f"  realistic net P&L       {gross - tot_walked:>12,.0f}  ..  {gross - tot_mid:,.0f}")
    weak = [sc for _, sc in costed if sc.coverage < 0.8]
    if weak:
        print(f"\n  NOTE: {len(weak)} session(s) priced under 80% of fills -- treat those as indicative.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

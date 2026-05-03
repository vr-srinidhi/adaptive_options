"""
Parameter sweep: re-apply different target/stop combinations to the existing
strategy_run_mtm series for iron_butterfly runs — no new DB writes needed.

Uses net_mtm (which already has estimated exit charges folded in per minute)
to simulate what P&L would have been under each exit configuration.
"""
import asyncio
import os
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@db:5432/adaptive_options")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

TARGETS = [8_000, 10_000, 12_000, 15_000, 18_000, 20_000, 25_000]
STOPS   = [15_000, 20_000, 25_000, 30_000, 35_000]


def simulate_exit(net_mtm_series, target, stop):
    """
    Walk the net_mtm series and return simulated P&L under given target/stop.
    Returns (pnl, exit_type).
    """
    prev = 0.0
    for val in net_mtm_series:
        if val >= target:
            return target, "TARGET"
        if val <= -stop:
            return -stop, "STOP"
        prev = val
    return prev, "TIME"  # reached time exit


async def main():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Load all IB runs with their trade_date
        runs_res = await db.execute(text(
            "SELECT id, trade_date FROM strategy_runs WHERE strategy_id='iron_butterfly' ORDER BY trade_date"
        ))
        runs = runs_res.fetchall()

        if not runs:
            print("No iron_butterfly runs found. Run the batch first.")
            return

        # Load all MTM series in one query
        run_ids = [str(r.id) for r in runs]
        mtm_res = await db.execute(text(
            "SELECT run_id, net_mtm FROM strategy_run_mtm "
            "WHERE run_id = ANY(:ids) ORDER BY run_id, timestamp"
        ), {"ids": run_ids})
        mtm_rows = mtm_res.fetchall()

    # Group MTM by run_id
    mtm_by_run = defaultdict(list)
    for row in mtm_rows:
        mtm_by_run[str(row.run_id)].append(float(row.net_mtm))

    print(f"Loaded {len(runs)} runs, {len(mtm_rows)} MTM rows\n")

    # ── Header ──────────────────────────────────────────────────────────────
    stop_labels = [f"Stop ₹{s//1000}k" for s in STOPS]
    col_w = 18
    print(f"{'Target →':<14}", end="")
    for s in stop_labels:
        print(f"{s:>{col_w}}", end="")
    print()
    print("-" * (14 + col_w * len(STOPS)))

    best = {"pnl": float("-inf"), "target": None, "stop": None}

    for target in TARGETS:
        label = f"Target ₹{target//1000}k"
        print(f"{label:<14}", end="")
        for stop in STOPS:
            wins = losses = 0
            total_pnl = 0.0
            for run in runs:
                series = mtm_by_run.get(str(run.id), [])
                if not series:
                    continue
                pnl, _ = simulate_exit(series, target, stop)
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
            cell = f"₹{total_pnl:+,.0f} ({wins}W/{losses}L)"
            print(f"{cell:>{col_w}}", end="")
            if total_pnl > best["pnl"]:
                best = {"pnl": total_pnl, "target": target, "stop": stop, "wins": wins, "losses": losses}
        print()

    print("-" * (14 + col_w * len(STOPS)))
    print(f"\nBest: Target ₹{best['target']:,}  Stop ₹{best['stop']:,}  →  Net ₹{best['pnl']:+,.0f}  ({best['wins']}W/{best['losses']}L)")

    # ── Detailed day-by-day for best config ─────────────────────────────────
    print(f"\n── Day-by-day at best config (Target ₹{best['target']:,} / Stop ₹{best['stop']:,}) ──")
    print(f"{'Date':<14} {'P&L':>12} {'Exit':>8}")
    print("-" * 36)
    total = 0.0
    for run in runs:
        series = mtm_by_run.get(str(run.id), [])
        pnl, exit_type = simulate_exit(series, best["target"], best["stop"])
        total += pnl
        print(f"{run.trade_date}   {pnl:>+12,.2f}   {exit_type}")
    print("-" * 36)
    print(f"{'Total':14}   {total:>+12,.2f}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

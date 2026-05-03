"""Batch runner: Short Straddle — Dual Lock (profit lock + loss lock) 2020–2026."""
import asyncio, os, uuid
from datetime import date
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@db:5432/adaptive_options")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

from app.services.workbench_catalog import get_strategy
from app.services.generic_executor import validate_run
from app.services.straddle_adjustment_executor import execute_run
from app.models.user import User

CONFIG = {
    "instrument":          "NIFTY",
    "entry_time":          "09:50",
    "capital":             2_500_000,
    "lock_trigger":        20_000,   # buy wings when profit reaches ₹20k
    "loss_lock_trigger":   10_000,   # buy wings when loss reaches ₹10k (defensive)
    "wing_width_steps":    2,
    "trail_trigger":       12_000,
    "trail_pct":           0.50,
    "stop_capital_pct":    0.015,
    "vix_guardrail_enabled": False,
}

START = date(2020, 1, 1)
END   = date(2026, 3, 31)


async def main():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    strategy = get_strategy("short_straddle_dual_lock")
    if strategy is None:
        print("ERROR: short_straddle_dual_lock not found in catalog")
        return

    async with async_session() as db:
        row = await db.execute(select(User).where(User.email == "vr.srinidhi@gmail.com"))
        user = row.scalar_one_or_none()
        if user is None:
            print("ERROR: user not found"); return
        user_id = user.id

        result = await db.execute(
            text("SELECT trade_date FROM trading_days WHERE backtest_ready=true AND trade_date BETWEEN :s AND :e ORDER BY trade_date"),
            {"s": START, "e": END},
        )
        trade_dates = [r.trade_date for r in result.fetchall()]

    print(f"Found {len(trade_dates)} days  |  profit_lock=₹{CONFIG['lock_trigger']:,}  loss_lock=₹{CONFIG['loss_lock_trigger']:,}  wing={CONFIG['wing_width_steps']}  trail={CONFIG['trail_trigger']:,}@{CONFIG['trail_pct']*100:.0f}%")
    print(f"{'Date':<14} {'Lots':>5} {'P&L':>12} {'Exit':>18} {'Lock'}")
    print("-" * 70)

    wins = losses = skips = 0
    total_pnl = 0.0
    profit_locks = loss_locks = 0

    for td in trade_dates:
        cfg = {**CONFIG, "trade_date": td.isoformat()}
        async with async_session() as db:
            validation = await validate_run(db, strategy, cfg)
            if not validation.validated:
                skips += 1
                print(f"{td}   SKIP  {validation.error[:55]}")
                continue
            run_id = uuid.uuid4()
            res = await execute_run(db, run_id, strategy, cfg, validation, user_id)

        pnl = res.realized_net_pnl or 0.0
        total_pnl += pnl
        if pnl >= 0: wins += 1
        else: losses += 1
        print(f"{td}   {validation.approved_lots:>4}  {pnl:>+12,.2f}  {res.exit_reason or 'TIME_EXIT':>18}")

    print("-" * 70)
    print(f"Total: {wins}W / {losses}L / {skips} skip   Net P&L: ₹{total_pnl:+,.2f}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

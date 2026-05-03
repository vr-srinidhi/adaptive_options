"""Batch runner: Short Straddle — Profit Lock for 2025 + Q1 2026."""
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
    "instrument":       "NIFTY",
    "entry_time":       "09:50",
    "capital":          2_500_000,
    "lock_trigger":     20_000,
    "wing_width_steps": 2,
    "trail_trigger":    12_000,
    "trail_pct":        0.50,
    "stop_capital_pct": 0.015,
    "vix_guardrail_enabled": False,
}

START = date(2020, 1, 1)
END   = date(2024, 12, 31)


async def main():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    strategy = get_strategy("short_straddle_profit_lock")
    if strategy is None:
        print("ERROR: short_straddle_profit_lock not found in catalog")
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

    print(f"Found {len(trade_dates)} days  |  lock=₹{CONFIG['lock_trigger']:,}  wing={CONFIG['wing_width_steps']}  trail={CONFIG['trail_trigger']:,}@{CONFIG['trail_pct']*100:.0f}%")
    print(f"{'Date':<14} {'Lots':>5} {'P&L':>12} {'Exit':>18} {'Locked'}")
    print("-" * 65)

    wins = losses = skips = 0
    total_pnl = 0.0

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
        locked = "YES" if res.warnings or (res.exit_reason and "LOCK" not in (res.exit_reason or "")) else " "
        # read lock info from result_json via a second DB query is expensive; use warnings flag
        locked_flag = "?" # will show from replay; batch just shows P&L
        if pnl >= 0: wins += 1
        else: losses += 1
        print(f"{td}   {validation.approved_lots:>4}  {pnl:>+12,.2f}  {res.exit_reason or 'TIME_EXIT':>18}")

    print("-" * 65)
    print(f"Total: {wins}W / {losses}L / {skips} skip   Net P&L: ₹{total_pnl:+,.2f}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

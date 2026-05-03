"""One-shot batch runner: Iron Butterfly single-session backtests for Jan–Mar 2026."""
import asyncio
import os
import uuid
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
from app.services.generic_executor import validate_run, execute_run
from app.models.user import User

CONFIG = {
    "instrument": "NIFTY",
    "entry_time": "09:50",
    "capital": 2_500_000,
    "wing_width_steps": 2,
    "target_amount": 12_000,
    "stop_loss_amount": 35_000,
    "vix_guardrail_enabled": False,
}

START = date(2025, 1, 1)
END   = date(2025, 12, 31)


async def main():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    strategy = get_strategy("iron_butterfly")
    if strategy is None:
        print("ERROR: iron_butterfly not found in catalog")
        return

    async with async_session() as db:
        row = await db.execute(select(User).where(User.email == "vr.srinidhi@gmail.com"))
        user = row.scalar_one_or_none()
        if user is None:
            print("ERROR: user not found")
            return
        user_id = user.id

        result = await db.execute(
            text("SELECT trade_date FROM trading_days WHERE backtest_ready=true AND trade_date BETWEEN :s AND :e ORDER BY trade_date"),
            {"s": START, "e": END},
        )
        trade_dates = [row.trade_date for row in result.fetchall()]

    print(f"Found {len(trade_dates)} backtest-ready days from {START} to {END}")
    print(f"Config: target=₹{CONFIG['target_amount']:,}  stop=₹{CONFIG['stop_loss_amount']:,}  lots=auto  wings={CONFIG['wing_width_steps']}")
    print(f"\n{'Date':<14} {'Lots':>5} {'Entry Credit':>14} {'P&L':>12} {'Exit':>18} {'Warn'}")
    print("-" * 75)

    wins, losses, skips = 0, 0, 0
    total_pnl = 0.0

    for td in trade_dates:
        cfg = {**CONFIG, "trade_date": td.isoformat()}
        async with async_session() as db:
            validation = await validate_run(db, strategy, cfg)
            if not validation.validated:
                skips += 1
                print(f"{td}   SKIP  {validation.error[:50]}")
                continue

            run_id = uuid.uuid4()
            result = await execute_run(db, run_id, strategy, cfg, validation, user_id)

        pnl = result.realized_net_pnl or 0.0
        total_pnl += pnl
        warn = "W" if result.warnings else " "
        if pnl >= 0:
            wins += 1
        else:
            losses += 1

        print(
            f"{td}   {validation.approved_lots:>4}  "
            f"  {validation.estimated_margin:>12,.0f}  "
            f"{pnl:>+12,.2f}  {result.exit_reason or 'TIME_EXIT':>18}  {warn}"
        )

    print("-" * 75)
    print(f"Total: {wins} wins / {losses} losses / {skips} skipped   Net P&L: ₹{total_pnl:+,.2f}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

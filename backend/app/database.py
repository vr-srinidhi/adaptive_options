import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase

_raw_url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/adaptive_options",
)
# Railway injects postgresql:// but asyncpg requires postgresql+asyncpg://
DATABASE_URL = (
    _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if _raw_url.startswith("postgresql://")
    else _raw_url
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    # Import models so they register with Base metadata
    from app.models import session as _  # noqa
    from app.models import paper_trade as _pt  # noqa
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent migrations for new columns added post-initial-deploy
        for stmt in [
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS no_trade_reason VARCHAR(30)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS expiry_date DATE",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS data_source VARCHAR(20)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS regime_detail VARCHAR(30)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS signal_type VARCHAR(30)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS signal_score INTEGER",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS atr14 NUMERIC(10,2)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS r_multiple NUMERIC(6,2)",
            # Paper trading tables (idempotent — create_all handles initial creation)
            # No ALTER needed; new tables are created fresh by SQLAlchemy create_all above
        ]:
            await conn.execute(__import__("sqlalchemy").text(stmt))

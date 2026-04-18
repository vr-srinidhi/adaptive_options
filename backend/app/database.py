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
    # Import all models so they register with Base metadata
    from app.models import session as _  # noqa
    from app.models import paper_trade as _pt  # noqa
    from app.models import user as _u  # noqa
    from app.models import broker_token as _bt  # noqa
    from app.models import audit_log as _al  # noqa
    from app.models import historical as _hist  # noqa

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Idempotent schema additions — run on every startup so both fresh
        # deployments and upgrades converge to the same schema without Alembic.
        for stmt in [
            # ── Backtest session columns ──────────────────────────────────────
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS no_trade_reason VARCHAR(30)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS expiry_date DATE",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS data_source VARCHAR(20)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS regime_detail VARCHAR(30)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS signal_type VARCHAR(30)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS signal_score INTEGER",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS atr14 NUMERIC(10,2)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS r_multiple NUMERIC(6,2)",
            "ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id)",
            # ── Paper session columns ─────────────────────────────────────────
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS final_session_state VARCHAR(30)",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id)",
            # ── Historical backtest columns on paper_sessions ─────────────────
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS session_type VARCHAR(20) DEFAULT 'paper_replay'",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES session_batches(id) ON DELETE SET NULL",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(20) DEFAULT 'interactive'",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS source_mode VARCHAR(20) DEFAULT 'live_like'",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS strategy_config_snapshot JSONB",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS summary_pnl NUMERIC(12,2)",
            # ── strategy_minute_decisions columns ─────────────────────────────
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS session_state VARCHAR(30)",
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS signal_substate VARCHAR(30)",
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS rejection_gate VARCHAR(10)",
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS price_freshness_json JSONB",
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS candidate_ranking_json JSONB",
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS selected_candidate_rank INTEGER",
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS selected_candidate_score NUMERIC(10,4)",
            "ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS selected_candidate_score_breakdown_json JSONB",
            # ── paper_trade_minute_marks columns ─────────────────────────────
            "ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS gross_mtm NUMERIC(10,2)",
            "ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS estimated_exit_charges NUMERIC(10,2)",
            "ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS estimated_net_mtm NUMERIC(10,2)",
            "ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS price_freshness_json JSONB",
            # ── paper_trade_headers columns ───────────────────────────────────
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS charges NUMERIC(10,2)",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS charges_breakdown_json JSONB",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(50)",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(20)",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS strategy_params_json JSONB",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS risk_cap NUMERIC(12,2)",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS entry_reason_code VARCHAR(60)",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS entry_reason_text TEXT",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS selection_method VARCHAR(60)",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS selected_candidate_rank INTEGER",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS selected_candidate_score NUMERIC(10,4)",
            "ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS selected_candidate_score_breakdown_json JSONB",
        ]:
            await conn.execute(__import__("sqlalchemy").text(stmt))

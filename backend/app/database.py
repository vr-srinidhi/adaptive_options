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
    from app.models import strategy_run as _sr  # noqa

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
            # ── paper_sessions SKIPPED status support ─────────────────────────
            "ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS error_message TEXT",
        ]:
            await conn.execute(__import__("sqlalchemy").text(stmt))

        # ── Warehouse unique constraints ──────────────────────────────────────
        # PostgreSQL does not support ALTER TABLE … ADD CONSTRAINT IF NOT EXISTS.
        # We check pg_constraint first so this block is safe to run repeatedly.
        # We also deduplicate existing rows before adding each constraint so
        # the operation succeeds even when data was loaded before constraints existed.
        _sa_text = __import__("sqlalchemy").text
        for table, constraint, cols, dedup_key in [
            (
                "spot_candles",
                "uq_spot_candles_date_sym_ts",
                "trade_date, symbol, timestamp",
                "trade_date, symbol, timestamp",
            ),
            (
                "vix_candles",
                "uq_vix_candles_date_sym_ts",
                "trade_date, symbol, timestamp",
                "trade_date, symbol, timestamp",
            ),
            (
                "futures_candles",
                "uq_futures_candles_date_sym_exp_ts",
                "trade_date, symbol, expiry_date, timestamp",
                "trade_date, symbol, expiry_date, timestamp",
            ),
            (
                "options_candles",
                "uq_options_candles_natural",
                "trade_date, symbol, expiry_date, option_type, strike, timestamp",
                "trade_date, symbol, expiry_date, option_type, strike, timestamp",
            ),
        ]:
            exists = (await conn.execute(_sa_text(
                "SELECT 1 FROM pg_constraint WHERE conname = :name"
            ), {"name": constraint})).scalar()
            if not exists:
                # Deduplicate: keep the row with the lowest id per natural key
                await conn.execute(_sa_text(
                    f"DELETE FROM {table} WHERE id NOT IN ("
                    f"  SELECT MIN(id) FROM {table} GROUP BY {dedup_key}"
                    f")"
                ))
                await conn.execute(_sa_text(
                    f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                    f"UNIQUE ({cols})"
                ))

        # ── Seed instrument_contract_specs ────────────────────────────────────
        # Idempotent: INSERT ... ON CONFLICT DO NOTHING so re-runs are safe.
        # NSE changed NIFTY lot size 50→75 in Nov 2024; both rows are seeded.
        from datetime import date as _date
        for row in [
            # (instrument, effective_from, effective_to, lot_size, strike_step, weekly_expiry_weekday, est_margin)
            ("NIFTY",    _date(2020, 1, 1),  _date(2024, 11, 20), 50,  50,  3, 90000.00),
            ("NIFTY",    _date(2024, 11, 21), None,               75,  50,  3, 180000.00),
            ("BANKNIFTY",_date(2020, 1, 1),  _date(2024, 11, 20), 25, 100,  2, 75000.00),
            ("BANKNIFTY",_date(2024, 11, 21), None,               35, 100,  2, 105000.00),
        ]:
            await conn.execute(_sa_text(
                "INSERT INTO instrument_contract_specs "
                "(instrument, effective_from, effective_to, lot_size, strike_step, "
                " weekly_expiry_weekday, estimated_margin_per_lot) "
                "VALUES (:inst, :from_, :to_, :ls, :ss, :wed, :margin) "
                "ON CONFLICT DO NOTHING"
            ), {
                "inst":   row[0],
                "from_":  row[1],
                "to_":    row[2],
                "ls":     row[3],
                "ss":     row[4],
                "wed":    row[5],
                "margin": row[6],
            })


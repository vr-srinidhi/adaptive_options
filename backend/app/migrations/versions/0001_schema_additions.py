"""Add post-initial-deploy columns to backtest and paper trading tables.

Corresponds to all ALTER TABLE IF NOT EXISTS statements that were previously
run inline in database.py's init_db(). Uses IF NOT EXISTS so this migration
is safe to run against both fresh and existing deployments.

Revision ID: 0001
Revises:
Create Date: 2026-04-10
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backtest session columns
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS no_trade_reason VARCHAR(30)")
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS expiry_date DATE")
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS data_source VARCHAR(20)")
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS regime_detail VARCHAR(30)")
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS signal_type VARCHAR(30)")
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS signal_score INTEGER")
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS atr14 NUMERIC(10,2)")
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS r_multiple NUMERIC(6,2)")
    # Phase 1 paper trading columns
    op.execute("ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS final_session_state VARCHAR(30)")
    op.execute("ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS session_state VARCHAR(30)")
    op.execute("ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS signal_substate VARCHAR(30)")
    op.execute("ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS rejection_gate VARCHAR(10)")
    op.execute("ALTER TABLE strategy_minute_decisions ADD COLUMN IF NOT EXISTS price_freshness_json JSONB")
    op.execute("ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS gross_mtm NUMERIC(10,2)")
    op.execute("ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS estimated_exit_charges NUMERIC(10,2)")
    op.execute("ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS estimated_net_mtm NUMERIC(10,2)")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS charges NUMERIC(10,2)")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS charges_breakdown_json JSONB")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(50)")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(20)")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS strategy_params_json JSONB")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS risk_cap NUMERIC(12,2)")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS entry_reason_code VARCHAR(60)")
    op.execute("ALTER TABLE paper_trade_headers ADD COLUMN IF NOT EXISTS entry_reason_text TEXT")
    op.execute("ALTER TABLE paper_trade_minute_marks ADD COLUMN IF NOT EXISTS price_freshness_json JSONB")


def downgrade() -> None:
    pass  # Column removals are not rolled back in this project

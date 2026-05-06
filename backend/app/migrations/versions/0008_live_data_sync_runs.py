"""Live data sync audit runs

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-06
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS live_data_sync_runs (
            id UUID PRIMARY KEY,
            trade_date DATE NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            triggered_by VARCHAR(30) NOT NULL DEFAULT 'scheduler',
            token_status VARCHAR(40) NOT NULL,
            status VARCHAR(40) NOT NULL,
            spot_rows INTEGER NOT NULL DEFAULT 0,
            vix_rows INTEGER NOT NULL DEFAULT 0,
            futures_rows INTEGER NOT NULL DEFAULT 0,
            options_rows INTEGER NOT NULL DEFAULT 0,
            option_contracts INTEGER NOT NULL DEFAULT 0,
            expiries_json JSONB,
            failed_items_json JSONB,
            notes TEXT,
            error_message TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_live_data_sync_runs_trade_date
        ON live_data_sync_runs (trade_date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_live_data_sync_runs_trade_date_started
        ON live_data_sync_runs (trade_date, started_at)
    """)


def downgrade() -> None:
    op.drop_index("ix_live_data_sync_runs_trade_date_started", table_name="live_data_sync_runs")
    op.drop_index("ix_live_data_sync_runs_trade_date", table_name="live_data_sync_runs")
    op.drop_table("live_data_sync_runs")

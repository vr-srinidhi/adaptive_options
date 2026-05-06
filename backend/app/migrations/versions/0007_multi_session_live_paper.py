"""Multi-session live paper: per-config unique constraint + label column

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add label column — idempotent via IF NOT EXISTS equivalent
    op.execute("""
        ALTER TABLE live_paper_configs
        ADD COLUMN IF NOT EXISTS label VARCHAR(50)
    """)

    # Drop the old single-session-per-day constraint only if it exists.
    # Databases bootstrapped via init_db() after this ORM change won't have it.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_live_paper_session_user_date'
                  AND conrelid = 'live_paper_sessions'::regclass
            ) THEN
                ALTER TABLE live_paper_sessions
                DROP CONSTRAINT uq_live_paper_session_user_date;
            END IF;
        END$$
    """)

    # New partial unique index — idempotent via IF NOT EXISTS.
    # One session per (user, date, config) where config is set.
    # NULL config_id rows (legacy sessions) are left unconstrained.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_live_paper_session_user_date_config
        ON live_paper_sessions (user_id, trade_date, config_id)
        WHERE config_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_live_paper_session_user_date_config")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_live_paper_session_user_date'
                  AND conrelid = 'live_paper_sessions'::regclass
            ) THEN
                ALTER TABLE live_paper_sessions
                ADD CONSTRAINT uq_live_paper_session_user_date
                UNIQUE (user_id, trade_date);
            END IF;
        END$$
    """)
    op.execute("ALTER TABLE live_paper_configs DROP COLUMN IF EXISTS label")

"""Add security tables and user ownership columns.

Creates: users, broker_tokens, audit_logs
Adds:    user_id FK to backtest_sessions and paper_sessions

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-10
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ─────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) UNIQUE NOT NULL,
            hashed_password VARCHAR(255) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # ── broker_tokens ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS broker_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            broker VARCHAR(20) NOT NULL DEFAULT 'ZERODHA',
            encrypted_token TEXT NOT NULL,
            token_date DATE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_broker_tokens_user_id ON broker_tokens(user_id)")

    # ── audit_logs ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            event_type VARCHAR(50) NOT NULL,
            detail JSONB,
            ip_address VARCHAR(45),
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # ── user_id FK on existing tables ─────────────────────────────────────────
    op.execute("ALTER TABLE backtest_sessions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id)")
    op.execute("ALTER TABLE paper_sessions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id)")


def downgrade() -> None:
    op.execute("ALTER TABLE paper_sessions DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE backtest_sessions DROP COLUMN IF EXISTS user_id")
    op.execute("DROP TABLE IF EXISTS audit_logs")
    op.execute("DROP TABLE IF EXISTS broker_tokens")
    op.execute("DROP TABLE IF EXISTS users")

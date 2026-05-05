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
    # Add label to live_paper_configs so each slot is identifiable
    op.add_column(
        "live_paper_configs",
        sa.Column("label", sa.String(50), nullable=True),
    )

    # Drop the old single-session-per-day constraint
    op.drop_constraint("uq_live_paper_session_user_date", "live_paper_sessions", type_="unique")

    # New partial unique index: one session per (user, date, config) where config is set.
    # NULL config_id rows (legacy sessions) are left unconstrained.
    op.execute("""
        CREATE UNIQUE INDEX uq_live_paper_session_user_date_config
        ON live_paper_sessions (user_id, trade_date, config_id)
        WHERE config_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_live_paper_session_user_date_config")
    op.create_unique_constraint(
        "uq_live_paper_session_user_date",
        "live_paper_sessions",
        ["user_id", "trade_date"],
    )
    op.drop_column("live_paper_configs", "label")

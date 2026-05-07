"""Add delta hedge tracking fields.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategy_run_mtm",
        sa.Column("net_delta", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "live_paper_sessions",
        sa.Column("net_delta_latest", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "live_paper_sessions",
        sa.Column("delta_hedge_status", sa.String(20), nullable=True, server_default="off"),
    )
    op.add_column(
        "live_paper_sessions",
        sa.Column("delta_hedge_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("live_paper_sessions", "delta_hedge_count")
    op.drop_column("live_paper_sessions", "delta_hedge_status")
    op.drop_column("live_paper_sessions", "net_delta_latest")
    op.drop_column("strategy_run_mtm", "net_delta")

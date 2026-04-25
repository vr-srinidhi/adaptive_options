"""Add trail_stop_level column to strategy_run_mtm.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategy_run_mtm",
        sa.Column("trail_stop_level", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategy_run_mtm", "trail_stop_level")

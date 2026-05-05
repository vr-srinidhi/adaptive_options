"""Add entry_timestamp to strategy_run_legs

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "strategy_run_legs",
        sa.Column("entry_timestamp", sa.TIMESTAMP(timezone=False), nullable=True),
    )


def downgrade():
    op.drop_column("strategy_run_legs", "entry_timestamp")

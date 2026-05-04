"""Add unique constraint on live_paper_sessions (user_id, trade_date).

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-04
"""
from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_live_paper_session_user_date",
        "live_paper_sessions",
        ["user_id", "trade_date"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_live_paper_session_user_date",
        "live_paper_sessions",
        type_="unique",
    )

"""Add generic strategy run tables and instrument contract specs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_contract_specs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument", sa.String(20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("strike_step", sa.Integer(), nullable=False),
        sa.Column("weekly_expiry_weekday", sa.SmallInteger(), nullable=False),
        sa.Column("estimated_margin_per_lot", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contract_specs_instrument_from", "instrument_contract_specs", ["instrument", "effective_from"])

    op.create_table(
        "strategy_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_id", sa.String(60), nullable=False),
        sa.Column("strategy_version", sa.String(20), nullable=True),
        sa.Column("run_type", sa.String(40), nullable=False),
        sa.Column("executor", sa.String(40), nullable=False),
        sa.Column("instrument", sa.String(20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("entry_time", sa.String(5), nullable=True),
        sa.Column("exit_time", sa.String(5), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("exit_reason", sa.String(30), nullable=True),
        sa.Column("capital", sa.Numeric(14, 2), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=True),
        sa.Column("approved_lots", sa.Integer(), nullable=True),
        sa.Column("entry_credit_per_unit", sa.Numeric(10, 2), nullable=True),
        sa.Column("entry_credit_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("gross_pnl", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_charges", sa.Numeric(10, 2), nullable=True),
        sa.Column("realized_net_pnl", sa.Numeric(12, 2), nullable=True),
        sa.Column("config_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_runs_user_date", "strategy_runs", ["user_id", "trade_date"])
    op.create_index("ix_strategy_runs_strategy_date", "strategy_runs", ["strategy_id", "trade_date"])

    op.create_table(
        "strategy_run_legs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("leg_index", sa.SmallInteger(), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("option_type", sa.String(3), nullable=False),
        sa.Column("strike", sa.Integer(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("exit_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("gross_leg_pnl", sa.Numeric(12, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "leg_index", name="uq_strategy_run_legs_run_idx"),
    )
    op.create_index("ix_strategy_run_legs_run_id", "strategy_run_legs", ["run_id"])

    op.create_table(
        "strategy_run_mtm",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=False), nullable=False),
        sa.Column("spot_close", sa.Numeric(10, 2), nullable=True),
        sa.Column("vix_close", sa.Numeric(8, 4), nullable=True),
        sa.Column("gross_mtm", sa.Numeric(12, 2), nullable=True),
        sa.Column("est_exit_charges", sa.Numeric(10, 2), nullable=True),
        sa.Column("net_mtm", sa.Numeric(12, 2), nullable=True),
        sa.Column("event_code", sa.String(30), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_run_mtm_run_ts", "strategy_run_mtm", ["run_id", "timestamp"])

    op.create_table(
        "strategy_leg_mtm",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("leg_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=False), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("gross_leg_pnl", sa.Numeric(12, 2), nullable=True),
        sa.Column("stale_minutes", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_leg_mtm_run_ts", "strategy_leg_mtm", ["run_id", "timestamp"])
    op.create_index("ix_strategy_leg_mtm_leg_ts", "strategy_leg_mtm", ["leg_id", "timestamp"])

    op.create_table(
        "strategy_run_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=False), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(50), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_run_events_run_ts", "strategy_run_events", ["run_id", "timestamp"])


def downgrade() -> None:
    op.drop_table("strategy_run_events")
    op.drop_table("strategy_leg_mtm")
    op.drop_table("strategy_run_mtm")
    op.drop_table("strategy_run_legs")
    op.drop_table("strategy_runs")
    op.drop_table("instrument_contract_specs")

"""
Generic strategy run ORM models.

These tables back every strategy on the workbench (Short Straddle, Buy Call,
Iron Condor, …).  The ORB engine continues using paper_sessions / paper_trade_*
tables until a future migration bridges it here.

Tables
------
instrument_contract_specs  — lot size, strike step, expiry weekday per instrument/date range
strategy_runs              — one row per executed run (any strategy)
strategy_run_legs          — frozen leg contracts at entry (N legs, generic)
strategy_run_mtm           — aggregate MTM timeline (one row per minute)
strategy_leg_mtm           — per-leg minute pricing (optional, granular)
strategy_run_events        — explainability / audit event log
"""
import uuid

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, Index, Integer, Numeric,
    SmallInteger, String, TIMESTAMP, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class InstrumentContractSpec(Base):
    """
    Static contract metadata for each instrument keyed by effective date range.

    Allows the engine to look up lot size and strike step for any historical date
    without hard-coding values in strategy code.
    """
    __tablename__ = "instrument_contract_specs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument = Column(String(20), nullable=False)          # NIFTY / BANKNIFTY
    effective_from = Column(Date, nullable=False)            # inclusive
    effective_to = Column(Date, nullable=True)               # NULL = still current
    lot_size = Column(Integer, nullable=False)
    strike_step = Column(Integer, nullable=False)            # e.g. 50 for NIFTY
    weekly_expiry_weekday = Column(SmallInteger, nullable=False)  # 0=Mon … 4=Thu (NIFTY = 3)
    estimated_margin_per_lot = Column(Numeric(12, 2), nullable=True)  # rough SPAN margin

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_contract_specs_instrument_from", "instrument", "effective_from"),
    )


class StrategyRun(Base):
    """One row per executed workbench run (any strategy, any run type)."""
    __tablename__ = "strategy_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True)      # soft FK to users.id

    strategy_id = Column(String(60), nullable=False)         # e.g. "short_straddle"
    strategy_version = Column(String(20), nullable=True)     # e.g. "v1"
    run_type = Column(String(40), nullable=False)            # "single_session_backtest"
    executor = Column(String(40), nullable=False)            # "generic_v1"

    instrument = Column(String(20), nullable=False)
    trade_date = Column(Date, nullable=False)
    entry_time = Column(String(5), nullable=True)            # "HH:MM", null if entry conditional
    exit_time = Column(String(5), nullable=True)

    # Lifecycle: pending | running | completed | completed_with_warnings | no_trade | failed
    status = Column(String(30), nullable=False, default="pending")
    exit_reason = Column(String(30), nullable=True)          # TARGET_EXIT / STOP_EXIT / TIME_EXIT / DATA_GAP_EXIT

    # Position
    capital = Column(Numeric(14, 2), nullable=False)
    lot_size = Column(Integer, nullable=True)
    approved_lots = Column(Integer, nullable=True)
    entry_credit_per_unit = Column(Numeric(10, 2), nullable=True)   # CE_entry + PE_entry (for short straddle)
    entry_credit_total = Column(Numeric(12, 2), nullable=True)      # per_unit × lot_size × lots

    # Result
    gross_pnl = Column(Numeric(12, 2), nullable=True)
    total_charges = Column(Numeric(10, 2), nullable=True)
    realized_net_pnl = Column(Numeric(12, 2), nullable=True)

    # Frozen config snapshot (reproducibility)
    config_json = Column(JSONB, nullable=False, default=dict)
    result_json = Column(JSONB, nullable=True)               # summary stats, warnings

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_strategy_runs_user_date", "user_id", "trade_date"),
        Index("ix_strategy_runs_strategy_date", "strategy_id", "trade_date"),
    )


class StrategyRunLeg(Base):
    """Frozen contract leg at the time of entry. N rows per run."""
    __tablename__ = "strategy_run_legs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), nullable=False, index=True)  # FK → strategy_runs.id
    leg_index = Column(SmallInteger, nullable=False)          # 0-based order

    side = Column(String(5), nullable=False)                  # SELL / BUY
    option_type = Column(String(3), nullable=False)           # CE / PE
    strike = Column(Integer, nullable=False)
    expiry_date = Column(Date, nullable=False)
    quantity = Column(Integer, nullable=False)                # lot_size × approved_lots

    entry_price = Column(Numeric(10, 2), nullable=True)
    exit_price = Column(Numeric(10, 2), nullable=True)
    gross_leg_pnl = Column(Numeric(12, 2), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "leg_index", name="uq_strategy_run_legs_run_idx"),
    )


class StrategyRunMtm(Base):
    """Aggregate MTM timeline — one row per minute while trade is open."""
    __tablename__ = "strategy_run_mtm"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)

    spot_close = Column(Numeric(10, 2), nullable=True)
    vix_close = Column(Numeric(8, 4), nullable=True)

    gross_mtm = Column(Numeric(12, 2), nullable=True)
    est_exit_charges = Column(Numeric(10, 2), nullable=True)
    net_mtm = Column(Numeric(12, 2), nullable=True)

    # Trailing stop overlay — null until trail activates
    trail_stop_level = Column(Numeric(12, 2), nullable=True)

    # If an exit fires at this minute, record it here
    event_code = Column(String(30), nullable=True)

    __table_args__ = (
        Index("ix_strategy_run_mtm_run_ts", "run_id", "timestamp"),
    )


class StrategyLegMtm(Base):
    """Per-leg minute pricing — one row per leg per minute."""
    __tablename__ = "strategy_leg_mtm"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    leg_id = Column(UUID(as_uuid=True), nullable=False)       # FK → strategy_run_legs.id
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)

    price = Column(Numeric(10, 2), nullable=True)
    gross_leg_pnl = Column(Numeric(12, 2), nullable=True)
    stale_minutes = Column(SmallInteger, nullable=False, default=0)

    __table_args__ = (
        Index("ix_strategy_leg_mtm_run_ts", "run_id", "timestamp"),
        Index("ix_strategy_leg_mtm_leg_ts", "leg_id", "timestamp"),
    )


class StrategyRunEvent(Base):
    """Explainability / audit event log — one row per notable event."""
    __tablename__ = "strategy_run_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)

    event_type = Column(String(30), nullable=False)           # ENTRY / HOLD / TARGET_EXIT / STOP_EXIT / TIME_EXIT / DATA_GAP / NO_TRADE
    reason_code = Column(String(50), nullable=True)
    reason_text = Column(Text, nullable=True)
    payload_json = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_strategy_run_events_run_ts", "run_id", "timestamp"),
    )

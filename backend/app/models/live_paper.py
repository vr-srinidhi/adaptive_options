"""
Live paper trading ORM models.

Tables
------
live_paper_configs   — one row per "strategy to run live" (single user, enabled flag)
live_paper_sessions  — one row per trading day execution (scheduling + live state)
"""
import uuid

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, Integer, Numeric,
    String, TIMESTAMP, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class LivePaperConfig(Base):
    """
    Persistent config for the live paper trading engine.
    Multiple rows per user are allowed — each represents one parallel time slot.
    """
    __tablename__ = "live_paper_configs"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), nullable=True)          # soft FK to users
    label        = Column(String(50), nullable=True)                   # e.g. "10:15 slot"
    strategy_id  = Column(String(60), nullable=False, default="short_straddle_dual_lock")
    instrument   = Column(String(20), nullable=False, default="NIFTY")
    capital      = Column(Numeric(15, 2), nullable=False, default=2_500_000)
    entry_time   = Column(String(5), nullable=False, default="09:50") # "HH:MM"
    params_json  = Column(JSONB, nullable=False, default=dict)         # strategy-specific params
    enabled      = Column(Boolean, nullable=False, default=False)
    execution_mode = Column(String(10), nullable=False, default="paper")  # paper | live
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at   = Column(TIMESTAMP(timezone=True), server_default=func.now())


class LivePaperSession(Base):
    """
    One row per trading day the engine runs.

    status flow:
      scheduled → waiting → entered → exited
                                    → no_trade
                          → error   (at any point)

    strategy_run_id is set when the trade opens and links to strategy_runs /
    strategy_run_mtm / strategy_run_legs / strategy_run_events — the same
    tables used by historical backtests, so ReplayAnalyzer works unchanged.

    waiting_spot_json holds {timestamp, spot} readings during the pre-entry
    phase (09:14–09:49) for charting the full-day context on the monitor.
    """
    __tablename__ = "live_paper_sessions"
    # Uniqueness enforced by a partial index in migration 0007:
    #   UNIQUE (user_id, trade_date, config_id) WHERE config_id IS NOT NULL
    __table_args__ = {}

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_id       = Column(UUID(as_uuid=True), nullable=True)       # soft FK to live_paper_configs
    user_id         = Column(UUID(as_uuid=True), nullable=True)       # soft FK to users
    trade_date      = Column(Date, nullable=False)
    status          = Column(String(20), nullable=False, default="scheduled")

    # Resolved at ~09:49 from live Zerodha instruments master
    atm_strike      = Column(Integer, nullable=True)
    expiry_date     = Column(Date, nullable=True)
    ce_symbol       = Column(String(60), nullable=True)               # "NFO:NIFTY25MAY24CE24300"
    pe_symbol       = Column(String(60), nullable=True)
    wing_ce_symbol  = Column(String(60), nullable=True)
    wing_pe_symbol  = Column(String(60), nullable=True)
    approved_lots   = Column(Integer, nullable=True)

    # Link to the strategy_run row (set at trade entry)
    strategy_run_id = Column(UUID(as_uuid=True), nullable=True)

    # Live state — updated every minute for fast status polling
    net_mtm_latest  = Column(Numeric(12, 2), nullable=True)
    spot_latest     = Column(Numeric(10, 2), nullable=True)
    lock_status     = Column(String(20), nullable=True, default="none")  # none|profit_locked|loss_locked

    # Final outcome
    exit_reason          = Column(String(30), nullable=True)
    realized_net_pnl     = Column(Numeric(12, 2), nullable=True)

    # Pre-entry spot readings for the waiting-phase chart
    waiting_spot_json    = Column(JSONB, nullable=True, default=list)

    error_message        = Column(Text, nullable=True)
    created_at           = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at           = Column(TIMESTAMP(timezone=True), server_default=func.now())

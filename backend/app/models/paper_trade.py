"""
SQLAlchemy ORM models for the Paper Trading (ORB replay) module.

Six tables:
  paper_sessions            — one row per replay session
  strategy_minute_decisions — one row per market minute (full audit ledger)
  paper_trade_headers       — one row per trade opened
  paper_trade_minute_marks  — per-minute MTM while trade is open
  paper_trade_legs          — long + short option legs
  paper_candle_series       — raw 1-min OHLCV candles per session (SPOT + options)
"""
import uuid
from sqlalchemy import (
    Column, String, Date, Numeric, Integer,
    TIMESTAMP, Text, ForeignKey, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class PaperSession(Base):
    __tablename__ = "paper_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument = Column(String(20), nullable=False)
    session_date = Column(Date, nullable=False)
    capital = Column(Numeric(12, 2), nullable=False)
    status = Column(String(20), default="RUNNING")   # RUNNING / COMPLETED / ERROR
    error_message = Column(Text)
    decision_count = Column(Integer, default=0)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    # Phase 1: session lifecycle terminal state
    final_session_state = Column(String(30))  # OBSERVING / TRADE_CLOSED / SESSION_COMPLETE / etc.


class MinuteDecision(Base):
    __tablename__ = "strategy_minute_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("paper_sessions.id"), nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)
    spot_close = Column(Numeric(10, 2))
    opening_range_high = Column(Numeric(10, 2))
    opening_range_low = Column(Numeric(10, 2))
    trade_state = Column(String(20))    # NO_OPEN_TRADE / OPEN_TRADE
    signal_state = Column(String(20))   # EVALUATE / SKIP_MINUTE
    action = Column(String(30))         # NO_TRADE / ENTER / HOLD / EXIT_*
    reason_code = Column(String(60))
    reason_text = Column(Text)
    candidate_structure = Column(JSONB)
    computed_max_loss = Column(Numeric(10, 2))
    computed_target = Column(Numeric(10, 2))
    # Phase 1: enriched audit fields
    session_state = Column(String(30))      # OBSERVING / TENTATIVE_SIGNAL / OPEN_TRADE / TRADE_CLOSED / SESSION_COMPLETE
    signal_substate = Column(String(30))    # TENTATIVE_BREAKOUT / CONFIRMED_BREAKOUT / FAILED_FIRST_BREAKOUT
    rejection_gate = Column(String(10))     # G1–G7 or None
    price_freshness_json = Column(JSONB)    # {spot_age_min, long_age_min, short_age_min}


class PaperTradeHeader(Base):
    __tablename__ = "paper_trade_headers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("paper_sessions.id"), nullable=False)
    entry_time = Column(TIMESTAMP(timezone=False))
    exit_time = Column(TIMESTAMP(timezone=False))
    bias = Column(String(10))           # BULLISH / BEARISH
    expiry = Column(Date)
    lot_size = Column(Integer)
    approved_lots = Column(Integer)
    entry_debit = Column(Numeric(10, 2))
    total_max_loss = Column(Numeric(10, 2))
    target_profit = Column(Numeric(10, 2))
    realized_gross_pnl = Column(Numeric(10, 2))
    realized_net_pnl = Column(Numeric(10, 2))
    charges = Column(Numeric(10, 2))        # Phase 1: total exit charges
    charges_breakdown_json = Column(JSONB)  # Phase 1: {brokerage, stt, exchange_charges, gst, total}
    status = Column(String(20), default="OPEN")  # OPEN / CLOSED
    exit_reason = Column(String(30))
    long_strike = Column(Integer)
    short_strike = Column(Integer)
    option_type = Column(String(5))     # CE / PE
    # Phase 1: immutable strategy context frozen at entry
    strategy_name = Column(String(50))
    strategy_version = Column(String(20))
    strategy_params_json = Column(JSONB)    # key config params at entry time
    risk_cap = Column(Numeric(12, 2))       # capital × max_risk_pct at entry
    entry_reason_code = Column(String(60))  # ENTER_TRADE
    entry_reason_text = Column(Text)        # full gate reason text


class PaperTradeMinuteMark(Base):
    __tablename__ = "paper_trade_minute_marks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_id = Column(UUID(as_uuid=True), ForeignKey("paper_trade_headers.id"), nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)
    long_leg_price = Column(Numeric(10, 2))
    short_leg_price = Column(Numeric(10, 2))
    current_spread_value = Column(Numeric(10, 2))
    mtm_per_lot = Column(Numeric(10, 2))
    total_mtm = Column(Numeric(10, 2))
    distance_to_target = Column(Numeric(10, 2))
    distance_to_stop = Column(Numeric(10, 2))
    action = Column(String(20))
    reason = Column(String(200))
    # Phase 1: gross/net split
    gross_mtm = Column(Numeric(10, 2))
    estimated_exit_charges = Column(Numeric(10, 2))
    estimated_net_mtm = Column(Numeric(10, 2))
    price_freshness_json = Column(JSONB)    # {long_age_min, short_age_min}


class PaperTradeLeg(Base):
    __tablename__ = "paper_trade_legs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_id = Column(UUID(as_uuid=True), ForeignKey("paper_trade_headers.id"), nullable=False)
    leg_side = Column(String(10))       # LONG / SHORT
    option_type = Column(String(5))     # CE / PE
    strike = Column(Integer)
    expiry = Column(Date)
    entry_price = Column(Numeric(10, 2))
    exit_price = Column(Numeric(10, 2))


class PaperCandleSeries(Base):
    __tablename__ = "paper_candle_series"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("paper_sessions.id"), nullable=False)
    # e.g. "SPOT", "22900_CE_WEEKLY", "22900_CE_MONTHLY", "22850_CE_WEEKLY", etc.
    series_type = Column(String(80), nullable=False)
    # [{time, open, high, low, close, volume}, ...]
    candles = Column(JSONB, nullable=False)

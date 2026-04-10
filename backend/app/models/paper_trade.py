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
    status = Column(String(20), default="OPEN")  # OPEN / CLOSED
    exit_reason = Column(String(30))
    long_strike = Column(Integer)
    short_strike = Column(Integer)
    option_type = Column(String(5))     # CE / PE


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

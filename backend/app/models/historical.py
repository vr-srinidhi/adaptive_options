"""
Historical data warehouse ORM models.

Tables:
  trading_days    — one row per trading date; tracks per-file availability + ingestion status
  spot_candles    — 1-min OHLCV for NIFTY spot
  vix_candles     — 1-min OHLCV for India VIX
  futures_candles — 1-min OHLCV+OI for NIFTY futures (available from 2026-01-28)
  options_candles — 1-min OHLCV+ltp+OI for NIFTY options (~67 M rows expected)
  session_batches — groups multiple historical backtest sessions into one run
"""
import uuid
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, Index, Integer, Numeric,
    String, TIMESTAMP, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class TradingDay(Base):
    __tablename__ = "trading_days"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_date = Column(Date, unique=True, nullable=False, index=True)

    # Source file names (stored for audit / re-ingestion)
    spot_file_name = Column(Text)
    futures_file_name = Column(Text)
    options_file_name = Column(Text)
    vix_file_name = Column(Text)

    # Per-file availability flags
    spot_available = Column(Boolean, default=False, nullable=False)
    futures_available = Column(Boolean, default=False, nullable=False)
    options_available = Column(Boolean, default=False, nullable=False)
    vix_available = Column(Boolean, default=False, nullable=False)

    # Ingestion lifecycle
    # pending | in_progress | completed | completed_with_warnings | failed
    ingestion_status = Column(String(30), default="pending", nullable=False)
    backtest_ready = Column(Boolean, default=False, nullable=False)
    ingestion_notes = Column(Text)

    # Row counts for quick sanity checks
    spot_row_count = Column(Integer)
    options_row_count = Column(Integer)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class SpotCandle(Base):
    __tablename__ = "spot_candles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)
    symbol = Column(String(20), nullable=False)
    open = Column(Numeric(10, 2))
    high = Column(Numeric(10, 2))
    low = Column(Numeric(10, 2))
    close = Column(Numeric(10, 2))
    volume = Column(BigInteger, default=0)
    source_file = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_spot_candles_date_ts", "trade_date", "timestamp"),
        Index("ix_spot_candles_symbol_date", "symbol", "trade_date"),
    )


class VixCandle(Base):
    __tablename__ = "vix_candles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)
    symbol = Column(String(20), nullable=False)
    open = Column(Numeric(10, 2))
    high = Column(Numeric(10, 2))
    low = Column(Numeric(10, 2))
    close = Column(Numeric(10, 2))
    source_file = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_vix_candles_date_ts", "trade_date", "timestamp"),
    )


class FuturesCandle(Base):
    __tablename__ = "futures_candles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)
    symbol = Column(String(20), nullable=False)
    expiry_date = Column(Date, nullable=False)
    open = Column(Numeric(10, 2))
    high = Column(Numeric(10, 2))
    low = Column(Numeric(10, 2))
    close = Column(Numeric(10, 2))
    volume = Column(BigInteger, default=0)
    open_interest = Column(BigInteger, default=0)
    source_file = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_futures_candles_date_ts", "trade_date", "timestamp"),
        Index("ix_futures_candles_symbol_expiry_ts", "symbol", "expiry_date", "timestamp"),
    )


class OptionsCandle(Base):
    """
    Largest table — ~67 M rows for the full 2022-2026 dataset.
    Primary access pattern: WHERE trade_date=$1 AND expiry_date=$2
                            AND option_type=$3 AND strike=$4
    covered by ix_options_candles_lookup.
    """
    __tablename__ = "options_candles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    timestamp = Column(TIMESTAMP(timezone=False), nullable=False)
    symbol = Column(String(20), nullable=False)
    expiry_date = Column(Date, nullable=False)
    option_type = Column(String(5), nullable=False)   # CE / PE
    strike = Column(Integer, nullable=False)           # e.g. 22900
    open = Column(Numeric(10, 2))
    high = Column(Numeric(10, 2))
    low = Column(Numeric(10, 2))
    close = Column(Numeric(10, 2))
    volume = Column(BigInteger, default=0)
    open_interest = Column(BigInteger, default=0)
    ltp = Column(Numeric(10, 2))
    source_file = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        # Engine lookup: fetch all candles for a specific strike/expiry/type on a date
        Index(
            "ix_options_candles_lookup",
            "trade_date", "expiry_date", "option_type", "strike", "timestamp",
        ),
        # Broad date scan (e.g. available contracts for a day)
        Index("ix_options_candles_date_ts", "trade_date", "timestamp"),
    )


class SessionBatch(Base):
    """
    Groups N historical backtest sessions into one batch run.
    One row per "run historical backtest over date range X–Y" action.
    """
    __tablename__ = "session_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    # batch_type: historical_backtest (only type for now)
    batch_type = Column(String(30), default="historical_backtest", nullable=False)
    # draft | queued | running | completed | completed_with_warnings | failed | cancelled
    status = Column(String(30), default="draft", nullable=False)

    # Strategy config frozen at batch creation time
    strategy_id = Column(String(50), nullable=False)
    strategy_version = Column(String(20))
    strategy_config_snapshot = Column(JSONB, nullable=False)

    # Date range
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    # latest_first | oldest_first
    execution_order = Column(String(20), default="latest_first", nullable=False)

    # Progress counters (updated live during run)
    total_sessions = Column(Integer, default=0, nullable=False)
    completed_sessions = Column(Integer, default=0, nullable=False)
    failed_sessions = Column(Integer, default=0, nullable=False)
    skipped_sessions = Column(Integer, default=0, nullable=False)
    total_pnl = Column(Numeric(12, 2), default=0)

    notes = Column(Text)
    created_by = Column(UUID(as_uuid=True), nullable=True)   # FK to users.id (soft ref)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

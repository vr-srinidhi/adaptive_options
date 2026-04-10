import uuid
from sqlalchemy import Column, String, Date, Numeric, Integer, Time, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base



class BacktestSession(Base):
    __tablename__ = "backtest_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument = Column(String(20), nullable=False)
    session_date = Column(Date, nullable=False)
    capital = Column(Numeric(12, 2), nullable=False)
    regime = Column(String(20))
    iv_rank = Column(Integer)
    strategy = Column(String(30))
    entry_time = Column(Time)
    exit_time = Column(Time)
    exit_reason = Column(String(30))
    spot_in = Column(Numeric(10, 2))
    spot_out = Column(Numeric(10, 2))
    lots = Column(Integer, default=0)
    max_profit = Column(Numeric(10, 2))
    max_loss = Column(Numeric(10, 2))
    pnl = Column(Numeric(10, 2))
    pnl_pct = Column(Numeric(6, 4))
    wl = Column(String(15))
    ema5 = Column(Numeric(10, 2))
    ema20 = Column(Numeric(10, 2))
    rsi14 = Column(Numeric(6, 2))
    legs = Column(JSONB)
    min_data = Column(JSONB)
    no_trade_reason = Column(String(30))
    expiry_date = Column(Date)
    data_source = Column(String(20))
    regime_detail = Column(String(30))
    signal_type = Column(String(30))
    signal_score = Column(Integer)
    atr14 = Column(Numeric(10, 2))
    r_multiple = Column(Numeric(6, 2))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

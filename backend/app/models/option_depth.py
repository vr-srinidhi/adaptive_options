"""
Order-book depth captured alongside the live paper poll.

Every P&L figure this system produces assumes we transact at the observed
price. Real fills happen at the bid (when selling) or the ask (when buying),
and that spread is paid on every leg of every session. This table records what
was actually on offer at each decision minute so the gap can be measured.

Depth is only available live — Zerodha does not serve it historically — so a
session that is not captured can never be analysed later.

Written on a best-effort basis: capture failures must never affect trading.
"""
from sqlalchemy import (
    Column, Date, DateTime, Integer, Numeric, String, BigInteger, Index,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class OptionDepthSnapshot(Base):
    __tablename__ = "option_depth_snapshots"

    id = Column(BigInteger, primary_key=True)

    trade_date = Column(Date, nullable=False)
    timestamp  = Column(DateTime(timezone=False), nullable=False)
    symbol     = Column(String(60), nullable=False)   # NFO:NIFTY25AUG24300CE

    # Parsed from the symbol where available; nullable so a capture never fails
    # on an unexpected symbol format.
    strike      = Column(Integer, nullable=True)
    option_type = Column(String(5), nullable=True)

    # Passed in by the engine, which resolved it from the instruments master.
    expiry_date = Column(Date, nullable=True)

    last_price = Column(Numeric(12, 2), nullable=True)

    # Top of book — the numbers a realistic fill actually uses.
    bid     = Column(Numeric(12, 2), nullable=True)
    ask     = Column(Numeric(12, 2), nullable=True)
    bid_qty = Column(Integer, nullable=True)
    ask_qty = Column(Integer, nullable=True)

    # Full 5-level ladder, kept for the fill model that walks the book later.
    depth_json = Column(JSONB, nullable=True)

    # Cumulative traded volume and open interest at capture time, for context.
    volume        = Column(BigInteger, nullable=True)
    open_interest = Column(BigInteger, nullable=True)

    # Which live paper session produced this poll (nullable: not an FK, so
    # deleting a session never blocks on captured market data).
    session_id = Column(String(40), nullable=True)

    __table_args__ = (
        Index("ix_option_depth_date_symbol_ts", "trade_date", "symbol", "timestamp"),
    )

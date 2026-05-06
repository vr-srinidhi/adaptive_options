"""Audit model for scheduled live market data warehouse syncs."""
import uuid

from sqlalchemy import Column, Date, Integer, String, TIMESTAMP, Text, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class LiveDataSyncRun(Base):
    __tablename__ = "live_data_sync_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_date = Column(Date, nullable=False, index=True)
    started_at = Column(TIMESTAMP(timezone=True), nullable=False)
    completed_at = Column(TIMESTAMP(timezone=True))
    triggered_by = Column(String(30), nullable=False, default="scheduler")

    token_status = Column(String(40), nullable=False)
    status = Column(String(40), nullable=False)

    spot_rows = Column(Integer, nullable=False, default=0)
    vix_rows = Column(Integer, nullable=False, default=0)
    futures_rows = Column(Integer, nullable=False, default=0)
    options_rows = Column(Integer, nullable=False, default=0)
    option_contracts = Column(Integer, nullable=False, default=0)

    expiries_json = Column(JSONB)
    failed_items_json = Column(JSONB)
    notes = Column(Text)
    error_message = Column(Text)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_live_data_sync_runs_trade_date_started", "trade_date", "started_at"),
    )

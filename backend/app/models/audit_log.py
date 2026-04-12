"""
Immutable audit log for security-relevant events.
"""
import uuid

from sqlalchemy import Column, ForeignKey, String, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type = Column(String(50), nullable=False)
    # e.g. LOGIN, LOGOUT, REGISTER, ZERODHA_CONNECT, RUN_BACKTEST,
    #      RUN_PAPER_SESSION, DELETE_BACKTEST_RESULTS
    detail = Column(JSONB)
    ip_address = Column(String(45))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

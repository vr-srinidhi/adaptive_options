"""
Stores encrypted broker access tokens per user.
One row per (user, broker) — upserted on each Zerodha session establishment.
"""
import uuid

from sqlalchemy import Column, Date, ForeignKey, String, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class BrokerToken(Base):
    __tablename__ = "broker_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    broker = Column(String(20), nullable=False, default="ZERODHA")
    encrypted_token = Column(Text, nullable=False)
    token_date = Column(Date, nullable=False)   # calendar date the token was minted
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

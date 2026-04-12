"""
Encrypted broker token storage and retrieval.
"""
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_token import BrokerToken


async def store_broker_token(
    db: AsyncSession,
    user_id: uuid.UUID,
    access_token: str,
    broker: str = "ZERODHA",
) -> None:
    """Encrypt and persist (upsert) a broker access token for a user."""
    from app.core.security import encrypt_token

    encrypted = encrypt_token(access_token)
    today = date.today()

    existing = (
        await db.execute(
            select(BrokerToken).where(
                BrokerToken.user_id == user_id,
                BrokerToken.broker == broker,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.encrypted_token = encrypted
        existing.token_date = today
    else:
        db.add(
            BrokerToken(
                user_id=user_id,
                broker=broker,
                encrypted_token=encrypted,
                token_date=today,
            )
        )

    await db.commit()


async def get_broker_token(
    db: AsyncSession,
    user_id: uuid.UUID,
    broker: str = "ZERODHA",
) -> Optional[str]:
    """Return the decrypted access token, or None if not stored."""
    from app.core.security import decrypt_token

    row = (
        await db.execute(
            select(BrokerToken).where(
                BrokerToken.user_id == user_id,
                BrokerToken.broker == broker,
            )
        )
    ).scalar_one_or_none()

    if not row:
        return None
    try:
        return decrypt_token(row.encrypted_token)
    except Exception:
        return None

"""
Fire-and-forget audit logging helper.

Usage:
    import asyncio
    asyncio.ensure_future(log_event(db, "LOGIN", user_id=user.id, ip_address=ip))
"""
import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def log_event(
    db: AsyncSession,
    event_type: str,
    user_id: Optional[uuid.UUID] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Write an audit log entry. Swallows exceptions so it never blocks callers."""
    try:
        from app.models.audit_log import AuditLog

        entry = AuditLog(
            user_id=user_id,
            event_type=event_type,
            detail=detail,
            ip_address=ip_address,
        )
        db.add(entry)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("Audit log write failed [%s]: %s", event_type, exc)

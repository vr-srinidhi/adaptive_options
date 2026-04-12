"""
Fire-and-forget audit logging helper.

Opens its own DB session so it is independent of the request session lifecycle.

Usage:
    import asyncio
    asyncio.ensure_future(log_event("LOGIN", user_id=user.id, ip_address=ip))
"""
import logging
import uuid
from typing import Optional

log = logging.getLogger(__name__)


async def log_event(
    event_type: str,
    user_id: Optional[uuid.UUID] = None,
    detail: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Write an audit log entry. Swallows exceptions so it never blocks callers."""
    try:
        from app.database import AsyncSessionLocal
        from app.models.audit_log import AuditLog

        async with AsyncSessionLocal() as session:
            entry = AuditLog(
                user_id=user_id,
                event_type=event_type,
                detail=detail,
                ip_address=ip_address,
            )
            session.add(entry)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("Audit log write failed [%s]: %s", event_type, exc)

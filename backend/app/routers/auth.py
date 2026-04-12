"""
Zerodha authentication endpoints.

Flow:
  1. GET  /auth/zerodha/login-url  (authenticated)
       → returns the Zerodha OAuth URL the user must open in a browser

  2. User logs in via Zerodha, which redirects to your app with
       ?request_token=<token>&action=login&status=success

  3. POST /auth/zerodha/session  { "request_token": "<token>" }  (authenticated)
       → exchanges the request_token for an access_token
       → stores encrypted access_token in DB (keyed to the authenticated user)
       → access_token is valid until 6 AM IST next day

  4. GET  /auth/zerodha/status  (authenticated)
       → confirms whether the current user has a stored Zerodha session
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models.user import User
from app.services import zerodha_client
from app.services.audit import log_event
from app.services.token_store import get_broker_token, store_broker_token

router = APIRouter(prefix="/auth/zerodha", tags=["auth"])


class SessionRequest(BaseModel):
    request_token: str


@router.get("/login-url")
def get_login_url(user: User = Depends(get_current_active_user)):
    """Return the Zerodha login URL. Requires app authentication."""
    try:
        url = zerodha_client.get_login_url()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"login_url": url}


@router.post("/session")
async def create_session(
    req: SessionRequest,
    request: Request,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a Zerodha request_token for an access_token.
    The encrypted token is stored in the database for the authenticated user.
    Call this once per trading day (tokens expire at 6 AM IST).
    """
    try:
        data = zerodha_client.generate_session(req.request_token)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Zerodha authentication failed: {exc}",
        )

    access_token = data.get("access_token", "")
    await store_broker_token(db, user.id, access_token)
    zerodha_client.invalidate_instruments_cache()

    asyncio.ensure_future(
        log_event(
            "ZERODHA_CONNECT",
            user_id=user.id,
            ip_address=request.client.host if request.client else "unknown",
        )
    )
    return {
        "status": "ok",
        "user_id": data.get("user_id"),
        "user_name": data.get("user_name"),
    }


@router.get("/status")
async def get_status(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Check whether the current user has a valid stored Zerodha token."""
    token = await get_broker_token(db, user.id)
    if not token:
        return {"authenticated": False, "profile": None}
    try:
        # Use a temporary KiteConnect instance so we don't mutate the shared singleton.
        from kiteconnect import KiteConnect
        tmp = KiteConnect(api_key=zerodha_client.API_KEY)
        tmp.set_access_token(token)
        profile = tmp.profile()
        return {"authenticated": True, "profile": profile}
    except Exception:
        return {"authenticated": False, "profile": None}

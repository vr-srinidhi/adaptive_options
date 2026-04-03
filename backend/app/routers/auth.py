"""
Zerodha authentication endpoints.

Flow:
  1. GET  /auth/zerodha/login-url
       → returns the Zerodha OAuth URL the user must open in a browser

  2. User logs in via Zerodha, which redirects to your app with
       ?request_token=<token>&action=login&status=success

  3. POST /auth/zerodha/session  { "request_token": "<token>" }
       → exchanges the request_token for an access_token
       → stores it in-process for all subsequent Zerodha API calls
       → access_token is valid until 6 AM IST next day

  4. GET  /auth/zerodha/status
       → confirms whether the current session is active
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import zerodha_client

router = APIRouter(prefix="/auth/zerodha", tags=["auth"])


class SessionRequest(BaseModel):
    request_token: str


@router.get("/login-url")
def get_login_url():
    """
    Return the Zerodha login URL.
    Open this URL in a browser to initiate the OAuth flow.
    After login Zerodha redirects with ?request_token=<token>.
    """
    try:
        url = zerodha_client.get_login_url()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"login_url": url}


@router.post("/session")
def create_session(req: SessionRequest):
    """
    Exchange a Zerodha request_token for an access_token.
    The token is stored in memory and used for all Zerodha API calls
    until the process restarts or a new session is created.

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
    # Invalidate instrument cache so fresh data is loaded with new token
    zerodha_client.invalidate_instruments_cache()
    return {
        "status": "ok",
        "user_id":   data.get("user_id"),
        "user_name": data.get("user_name"),
        "access_token": data.get("access_token"),
    }


@router.get("/status")
def get_status():
    """
    Check whether an active Zerodha session exists.
    Returns the user profile on success, or authenticated=false.
    """
    token = zerodha_client.get_access_token()
    if not token:
        return {"authenticated": False, "profile": None}
    try:
        profile = zerodha_client.get_profile()
        return {"authenticated": True, "profile": profile}
    except Exception:
        return {"authenticated": False, "profile": None}

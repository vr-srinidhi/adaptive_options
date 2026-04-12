"""
App-level authentication endpoints.

POST /api/users/register  — create account
POST /api/users/login     — authenticate; returns access_token + sets refresh cookie
POST /api/users/refresh   — exchange refresh cookie for new access_token
POST /api/users/logout    — clear refresh cookie
GET  /api/users/me        — current user profile
"""
import asyncio

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.rate_limit import limiter
from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models.user import User
from app.services.audit import log_event

router = APIRouter(prefix="/users", tags=["users"])

_REFRESH_COOKIE = "refresh_token"
_COOKIE_OPTS = dict(httponly=True, secure=False, samesite="lax", path="/api/users/refresh")


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    user = User(email=body.email, hashed_password=hash_password(body.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)

    asyncio.ensure_future(
        log_event(db, "REGISTER", user_id=user.id, ip_address=_get_ip(request))
    )
    return {"id": str(user.id), "email": user.email}


@router.post("/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled.")

    access_token = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    response.set_cookie(
        _REFRESH_COOKIE,
        refresh_token,
        max_age=60 * 60 * 24 * 7,
        **_COOKIE_OPTS,
    )

    asyncio.ensure_future(
        log_event(db, "LOGIN", user_id=user.id, ip_address=_get_ip(request))
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/refresh")
async def refresh(
    response: Response,
    refresh_token: str = Cookie(None, alias=_REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token.")

    from jose import JWTError

    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type.")
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

    import uuid
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    ).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled.")

    access_token = create_access_token({"sub": str(user.id)})
    new_refresh = create_refresh_token({"sub": str(user.id)})

    response.set_cookie(
        _REFRESH_COOKIE,
        new_refresh,
        max_age=60 * 60 * 24 * 7,
        **_COOKIE_OPTS,
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(_REFRESH_COOKIE, path="/api/users/refresh")
    return {"status": "logged_out"}


@router.get("/me")
async def me(user: User = Depends(get_current_active_user)):
    return {
        "id": str(user.id),
        "email": user.email,
        "is_active": user.is_active,
        "created_at": str(user.created_at) if user.created_at else None,
    }

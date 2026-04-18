import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.rate_limit import limiter
from app.database import init_db
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.routers import backtest, auth, paper_trading, users
from app.routers import historical, backtests

app = FastAPI(title="Adaptive Options API", version="1.0.0")

# ── Rate limiting ──────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ───────────────────────────────────────────────────────────────────────
_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Security headers ───────────────────────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)


@app.on_event("startup")
async def startup():
    # Fail-fast in production if insecure defaults are still set
    if settings.ENVIRONMENT == "production":
        if settings.SECRET_KEY.startswith("CHANGE_ME"):
            raise RuntimeError("SECRET_KEY must be set in production.")
        if not settings.BROKER_TOKEN_ENCRYPTION_KEY:
            raise RuntimeError("BROKER_TOKEN_ENCRYPTION_KEY must be set in production.")
    await init_db()


app.include_router(backtest.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(paper_trading.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(historical.router, prefix="/api/historical", tags=["historical"])
app.include_router(backtests.router, prefix="/api/backtests", tags=["backtests"])


@app.get("/health")
async def health():
    return {"status": "ok"}

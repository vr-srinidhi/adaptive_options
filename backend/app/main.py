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
from app.routers import workbench
from app.routers import live_paper

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
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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

    # Bootstrap schema first (idempotent create_all + seed data).
    # Alembic migrations run after so they only apply additive changes on top
    # of an already-consistent schema, avoiding conflicts on fresh databases.
    await init_db()

    # Apply pending Alembic migrations after init_db() has ensured all base
    # tables exist.  Using run_sync so we stay on the same event loop.
    import asyncio
    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_command
    alembic_cfg = AlembicConfig("/app/alembic.ini")
    await asyncio.get_event_loop().run_in_executor(
        None, alembic_command.upgrade, alembic_cfg, "head"
    )

    # Start the live paper trading scheduler
    from app.services.scheduler import init_scheduler
    init_scheduler()

    # Resume any session interrupted by a mid-day process restart
    from app.services.live_paper_engine import check_and_resume_sessions
    await check_and_resume_sessions()


@app.on_event("shutdown")
async def shutdown():
    from app.services.scheduler import shutdown_scheduler
    shutdown_scheduler()


app.include_router(backtest.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(paper_trading.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(historical.router, prefix="/api/historical", tags=["historical"])
app.include_router(backtests.router, prefix="/api/backtests", tags=["backtests"])
app.include_router(workbench.router)
app.include_router(live_paper.router)


@app.get("/health")
async def health():
    return {"status": "ok"}

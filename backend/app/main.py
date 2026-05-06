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

    # Apply pending Alembic migrations.
    # On a fresh database init_db() has already created all tables via
    # create_all(), so we stamp alembic at head before running upgrade to
    # prevent old non-idempotent migrations (e.g. 0003 create_table) from
    # being replayed against tables that already exist.
    import asyncio
    from pathlib import Path
    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_command
    from sqlalchemy import text as _sa_text
    from app.database import engine as _async_engine

    # alembic.ini lives one directory above this file (backend/alembic.ini)
    _alembic_ini = Path(__file__).parent.parent / "alembic.ini"
    alembic_cfg = AlembicConfig(str(_alembic_ini))

    # Check alembic_version via the async engine — synchronous inspect() is
    # explicitly unsupported for AsyncEngine and would raise NoInspectionAvailable.
    # to_regclass() returns NULL (not an error) when the table is absent.
    async with _async_engine.connect() as _conn:
        _vt_oid = (await _conn.execute(
            _sa_text("SELECT to_regclass('public.alembic_version')")
        )).scalar()
        _current = None
        if _vt_oid:
            _current = (await _conn.execute(
                _sa_text("SELECT version_num FROM alembic_version")
            )).fetchone()

    # Alembic stamp/upgrade call asyncio.run() via env.py — they must run in
    # a thread where no event loop is active; run_in_executor provides that.
    if not _current:
        # Fresh DB — already bootstrapped by init_db(); stamp at head so
        # upgrade head becomes a no-op and old non-idempotent migrations are
        # not replayed against tables that create_all() already created.
        await asyncio.get_event_loop().run_in_executor(
            None, alembic_command.stamp, alembic_cfg, "head"
        )
    else:
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

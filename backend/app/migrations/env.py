"""
Alembic migration environment.

Supports async SQLAlchemy (asyncpg) for the online mode.
Run migrations: alembic upgrade head
Generate new:   alembic revision --autogenerate -m "description"
"""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context

# Alembic Config object — gives access to alembic.ini values
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Import all models so their tables are in target_metadata ─────────────────
from app.database import Base, DATABASE_URL  # noqa: E402
from app.models import session  # noqa: F401
from app.models import paper_trade  # noqa: F401
from app.models import user  # noqa: F401
from app.models import broker_token  # noqa: F401
from app.models import audit_log  # noqa: F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL script)."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    connectable = create_async_engine(DATABASE_URL, future=True)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

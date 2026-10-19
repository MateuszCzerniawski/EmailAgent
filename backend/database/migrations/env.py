"""
Alembic environment configuration.

Uses an async engine (asyncpg) — the standard sync template is replaced with
the async pattern: engine.connect() → connection.run_sync(do_run_migrations).

DATABASE_URL is read from the environment (or .env file) so that credentials
are never stored in alembic.ini.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Make sure the backend/ directory is on the path so that
# "from database.models.orm import Base" resolves correctly.
# ---------------------------------------------------------------------------

_backend_dir = Path(__file__).parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from database.models.orm import Base  # noqa: E402

# ---------------------------------------------------------------------------
# Alembic Config object — gives access to alembic.ini values
# ---------------------------------------------------------------------------

config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The schema metadata Alembic uses for --autogenerate support.
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Resolve DATABASE_URL from environment (overrides alembic.ini placeholder)
# ---------------------------------------------------------------------------

def _get_database_url() -> str:
    """Return DATABASE_URL from environment, falling back to alembic.ini."""
    # Try .env file in the backend/ directory
    env_file = _backend_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")

    # Try OS environment variable
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    # Fall back to alembic.ini value
    return config.get_main_option("sqlalchemy.url")


# ---------------------------------------------------------------------------
# Migration runners
# ---------------------------------------------------------------------------

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection)."""
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live async database connection."""
    url = _get_database_url()
    connectable = create_async_engine(url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for 'online' mode — called by Alembic CLI."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

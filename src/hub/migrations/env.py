"""Alembic environment for the hub business-schema chain.

Independent from the framework chain: hub target_metadata, and its own
version table (``alembic_version_hub``) so the two chains never mix.
"""

import asyncio
import threading
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from agent_server.config.settings import settings
from hub.db import Base

config = context.config

# Same configparser %-interpolation workaround as the framework env.py.
config.attributes.setdefault("sqlalchemy.url", settings.db.database_url)

if config.config_file_name is not None and threading.current_thread() is threading.main_thread():
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

VERSION_TABLE = "alembic_version_hub"


def _get_database_url() -> str | None:
    """Read URL from config.attributes (set in env.py) or fall back to ini."""
    url = config.attributes.get("sqlalchemy.url")
    if url is not None:
        return url
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=_get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(connection=connection, target_metadata=target_metadata, version_table=VERSION_TABLE)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against an async engine."""
    configuration = config.get_section(config.config_ini_section) or {}
    url = _get_database_url()
    if url is not None:
        configuration["sqlalchemy.url"] = url

    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

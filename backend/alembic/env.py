"""Alembic environment.

The application runtime uses the async ``asyncpg`` driver, but migrations run
through a synchronous ``psycopg2`` connection: the initial migration executes a
multi-statement DDL script (dollar-quoted plpgsql functions and triggers) that
asyncpg's prepared-statement protocol cannot run in one call. The async DSN is
converted to its sync equivalent here so a single ``DATABASE_URL`` configures both.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sync_url(async_url: str) -> str:
    """Convert an async SQLAlchemy URL to its synchronous psycopg2 form."""
    return async_url.replace("+asyncpg", "+psycopg2")


config.set_main_option("sqlalchemy.url", _sync_url(str(get_settings().database_url)))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

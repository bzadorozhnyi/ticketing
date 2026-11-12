import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import core.models_registry  # noqa: F401
from core.db import BaseModel
from core.migrations import connect_args, run_with_lock_retries
from core.settings import settings

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
# configparser treats "%" as interpolation: a password containing it must be escaped.
config.set_main_option("sqlalchemy.url", settings.DB.url.replace("%", "%%"))

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseModel.metadata

# Columns the code no longer maps but a later migration has yet to drop.
DROPPED_LATER = {("orders", "payment_token")}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (
        type_ == "column"
        and compare_to is None
        and (obj.table.name, name) in DROPPED_LATER
    )


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Every attempt takes a new connection, whose session gives up a lock after
    `MIGRATIONS__LOCK_TIMEOUT_SECONDS`; see core/migrations.py."""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args(settings.MIGRATIONS),
    )

    async def attempt() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)

    await run_with_lock_retries(attempt, settings=settings.MIGRATIONS)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

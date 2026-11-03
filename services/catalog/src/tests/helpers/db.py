from urllib.parse import urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _db_name(*, db_url: str) -> str:
    return urlparse(db_url).path.lstrip("/")


def _db_root_url(*, db_url: str) -> str:
    """The same server, connected to the default `postgres` maintenance database."""
    return urlunparse(urlparse(db_url)._replace(path="/postgres"))


async def drop_db(*, db_url: str) -> None:
    """Drops the database at `db_url`, connecting to the maintenance database —
    you can't drop a database you're connected to.
    """
    db_name = _db_name(db_url=db_url)
    engine = create_async_engine(
        _db_root_url(db_url=db_url), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity WHERE datname = :name"
                ),
                {"name": db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    finally:
        await engine.dispose()


async def create_db(*, db_url: str) -> None:
    db_name = _db_name(db_url=db_url)
    engine = create_async_engine(
        _db_root_url(db_url=db_url), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await engine.dispose()

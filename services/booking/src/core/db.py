from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from loguru import logger
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.exceptions import ServiceUnavailableException
from core.settings import settings

engine = create_async_engine(
    settings.DB.url,
    connect_args={"timeout": settings.DB.CONNECT_TIMEOUT_SECONDS},
    pool_pre_ping=settings.DB.POOL_PRE_PING,
)
async_session = async_sessionmaker(engine, expire_on_commit=False)

# Reads that may be as old as the replica's lag. Without a replica they use the primary.
replica_engine = (
    create_async_engine(
        settings.DB_REPLICA.url,
        connect_args={"timeout": settings.DB_REPLICA.CONNECT_TIMEOUT_SECONDS},
        pool_pre_ping=settings.DB_REPLICA.POOL_PRE_PING,
    )
    if settings.DB_REPLICA
    else engine
)
replica_session = async_sessionmaker(replica_engine, expire_on_commit=False)

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class BaseModel(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)
    type_annotation_map = {datetime: DateTime(timezone=True)}

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# The server refused the session or is going away: shutting down, crashed, starting up,
# out of connections. Class 08 is a lost or refused connection.
UNAVAILABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03", "53300"})


def is_connection_error(exc: Exception) -> bool:
    """asyncpg raises plain OSError subclasses when the socket fails; SQLAlchemy
    marks a lost open connection as invalidated. A server that answers the login
    with an error (starting up, too many connections) gives neither: only its
    SQLSTATE tells.
    """
    if isinstance(exc, DBAPIError):
        sqlstate = getattr(exc.orig, "sqlstate", None) or ""
        return (
            exc.connection_invalidated
            or sqlstate in UNAVAILABLE_SQLSTATES
            or sqlstate.startswith("08")
        )
    return isinstance(exc, OSError)


@asynccontextmanager
async def _guarded(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    async with maker() as session:
        try:
            yield session
        except (OSError, DBAPIError) as exc:
            if is_connection_error(exc):
                logger.bind(error=repr(exc)).warning("Database is unavailable")
                raise ServiceUnavailableException("Database is unavailable") from exc
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _guarded(async_session) as session:
        yield session


async def get_replica_session() -> AsyncIterator[AsyncSession]:
    async with _guarded(replica_session) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReplicaSessionDep = Annotated[AsyncSession, Depends(get_replica_session)]

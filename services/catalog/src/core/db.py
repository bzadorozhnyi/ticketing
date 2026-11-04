from collections.abc import AsyncIterator
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


def is_connection_error(exc: Exception) -> bool:
    """asyncpg raises plain OSError subclasses while connecting; SQLAlchemy only
    wraps failures on an already open connection.
    """
    if isinstance(exc, DBAPIError):
        return exc.connection_invalidated
    return isinstance(exc, OSError)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        try:
            yield session
        except (OSError, DBAPIError) as exc:
            if is_connection_error(exc):
                logger.bind(error=repr(exc)).warning("Database is unavailable")
                raise ServiceUnavailableException("Database is unavailable") from exc
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]

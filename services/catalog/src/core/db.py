from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import MetaData, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.settings import settings

engine = create_async_engine(
    settings.DB.url,
    connect_args={"timeout": settings.DB.CONNECT_TIMEOUT_SECONDS},
)
async_session = async_sessionmaker(engine, expire_on_commit=False)

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class BaseModel(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]

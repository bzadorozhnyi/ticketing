from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class AsyncFactory:
    """Wraps a synchronous factory_boy Factory class so every entry point
    (create, create_batch, build, build_batch, ...) runs through
    AsyncSession.run_sync(). factory_boy's session calls are synchronous,
    and an async engine only allows DBAPI I/O inside the greenlet
    run_sync() sets up — calling them directly raises MissingGreenlet.
    """

    def __init__(self, *, factory_class: type, session: AsyncSession):
        self._factory_class = factory_class
        self._session = session

    @property
    def factory_class(self) -> type:
        return self._factory_class

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._factory_class, name)

        async def call(*args, **kwargs):
            return await self._session.run_sync(lambda _: target(*args, **kwargs))

        return call

    async def __call__(self, *args, **kwargs):
        return await self._session.run_sync(
            lambda _: self._factory_class(*args, **kwargs)
        )

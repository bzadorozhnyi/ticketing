import asyncio
import functools
import random
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends

from core.cache.memory import InMemoryStore
from core.cache.store import CacheStore
from core.settings import settings


class ResponseCache:
    """Ready response bodies, kept for about `ttl_seconds`.

    Single flight: while one request loads a key, the others asking for it wait for that
    load instead of loading it too, so a cold or expired key under a spike costs one
    query, not one per request. A failed load is not cached and reaches every waiter.
    The TTL varies by `ttl_jitter` (a share of it), so keys loaded together do not all
    expire in the same second.
    """

    def __init__(self, *, store: CacheStore, ttl_seconds: float, ttl_jitter: float):
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.ttl_jitter = ttl_jitter
        self._loading: dict[str, asyncio.Future[bytes]] = {}

    async def get_or_load(
        self, *, key: str, load: Callable[[], Awaitable[bytes]]
    ) -> bytes:
        while True:
            cached = await self.store.get(key=key)
            if cached is not None:
                return cached
            pending = self._loading.get(key)
            if pending is None:
                return await self._load(key=key, load=load)
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                # The request that was loading went away; this one tries again.
                if not pending.cancelled():
                    raise

    async def _load(self, *, key: str, load: Callable[[], Awaitable[bytes]]) -> bytes:
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._loading[key] = future
        try:
            value = await load()
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as exc:
            future.set_exception(exc)
            future.exception()  # retrieved here, so no warning when nobody waits
            raise
        else:
            await self.store.set(key=key, value=value, ttl_seconds=self._ttl())
            future.set_result(value)
            return value
        finally:
            self._loading.pop(key, None)

    def _ttl(self) -> float:
        spread = self.ttl_seconds * self.ttl_jitter
        return self.ttl_seconds + random.uniform(-spread, spread)


@functools.cache
def get_response_cache() -> ResponseCache:
    """One cache per process. The only place that chooses where the cache lives."""
    return ResponseCache(
        store=InMemoryStore(max_entries=settings.CACHE.MAX_ENTRIES),
        ttl_seconds=settings.CACHE.TTL_SECONDS,
        ttl_jitter=settings.CACHE.TTL_JITTER,
    )


ResponseCacheDep = Annotated[ResponseCache, Depends(get_response_cache)]

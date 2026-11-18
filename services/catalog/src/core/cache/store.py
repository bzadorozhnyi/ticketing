from typing import Protocol


class CacheStore(Protocol):
    """Where cached values live. The rest of the code sees only this, so the storage
    (process memory today) can be replaced without touching its callers.
    """

    async def get(self, *, key: str) -> bytes | None: ...

    async def set(self, *, key: str, value: bytes, ttl_seconds: float) -> None: ...

import time
from collections import OrderedDict
from collections.abc import Callable


class InMemoryStore:
    """A cache in this process's memory. At most `max_entries` values: adding one more
    drops the least recently used, so the memory stays bounded whatever keys arrive.
    """

    def __init__(
        self, *, max_entries: int, clock: Callable[[], float] = time.monotonic
    ):
        self.max_entries = max_entries
        self.clock = clock
        self._entries: OrderedDict[str, tuple[float, bytes]] = OrderedDict()

    async def get(self, *, key: str) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self.clock():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    async def set(self, *, key: str, value: bytes, ttl_seconds: float) -> None:
        self._entries[key] = (self.clock() + ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

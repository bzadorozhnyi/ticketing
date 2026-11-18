import asyncio

import pytest

from core.cache.memory import InMemoryStore
from core.cache.response_cache import ResponseCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def cache_with(store: InMemoryStore) -> ResponseCache:
    return ResponseCache(store=store, ttl_seconds=60, ttl_jitter=0)


class Loader:
    """Counts its calls; each call can be held until released."""

    def __init__(self, value: bytes = b"body"):
        self.value = value
        self.calls = 0
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(self) -> bytes:
        self.calls += 1
        await self.release.wait()
        return self.value


@pytest.mark.anyio
async def test_a_value_expires_after_its_ttl():
    clock = Clock()
    store = InMemoryStore(max_entries=10, clock=clock)
    await store.set(key="k", value=b"v", ttl_seconds=60)

    clock.now = 59.9
    assert await store.get(key="k") == b"v"
    clock.now = 60
    assert await store.get(key="k") is None


@pytest.mark.anyio
async def test_the_least_recently_used_value_goes_first():
    store = InMemoryStore(max_entries=2)
    await store.set(key="a", value=b"1", ttl_seconds=60)
    await store.set(key="b", value=b"2", ttl_seconds=60)
    await store.get(key="a")

    await store.set(key="c", value=b"3", ttl_seconds=60)

    assert await store.get(key="b") is None
    assert await store.get(key="a") == b"1"
    assert await store.get(key="c") == b"3"


@pytest.mark.anyio
async def test_a_cached_value_is_not_loaded_again():
    cache, load = cache_with(InMemoryStore(max_entries=10)), Loader()

    first = await cache.get_or_load(key="k", load=load)
    second = await cache.get_or_load(key="k", load=load)

    assert (first, second, load.calls) == (b"body", b"body", 1)


@pytest.mark.anyio
async def test_requests_at_once_for_a_missing_key_load_it_once():
    cache, load = cache_with(InMemoryStore(max_entries=10)), Loader()
    load.release.clear()

    waiting = [
        asyncio.create_task(cache.get_or_load(key="k", load=load)) for _ in range(50)
    ]
    await asyncio.sleep(0.01)
    load.release.set()

    assert await asyncio.gather(*waiting) == [b"body"] * 50
    assert load.calls == 1


@pytest.mark.anyio
async def test_a_failed_load_reaches_every_waiter_and_is_not_cached():
    cache = cache_with(InMemoryStore(max_entries=10))
    release = asyncio.Event()

    async def failing() -> bytes:
        await release.wait()
        raise LookupError("no such section")

    waiting = [
        asyncio.create_task(cache.get_or_load(key="k", load=failing)) for _ in range(3)
    ]
    await asyncio.sleep(0.01)
    release.set()
    results = await asyncio.gather(*waiting, return_exceptions=True)

    assert all(isinstance(r, LookupError) for r in results)
    assert await cache.get_or_load(key="k", load=Loader(b"later")) == b"later"


@pytest.mark.anyio
async def test_a_waiter_loads_itself_when_the_loading_request_is_cancelled():
    cache, load = cache_with(InMemoryStore(max_entries=10)), Loader()
    load.release.clear()
    leader = asyncio.create_task(cache.get_or_load(key="k", load=load))
    await asyncio.sleep(0.01)
    waiter = asyncio.create_task(cache.get_or_load(key="k", load=load))
    await asyncio.sleep(0.01)

    leader.cancel()
    await asyncio.sleep(0.01)
    load.release.set()

    assert await waiter == b"body"
    assert load.calls == 2


@pytest.mark.anyio
async def test_the_seat_map_is_served_from_the_cache(
    async_client, db_session, event_factory, section_factory, seat_factory
):
    event = await event_factory()
    section = await section_factory(venue=event.venue)
    await seat_factory(section=section)
    url = f"/api/v1/events/{event.id}/sections/{section.id}/seats"

    first = await async_client.get(url)
    await seat_factory(section=section)
    second = await async_client.get(url)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(second.json()["seats"]) == 1

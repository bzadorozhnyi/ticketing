# 0016: catalog caches seat maps in each process

**Status:** accepted

## Context

A seat map is read by everyone who opens an event, most of all at a sales start, and it does not change: catalog has
no API that edits venues or seats. Measured in kind with catalog pinned to 2 pods, readers calling its Service
directly: catalog served about 500 seat maps a second whatever the number of readers above 25, with both uvicorn
processes at one full core and catalog's database at 0.3 to 0.4 of a core. The limit is Python building ORM objects,
validating them and writing JSON, not the database. Caching only the rows would save the database and none of that.

## Decision

- **Cache the ready response body** of `GET /api/v1/events/{event_id}/sections/{section_id}/seats`, for 60 s with
  ±10 % jitter (`CACHE__TTL_SECONDS`, `CACHE__TTL_JITTER`), so keys loaded together do not expire together.
- **In each process's memory**, at most `CACHE__MAX_ENTRIES` (10,000) bodies, the least recently used dropped first.
- **Single flight:** while one request loads a key, the others wait for it. A cold or expired key under a spike costs
  one query per process. A failed load (404) is not cached.
- **One module owns it** (`core/cache/`): `CacheStore` is the storage interface, `InMemoryStore` its implementation,
  `ResponseCache` the single flight and TTL on top, and `get_response_cache()` the only place that picks the storage.

| Option | Why not |
|---|---|
| Valkey, shared by the pods | A network hop and deserializing on every hit, for a limit that is CPU in catalog. A new pod starts cold, but single flight makes that one query |
| Caching the rows, not the body | ORM, pydantic and JSON would still run on every request |
| More catalog pods instead | Throughput grows with cores, about 250 seat maps per core a second; the cache gives the same on the cores already there |
| No single flight | A cold key under a spike sends every concurrent request to the database |

## Consequences

- With the cache: 2,100 to 2,400 seat maps a second on the same 2 pods (4.5 to 4.8 times more), p99 139 ms against
  1,642 ms at 200 readers; catalog's database at 0.01 of a core.
- A cold cache under 1,000 requests at once: 2 reads of `seats` (one per pod) with single flight, 322 without it.
- A change of a seat map shows after up to 66 s. If catalog ever gets an API that edits venues, that API must drop the
  keys it changes, and with several pods that needs a shared cache or a broadcast.
- Each pod keeps its own copy: up to `MAX_ENTRIES` bodies of a few to some tens of KiB each.

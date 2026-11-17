# catalog

Events, venues, seat maps, prices. Read-heavy. Project-wide rules: see the root CLAUDE.md.

## Run

All commands from `services/catalog/`:

```
docker compose up -d --wait catalog-db          # from the repo root; Postgres on localhost:5433
cp .env.example .env && cp .env.test.example .env.test
cd src && set -a && . ../.env && set +a && uv run uvicorn main:app --reload
uv run pytest                                    # creates and drops testing_catalog_db
uv run pytest src/tests_e2e                      # needs rabbitmq; real commits, testing_catalog_e2e_db
uv run alembic -c src/alembic.ini upgrade head   # needs DB__* in the environment
uv run --directory src python -m scripts.seed    # demo data; no-op if venues exist
uv run alembic -c src/alembic.ini downgrade base # wipe local data before re-seeding
```

The pre-commit hook `contract` regenerates `contracts/catalog.openapi.json`.
If the API changed, the commit stops: review the diff, `git add` the file, and commit again.

## Run in Docker

From the repo root. The port is not published: clients go through the gateway.
For a direct call, `docker compose run --rm -p 8001:8000 catalog` starts a second container on `localhost:8001`.

```
docker compose up -d --wait --build                     # db -> catalog-migrate -> catalog
docker compose run --rm catalog python -m scripts.seed  # demo data
```

## Internal API

`POST /internal/v1/events/{event_id}/price` is for booking: the amount of an order comes from here, never from a client.
The gateway does not route `/internal`.

## Seat availability

catalog keeps a copy of which seats seat-hold holds or sold (ADR 0015), in `seat_availability`, one row per seat of an event.

- Background job: `catalog-projector`, the same image with `python -m scripts.project`, one pod. It consumes
  `catalog.seat-events` (bound to `seat-hold.events`, `seats.#`): apply, commit, then ack.
- A row changes only to a newer version, checked inside the `INSERT ... ON CONFLICT` (`availability/repositories/`). A
  release keeps the row as `free`, so a late older event changes nothing.
- A seat is taken when `sold`, or `held` with `expires_at > now()`: expiry has no event and is decided at read time.
- `GET /api/v1/events/{event_id}/sections/{section_id}/availability` lists taken seats; the sections list has
  `available_count`. Both may lag behind seat-hold, by about the relay's poll interval.
- A malformed event goes to `catalog.seat-events.dead`; any other failure retries with a pause doubling up to
  `RABBITMQ__MAX_RETRY_DELAY_SECONDS`, and after 20 deliveries (policy in `infra/rabbitmq/policies.sh`) to the dead queue.
- `catalog.seat_events` counts events by `outcome`: `applied`, `stale` (an older or repeated version), `retry`, `malformed`.
- Settings: `RABBITMQ__HOST`, `RABBITMQ__USER`, `RABBITMQ__PASS`, `RABBITMQ__PREFETCH_COUNT` (10),
  `RABBITMQ__HANDLER_TIMEOUT_SECONDS` (30), `RABBITMQ__RETRY_BASE_DELAY_SECONDS` (1), `RABBITMQ__MAX_RETRY_DELAY_SECONDS` (30).
  The API does not read them.
- Seats changed before the queue existed are unknown to the copy and show free, until a rebuild (TIK-101).

## Telemetry

See ADR 0006. `core/telemetry.py` sets the providers; FastAPI's built-in telemetry makes the server span and
`http.server.request.duration` (spans per dependency are off), the asyncpg instrumentation a span per statement,
and `db.client.connection.count` shows the pool by state. Histograms are exponential (native in Prometheus). `OTEL_SERVICE_NAME=catalog` in compose; the collector address
`OTEL_EXPORTER_OTLP_ENDPOINT` comes from the observability overlay. A trace starts only at the gateway: a request
without a `traceparent`, such as a health check, is not recorded. Without a collector nothing is recorded, but spans keep
the trace id from the gateway's `traceparent`, which `TraceContextMiddleware` puts into the logs.

## Layout notes

- Code lives in `src/` and is imported without a package prefix (`from core.db import ...`);
  pytest and ty use `src` as the import root.
- `core/models_registry.py` must import every entity's models package, or Alembic autogenerate won't see them.
- `/ready` fails within `DB__CONNECT_TIMEOUT_SECONDS` (default 5s) when the DB host is unreachable;
  asyncpg's own default would be 60s.

# catalog

Events, venues, seat maps, prices. Read-heavy. Project-wide rules: see the root CLAUDE.md.

## Run

All commands from `services/catalog/`:

```
docker compose up -d --wait catalog-db          # from the repo root; Postgres on localhost:5433
cp .env.example .env && cp .env.test.example .env.test
cd src && set -a && . ../.env && set +a && uv run uvicorn main:app --reload
uv run pytest                                    # creates and drops testing_catalog_db
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

## Telemetry

See ADR 0006. `core/telemetry.py` sets the providers; FastAPI's built-in telemetry makes the server span and
`http.server.request.duration` (spans per dependency are off), the asyncpg instrumentation a span per statement,
and `db.client.connection.count` shows the pool by state. Histograms are exponential (native in Prometheus). `OTEL_SERVICE_NAME=catalog` in compose; the collector address
`OTEL_EXPORTER_OTLP_ENDPOINT` comes from the observability overlay. Without it spans are not recorded but keep the
trace id from the gateway's `traceparent`, which `TraceContextMiddleware` puts into the logs.

## Layout notes

- Code lives in `src/` and is imported without a package prefix (`from core.db import ...`);
  pytest and ty use `src` as the import root.
- `core/models_registry.py` must import every entity's models package, or Alembic autogenerate won't see them.
- `/ready` fails within `DB__CONNECT_TIMEOUT_SECONDS` (default 5s) when the DB host is unreachable;
  asyncpg's own default would be 60s.

# booking

Orders, idempotency keys, saga orchestrator with persisted state. Project-wide rules: see the root CLAUDE.md.

## Run

All commands from `services/booking/`:

```
docker compose up -d --wait booking-db          # from the repo root; Postgres on localhost:5434
cp .env.example .env && cp .env.test.example .env.test
cd src && set -a && . ../.env && set +a && uv run uvicorn main:app --reload
uv run pytest                                    # creates and drops testing_booking_db
uv run alembic -c src/alembic.ini upgrade head   # needs DB__* in the environment
```

The pre-commit hook `contract` regenerates `contracts/booking.openapi.json`.
If the API changed, the commit stops: review the diff, `git add` the file, and commit again.

## Run in Docker

From the repo root. The port is not published: clients go through the gateway.
For a direct call, `docker compose run --rm -p 8002:8000 booking` starts a second container on `localhost:8002`.

```
docker compose up -d --wait --build                     # db -> booking-migrate -> booking
```

## Layout notes

- Code lives in `src/` and is imported without a package prefix (`from core.db import ...`);
  pytest and ty use `src` as the import root.
- `core/` is copied from catalog, not shared: services stay independent. A fix there may need repeating in each service.
- There are no API routes yet, so tests use the `probe_routes` fixture (`tests/helpers/probe_routes.py`).
  Replace them with real routes as they appear.
- `tests/factories/` and factory-boy come with the first entity; catalog shows the setup.

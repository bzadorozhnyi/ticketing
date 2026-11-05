# payment

Mock payment provider with fault injection; idempotent charges and refunds. Project-wide rules: see the root CLAUDE.md.

## Run

All commands from `services/payment/`:

```
docker compose up -d --wait payment-db          # from the repo root; Postgres on localhost:5435
cp .env.example .env && cp .env.test.example .env.test
cd src && set -a && . ../.env && set +a && uv run uvicorn main:app --reload
uv run pytest                                    # creates and drops testing_payment_db
uv run alembic -c src/alembic.ini upgrade head   # needs DB__* in the environment
```

The pre-commit hook `contract` regenerates `contracts/payment.openapi.json`.
If the API changed, the commit stops: review the diff, `git add` the file, and commit again.

## Run in Docker

From the repo root. The port is not published: clients go through the gateway.
For a direct call, `docker compose run --rm -p 8003:8000 payment` starts a second container on `localhost:8003`.

```
docker compose up -d --wait --build                     # db -> payment-migrate -> payment
```

## Layout notes

- Code lives in `src/` and is imported without a package prefix (`from core.db import ...`);
  pytest and ty use `src` as the import root.
- `core/` is copied from booking, not shared: services stay independent. A fix there may need repeating in each service.
- There are no API routes yet, so tests use the `probe_routes` fixture (`tests/helpers/probe_routes.py`).
  Replace them with real routes as they appear.
- `tests/factories/` and factory-boy come with the first entity; catalog shows the setup.

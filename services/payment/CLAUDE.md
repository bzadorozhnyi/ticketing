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

## Payments

Charges and refunds are use cases for now; TIK-36 connects them to RabbitMQ.

`order_id` is the idempotency key: one charge per order, enforced by a unique constraint. The same order with another
amount is refused. A refund that arrives first leaves a `cancelled` row, so a late charge takes no money.

```
uv run pytest src/tests_e2e                      # real commits, testing_payment_e2e_db
```

Faults come from the payment token (`tok_declined`, `tok_slow`, `tok_crash_before_commit`, `tok_crash_after_commit`)
or from random rates: `FAULTS__DECLINE_RATE`, `FAULTS__CRASH_BEFORE_COMMIT_RATE`, `FAULTS__CRASH_AFTER_COMMIT_RATE`,
`FAULTS__SLOW_SECONDS`. A crash is `os._exit`, a real one.

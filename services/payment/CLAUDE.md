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

## Telemetry

See ADR 0006; `core/telemetry.py` follows booking's. The consumer handles each command in a span continuing the
relay's `traceparent`, and the reply carries that span's own `traceparent`. `payment.commands` counts commands by type
and `outcome` (`handled`, `retry`, `dead_letter`, `malformed`); every outcome but `handled` calls `mark_span_failed()`, so the
span ends in error (ADR 0010); a handled one also has its `result` (`succeeded`, `declined`, ...). `OTEL_SERVICE_NAME` is `payment` for the
API and `payment-consumer` for the worker.

## Layout notes

- Code lives in `src/` and is imported without a package prefix (`from core.db import ...`);
  pytest and ty use `src` as the import root.
- `core/` is copied from booking, not shared: services stay independent. A fix there may need repeating in each service.
- There are no API routes yet, so tests use the `probe_routes` fixture (`tests/helpers/probe_routes.py`).
  Replace them with real routes as they appear.
- `tests/factories/` and factory-boy come with the first entity; catalog shows the setup.
- `MockProvider` keeps its own ledger, `provider_operations`, committed apart from our transaction like an external system.
  Count money by it (`tests/helpers/ledger.py`), not by our `charges` table. Unit tests give the provider a second session
  on the test's connection (`tests/helpers/provider.py`), so its commit is a savepoint; `tests_e2e` checks the real separate commit.
- Never hold a database connection while the provider answers: commit before the call. The mock shares our pool, so a held connection deadlocks.

## Payments

`payment-consumer` is the worker: the same image with `python -m scripts.consume`. It reads `payment.commands`, runs the
use cases and answers in `payment.replies`. Order: process, commit, send the reply, then ack; a crash at any point redelivers.

- A malformed command or a conflict goes straight to `payment.commands.dead`. A temporary failure retries with a growing
  pause, and after `RABBITMQ__DELIVERY_LIMIT` it goes there too.
- A reply booking rejects, or one delivered 20 times, goes to `payment.replies.dead`. That queue's dead letters come from a
  broker policy (`infra/rabbitmq/policies.sh`), not from arguments: `payment.replies` existed before, and a queue's arguments
  cannot change (redeclaring it fails with PRECONDITION_FAILED). Both services declare the dead queue.
- `core/rabbitmq.py` is the only place that knows the broker library: `RabbitMQClient` holds the connection, `Delivery` settles a message.
  A class per queue (`payments/queues/`) is built on the client.
- Retry with `reject(requeue=True)`, never `nack`: a `nack` requeue does not count towards the delivery limit.
- `traceparent` travels in the message headers.
- Settings: `RABBITMQ__HOST`, `RABBITMQ__PREFETCH_COUNT`, `RABBITMQ__DELIVERY_LIMIT`, `RABBITMQ__HANDLER_TIMEOUT_SECONDS`.

`order_id` is the idempotency key: one charge per order, enforced by a unique constraint. The same order with another
amount is refused. A refund that arrives first leaves a `cancelled` row, so a late charge takes no money.

```
uv run pytest src/tests_e2e                      # needs rabbitmq; real commits, testing_payment_e2e_db
```

Faults come from the payment token (`tok_declined`, `tok_slow`, `tok_crash_before_commit`, `tok_crash_after_commit`)
or from random rates: `FAULTS__DECLINE_RATE`, `FAULTS__CRASH_BEFORE_COMMIT_RATE`, `FAULTS__CRASH_AFTER_COMMIT_RATE`,
`FAULTS__SLOW_SECONDS`. A crash is `os._exit`, a real one. Delays and crashes fire on the first delivery only.

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

## Telemetry

See ADR 0006; `core/telemetry.py` follows catalog's. One trace per order, from the gateway to `confirmed`:
- `orders.traceparent` and `outbox.traceparent` are filled by a column default, `current_traceparent()`, in the same
  INSERT as the row, so no use case or repository handles them.
- `continue_trace()` gives the later work a span in that trace: a saga step (`saga {step}`, in `SagaRunner`), a publish
  (`publish {queue}`, a producer span in the relay, whose `traceparent` the message carries) and a reply (a consumer span).
- `HttpClient` makes a client span per call to seat-hold or catalog and sends its `traceparent`; it uses aiohttp,
  which has no instrumentation here.
- A trace starts only at the gateway: the polls of the saga and the relay and the health checks are not recorded.
- Metrics: `http.server.request.duration` (FastAPI), `http.client.request.duration` (upstream, status),
  `booking.saga.steps` (step, outcome `ran`/`postponed`/`failed`), `booking.outbox.publish.delay` (how long a command
  waited for the relay, from the INSERT: `outbox.created_at` is `clock_timestamp()`, since `now()` is the start of a
  saga step's transaction, before its calls to other services), `booking.order.duration` (creation to `confirmed` or
  `failed`, outcome, `failure_reason` of a failed one; the purchase SLO),
  `db.client.connection.count`. Each process has its own `OTEL_SERVICE_NAME`:
  `booking`, `booking-saga`, `booking-relay`.
- A use case that ends an order returns `FinishedOrder`, and one that changed nothing returns None, so a repeated
  reply is not counted twice; `SagaRunner` and `PaymentReplyConsumer` record the duration after the commit.
- A process killed with buffered spans loses up to about 5 s of them. Not measured yet: a relay crash after a publish
  should show as two publish and two consumer spans, the duplicate that at-least-once delivery allows.

## Read replica

See ADR 0007 and `infra/replication/README.md`. `GET /orders/{id}` reads from `booking-db-replica` (`DB_REPLICA__HOST`,
`__NAME`, `__USER`, `__PASS`); everything else uses `DB`. Without `DB_REPLICA` those reads go to the primary.
- Reads may be as old as the replica's lag: an order just created can answer 404. While the replica is down they answer 503.
- Background job of the API process, only with a replica: `core/replication.py` reads the replica's lag
  (`pg_stat_replication.replay_lag`) and the WAL its slot keeps from the primary every `REPLICATION__PROBE_INTERVAL_SECONDS`
  (5), exported as `db.replica.lag` and `db.replication_slot.retained`.
- Tests: `get_replica_session` is overridden like `get_session`; `test_get_reads_from_the_replica` gives it another
  connection, which does not see the test's uncommitted order, as a lagging replica does not.

## Layout notes

- Code lives in `src/` and is imported without a package prefix (`from core.db import ...`);
  pytest and ty use `src` as the import root.
- `core/` is copied from catalog, not shared: services stay independent. A fix there may need repeating in each service.
- The `probe_routes` fixture (`tests/helpers/probe_routes.py`) gives tests failures a real route cannot produce on demand.
- `src/tests_e2e/` has real commits and several connections: run it with `uv run pytest src/tests_e2e`. It needs RabbitMQ.
- The outbox relay is a background job: `python -m scripts.relay`, the `booking-relay` service in compose.
  Payment declares its queues, so booking only publishes: a message for a queue that does not exist stays in the outbox.
- The saga worker is a background job too: `python -m scripts.saga`, the `booking-saga` service in compose.
  Recovery lives there as well: a step that waits for payment has a timer in `next_attempt_at`.
- Calls to seat-hold and catalog go through a circuit breaker per upstream (`core/breaker.py`, `BREAKER__*`).
  While it is open, `HttpClient` raises `CircuitOpenError` without a call, and the saga postpones the order until
  the breaker may probe. It mirrors the gateway's breaker; a shared library or a service mesh comes if services multiply.
- `crash_point(name)` (`core/faults.py`) kills the process with the probability in `FAULTS__CRASH_RATE`, 0 by default.
  It is for crash experiments only; the points are `after_extend`, `after_publish`, `after_reply_commit`, `after_confirm`.
  Alembic autogenerate does not see a `CHECK` on an existing table: add it to the migration by hand.

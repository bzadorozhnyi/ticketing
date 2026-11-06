# seat-hold

Authoritative seat holds with a TTL: no seat is held by two people. Project-wide rules: see the root CLAUDE.md.

## Run

All commands from `services/seat-hold/`:

```
docker compose up -d --wait seat-hold-db         # from the repo root; Postgres on localhost:5436
cargo test                                       # creates testing_seat_hold_db, see tests/common/db.rs
cargo fmt && cargo clippy --all-targets -- -D warnings
```

After changing a query or a migration, update the offline cache (needs `sqlx-cli` 0.9.0):

```
export DATABASE_URL=postgres://postgres:password@localhost:5436/seat_hold_db
sqlx migrate run
SQLX_OFFLINE=false cargo sqlx prepare
```

The pre-commit hook `seat-hold-contract` regenerates `contracts/seat-hold.openapi.json`.

## Run in Docker

From the repo root. The port is not published: clients go through the gateway.

```
docker compose up -d --wait --build              # db -> seat-hold-migrate -> seat-hold
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `DB__HOST`, `DB__NAME`, `DB__USER`, `DB__PASS` | required | Postgres connection |
| `DB__PORT` | 5432 | Postgres port |
| `POOL__MAX_CONNECTIONS` | 10 | connection pool size |
| `POOL__ACQUIRE_TIMEOUT_SECONDS` | 5 | wait for a connection; `/ready` fails after this time |
| `HOLDS__TTL_SECONDS` | 300 | how long a hold lasts |
| `HOLDS__PAYMENT_TTL_SECONDS` | 900 | how long a hold lasts once extended for a payment |
| `HOLDS__MAX_SEATS` | 10 | seats in one request |
| `HOLDS__CLEANUP_INTERVAL_SECONDS` | 60 | how often expired rows are removed |
| `HOLDS__CLEANUP_BATCH_SIZE` | 1000 | rows removed in one statement |
| `HOLDS__IDEMPOTENCY_RETENTION_SECONDS` | 86400 | how long a used `Idempotency-Key` is remembered |
| `PORT` | 8000 | listening port |
| `LOGGING__FORMAT` | `JSON` | `JSON` or `PRETTY` |
| `LOGGING__LEVEL` | `INFO` | `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE` |

## Notes

- Migrations are SQL files in `migrations/`, embedded into the `migrate` binary.
- The trace comes from the gateway: a valid `traceparent` is continued, an invalid one starts a new trace.
- `POST /api/v1/holds` takes all seats or none, see ADR 0003. `X-User-Id` is the owner, set by the gateway from the token.
- `POST /api/v1/holds` takes an optional `Idempotency-Key`, per user. A repeat of a success returns the first response from
  `hold_requests`, even after the hold ended. The key is written first in the same transaction: a concurrent repeat waits on it,
  and a refused request rolls it back, so it can be tried again. The same key with another request gets 422.
- `POST /internal/v1/holds/{hold_id}/extend` and `/confirm` are for booking. The gateway does not route `/internal`:
  a client must not confirm a hold without paying. Both are safe to repeat, and both answer 409 when the hold is gone.
- A confirmed seat is sold: it never expires, cannot be released, and the cleanup leaves it alone.
- `DELETE /api/v1/holds/{hold_id}` always answers 204, so a retry is safe. Another user's hold stays untouched.
- Background job: `holds/cleanup_job.rs` removes expired rows. Correctness does not depend on it, and replicas need no coordination.
- `/ready` checks the database; the pool connects lazily, so the service starts during an outage.

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
| `HOLDS__MAX_SEATS` | 10 | seats in one request |
| `HOLDS__CLEANUP_INTERVAL_SECONDS` | 60 | how often expired rows are removed |
| `HOLDS__CLEANUP_BATCH_SIZE` | 1000 | rows removed in one statement |
| `PORT` | 8000 | listening port |
| `LOGGING__FORMAT` | `JSON` | `JSON` or `PRETTY` |
| `LOGGING__LEVEL` | `INFO` | `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE` |

## Notes

- Migrations are SQL files in `migrations/`, embedded into the `migrate` binary.
- The trace comes from the gateway: a valid `traceparent` is continued, an invalid one starts a new trace.
- `POST /api/v1/holds` takes all seats or none, see ADR 0003. `X-User-Id` is the owner, set by the gateway from the token.
- `DELETE /api/v1/holds/{hold_id}` always answers 204, so a retry is safe. Another user's hold stays untouched.
- Background job: `holds/cleanup_job.rs` removes expired rows. Correctness does not depend on it, and replicas need no coordination.
- `/ready` checks the database; the pool connects lazily, so the service starts during an outage.

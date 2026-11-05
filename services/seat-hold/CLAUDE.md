# seat-hold

Authoritative seat holds with a TTL: no seat is held by two people. Project-wide rules: see the root CLAUDE.md.

## Run

All commands from `services/seat-hold/`:

```
docker compose up -d --wait seat-hold-db         # from the repo root; Postgres on localhost:5436
cargo test                                       # creates testing_seat_hold_db, see tests/common/db.rs
cargo fmt && cargo clippy --all-targets -- -D warnings
```

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
| `PORT` | 8000 | listening port |
| `LOGGING__FORMAT` | `JSON` | `JSON` or `PRETTY` |
| `LOGGING__LEVEL` | `INFO` | `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE` |

## Notes

- Migrations are SQL files in `migrations/`, embedded into the `migrate` binary.
- The trace comes from the gateway: a valid `traceparent` is continued, an invalid one starts a new trace.
- `/ready` checks the database; the pool connects lazily, so the service starts during an outage.

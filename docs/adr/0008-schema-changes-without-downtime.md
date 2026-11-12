# 0008: Schema changes without downtime

**Status:** accepted

## Context

A deploy replaces a service's processes one by one, so the old and the new version run at the same time against one
database, the API and the background workers alike. A migration runs before the new version starts and must not break
the old one. Two experiments in M7 showed what goes wrong otherwise:

- A one-step move of `orders.payment_token` to its own table, with the old booking running (TIK-64): every order sent
  after it got 500 (19 of 19), the saga failed 62 times on the missing column, and orders already in flight stalled.
- An `ALTER TABLE` that takes milliseconds, waiting behind a 20 s report (TIK-63): every insert queued behind it,
  16 orders got 504 and 10 more 503 as the gateway's breaker opened.

## Decision

- **Every migration is compatible with the code of the release before it.** A change the old code cannot live with is
  split over releases: **expand** (add the new structure; the code writes both, reads the new one with a fallback),
  **migrate** (backfill in batches; the code reads only the new one, still writes both), **contract** (once no process
  of an older release runs, drop the old structure). Each release is one pull request and one deploy.
- **A migration never waits long for a lock**: `lock_timeout` 2 s and retries (TIK-63, `core/migrations.*`). No statement
  timeout, since a backfill or an index may take minutes.
- **No operation that rewrites or scans a big table under ACCESS EXCLUSIVE.** Measured on a table of 5,000,000 rows
  (510 MB) with a connection inserting all the time:

| Operation | Took | Longest insert meanwhile | Instead |
|---|---|---|---|
| `ADD COLUMN`, nullable or constant default | 2 ms | 3 to 5 ms | fine |
| `ADD COLUMN ... DEFAULT clock_timestamp()` | 4.5 s | 4.4 s | add it nullable, backfill in batches |
| `ALTER COLUMN TYPE int -> bigint` | 5.5 s | 5.5 s | a new column, expand/migrate/contract |
| `SET NOT NULL` | 1.3 s | 1.3 s | `CHECK (...) NOT VALID`, `VALIDATE CONSTRAINT` (246 ms, inserts 4 ms), then `SET NOT NULL` (1 ms) |
| `CREATE INDEX` | 0.9 s | 0.9 s | `CREATE INDEX CONCURRENTLY`: 7.0 s, inserts 88 ms |

- **A backfill is sized before it is placed.** A few rows: one idempotent statement in the migration (TIK-66 copied
  the tokens of the orders in flight only, a handful, since a final order never charges again). Thousands to
  millions: batches with a commit each, inside the migration (`autocommit_block`) or, when it takes longer than a
  deploy may wait, a separate command after it. One long statement over a big table holds a transaction open: the next
  migration queues behind it, vacuum cannot clean, and a burst of WAL puts the replica behind.
- **A removal waits for the last process that maps the old structure**, not only for the last one that uses it: an
  ORM lists every mapped column in its `SELECT` and `INSERT`, so dropping a column the code no longer reads still
  breaks the release that has it in its model. Hence one release stops mapping the column (and makes it nullable),
  and the next one drops it. A constraint can add a release: `orders.payment_token` was `NOT NULL` and compared on
  every repeat of `POST /orders`, so it took a fingerprint first. Writes the old release still makes (tokens of the
  orders it finished, orders without a fingerprint) are cleaned up by the next release's migration.
- Between those two releases `alembic check` would report the column the code no longer maps; `DROPPED_LATER` in the
  service's `migrations/env.py` names it until the drop.
- Indexes on existing tables are built `CONCURRENTLY` (CLAUDE.md has the recipes for Alembic and sqlx).
- Migrations live in the service that owns the database and ship with its code; tools are copied, not shared, as the
  rest of `core/` is.

| Option | Why not |
|---|---|
| A maintenance window: stop everything, migrate, start | Simple, and the only honest choice for some changes, but every schema change becomes an outage, and the 19 failed orders become all orders |
| One migration and a fast deploy | The window shrinks, the failures do not go away: 19 of 19 orders in 20 s failed in TIK-64 |
| A central migration service for all databases | Ties every service's schema to one deploy and one set of credentials, against the rule of a database per service |

## Consequences

- A change the old code cannot live with takes three releases, and the code writes some data twice for a while.
- Whether the two neighbouring versions work together is tested by running them together (TIK-64: old and new API and
  saga at once, then each saga alone on the other's orders), not by unit tests alone.
- The contract step depends on knowing that no older process runs. In compose that is a person checking; in
  Kubernetes (M8) it is the end of a rollout.

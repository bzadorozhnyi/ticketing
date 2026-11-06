# Learnings

## M1

- A test that passes is not proof. Mutation checks showed that a tie-breaker test passed without the tie-breaker, because UUIDv7 order matched insertion order.
- UUIDv7 is time-ordered, so tests on sort order must insert rows in reverse order.
- Alembic autogenerate can produce a migration that fails on a real database: it created a composite foreign key before the unique constraint it needs, and added a `NOT NULL` column without a default.
- If a model is missing from the registry, autogenerate proposes dropping its table. An in-process check misses this, because the app has already imported the model.
- `ForeignKey` on a column covers one column only. A composite guarantee needs a table-level `ForeignKeyConstraint`.
- A session joined to an outer test transaction rolls that transaction back on failure unless it uses `join_transaction_mode="create_savepoint"`.
- A healthy `/ready` does not mean clean failures: during a database outage the API still returned a plain-text 500, and the first request after a database restart failed on a stale pooled connection.
- Connection failures from asyncpg arrive as plain `OSError` subclasses, not SQLAlchemy errors, so `except OperationalError` would catch none of them.
- Starlette re-raises after a handler for `Exception` runs, so logging in that handler duplicates the traceback uvicorn already writes.
- `pool_pre_ping` fixes stale pooled connections only. It does not help during an outage or when a connection dies mid-query.
- `pool_pre_ping` added 0.25-0.36 ms to p50 on a request of about 3 ms, with the database on the same host. With 20 clients the difference was lost in noise. Assumption: over a real network the cost grows by the round trip. Gateway retries (M4) may make the setting unnecessary.
- `logger.contextualize` and any middleware that resets its context lose the trace id for unhandled errors: Starlette's `ServerErrorMiddleware` sits outside every user middleware, and uvicorn logs the traceback later still. A context variable set once per request survives, because uvicorn runs each request in its own task.
- loguru defaults to `diagnose=True`, which prints variable values into tracebacks. In production logs that can expose secrets, so the JSON format turns it off.
- A generated OpenAPI schema lists only what is declared. Catalog returned 404 and 503 that the schema never mentioned, and it documented a default 422 body the API does not send.
- A reverse proxy has traps a normal client does not. The HTTP client follows redirects and reads `HTTP_PROXY` by default, and its URL type resolves `..` and `%2e%2e`, so `/api/v1/events/../../internal` matches the public route and reaches another path upstream.
- Forwarding the client's `Host` keeps redirects from FastAPI on the public address: without it `Location` points at the internal service name.
- Running the service showed what its tests did not: `tracing` writes `u128` as a JSON string, so `duration_ms` from `as_millis()` was `"3"`, and the `Display` of a `reqwest` error says only "error sending request" while the cause is in its source chain.
- `cargo-chef` saved 15.5 s on a code-only Docker rebuild (dependency layer cached, the gateway itself builds in 1.7 s). The gain is small while the dependency list is short.
- A connection pool that keeps idle connections longer than the server does hits a race: the gateway sends on a connection uvicorn is closing, and a healthy upstream looks like a 502. With requests about 5 s apart this happened 2 times in 60 with a 90 s idle timeout, and 0 times with 3 s.
- The `config` crate lowercases environment names, so `port=9001` overrides `PORT=9000`, and any non-Unicode variable stops the start, even one the service does not read. Accepted: the environment of a container is ours, and the failure is loud.
- `#[serde(default)]` on a nested struct uses that struct's `Default`, not the defaults of its fields: a derived `Default` gave `max_body_bytes: 0`, which would refuse every request body.
- `jsonwebtoken` accepts a token for 60 s after `exp` by default, and it never checks the length of an HMAC secret. Both are visible only in its source.
- A rejection test can pass for the wrong reason: `Basic dXNlcjpwYXNz` was refused because it is not a JWT, not because of the scheme, so removing the scheme check changed nothing. A rejection test needs an input that is valid in every way except the one under test.
- Removing `Debug` from the settings that hold a secret turns "do not log the secret" into a compile error.
- `jsonwebtoken` with `rust_crypto` grew `Cargo.lock` from 124 to 180 packages, mostly for RSA and elliptic curves that HS256 does not use. Those unused crates carried two advisories, which `cargo-deny` found at its first run. `aws_lc_rs` gave 142 packages and no advisories, with no change to the Dockerfile.
- The choice of client library in the baseline moved the result more than the language: the same Python proxy reached 3,943 requests/s with aiohttp and 746 with httpx. Against httpx, Rust would have looked 20 times faster instead of 3.7.
- Comparing behaviour before measuring paid off: the first parity run found a different status for a dead upstream and an `Accept` header that `reqwest` adds by itself.
- With a real service behind it, the proxy language almost disappears: 599 against 596 requests/s, 2.1 against 2.4 ms at p50. The gain of Rust is cost per request at the gateway, not speed for the user.
- Writing one JSON log line per request costs the gateway 22% of throughput on 1 CPU and 31% on 2 CPUs, because the writes share one stdout.
- `docker stats` during a run showed in a minute that the gateway, not the stub or `oha`, was the limit. Check what is saturated before trusting a maximum.
- One uvicorn process does not use a second CPU: 3,779 requests/s on 2 CPUs and 3,793 on 4. Workers are separate processes with their own GIL and event loop, so they scale almost linearly (14,078 on 4 CPUs), but memory grows with them (237 MB) and they share no state.
- Threads are not automatically better than processes: from 1 to 4 CPUs Python workers gained 3.7x and the Rust gateway 2.1x, so the gap fell from 3.7x to 2.2x.
- Overwriting one file between `docker run` calls on Docker Desktop gave `oasdiff` a truncated read. Assumption: the shared file system cached the size of the previous file. A separate file per contract fixed it.
- One advisory database is not the whole picture. `jsonwebtoken` 9.3.1 passes `cargo-deny`, but OSV lists CVE-2026-25537 for it (a claim of the wrong type passes validation, fixed in 10.3.0). The RustSec database that `cargo-deny` reads does not have it.

## M2

- 200 database tests, 10 threads: rollback per test 1.3 s, a shared database with unique data 1.3 s, a clone from a template database 6.2 s, `#[sqlx::test]` 8.0 s with one migration and 9.0 s with ten.
- A shared database with random keys was as fast as rollback, but it was rejected: isolation would rest on key generation and on every query filtering by that key.
- In sqlx a `Transaction` held for the whole test turns every inner `begin` into a savepoint, so a `commit` in the code under test stays invisible to other connections. A raw `BEGIN` would not do this, because sqlx tracks the depth itself.
- One `INSERT ... ON CONFLICT DO UPDATE ... WHERE expired` both takes a free seat and takes over an expired hold, so TTL correctness needs no background job. On Postgres it ran about 32,000 times per second, and heavy conflict on 100 seats cost only 4%.
- The `time` crate was expected to bring fewer dependencies than `chrono`. Measured in the sqlx tree it brought more: 131 crates against 128.
- An index on `expires_at` cost nothing measurable on the hold statement: 31,600 operations/s without it and 31,700 with it, in the noise.
- A test that waits on a lock hangs forever when the code regresses, and CI hangs with it. Such a test needs its own timeout, so that it fails.
- The mutation script restored files only on a normal exit. A hanging mutation left a changed file behind, twice. A script that changes code must be bounded in time and restore the file on every path.
- A burst of new connections above the listen backlog fails before any application code runs. On macOS `kern.ipc.somaxconn` is 128, so 200 connections at once gave about 35 `Connection reset by peer`. The same limit matters for a real spike (M5).
- A constant duration is a sign of a timeout, not of load. Three different tests took the same 5.6 s, while the requests took 0.37 s: `#[sqlx::test]` was waiting for a pool it did not own.
- An experiment can fail by itself. Opening a connection per request used up local ports, and the "Postgres killed" run served one request and looked clean. A baseline run without any failure shows whether the load is real.
- "After the commit, before the response" is real and measurable: 2 of 52,245 holds when a replica was killed, 6 of 51,506 when Postgres was. Those clients got an error for a hold that exists, which is the case for idempotency keys in M3.
- The expectation written before the seat-hold benchmark was wrong: both services were expected to hit Postgres, but on 1 CPU the service was the limit. Writing the expectation down made the surprise visible.
- A hot seat does not scale with CPUs: Rust gave 6,776, 7,075 and 6,701 requests/s on 1, 2 and 4 CPUs, and Python stopped at about 5,200. Adding replicas will not help the exact case the system exists for: `pg_stat_activity` showed about 35 of 37 active connections waiting on `Lock:tuple`.
- A striking number needs its cause before it is used. Rust looked 15x better in the tail, and looking into the 3 s of Python showed a weak baseline: `@app.middleware("http")` (`BaseHTTPMiddleware`) halved the throughput and raised the p99 of a spike from 0.6 s to 3 s. With pure ASGI middleware the honest numbers are 2.1x and 2.8x.
- A spike at the start of a run was the obvious suspect for that tail and was wrong: the slowest 1% was spread over the run, and `gc.disable()` changed nothing. Both were checked by measuring latency over time.
- `oha -Z` picks body lines at random with repeats, not in order: 1000 requests over 1000 lines reached only 624 seats. A load tool's behaviour needs checking before its numbers can be trusted.

## M3

- An idempotency key does not need a column of its own when the business already has a natural one: one charge per order makes `order_id` the key.
- Autogenerate turned `Mapped[int]` into a 32-bit `Integer`, which is too small for money in minor units: the type has to be stated.
- A test comment claimed a refund could lose a race and do nothing, and the test ran refunds twice to compensate. The logic never needed it; removing the second round made the test stricter.
- On RabbitMQ 4.4 a requeue through `basic.nack` does not count towards `x-delivery-limit`, while `basic.reject` and a closed channel do. With a limit of 3, `nack` was delivered without end, `reject` 4 times and then dead-lettered. A test caught what the docs and my explanation did not.
- A retry without a pause turns a short outage into a full dead letter queue: the delivery limit is used up in milliseconds.
- A concurrency test can pass without racing. A test with holds about to expire gave the same outcome 30 times out of 30, because the holds had already expired. Printing the outcomes showed it; a fixed order on two connections replaced it.
- Routing by prefix exposes every new endpoint under it. Confirm on `/api/v1/holds` would have let a client take a seat without paying, so internal calls got their own path.
- Writing the idempotency key first in the same transaction solves three cases at once: a concurrent repeat waits on the unique index, a refused request takes the key back with its rollback, and a success keeps a response that survives the hold.
- `ON CONFLICT DO NOTHING` without a target covers every unique index, so one statement handles both a repeated key and a second order on the hold; a read by key afterwards tells them apart.
- A mutation that fails on a sequential repeat proves nothing about a race. Check-then-insert behaves correctly one request at a time, so only that variant shows the race test works: 14 x 500 out of 50.
- The 14 was the same in every run because it is the pool, not chance: 15 connections (5 + 10 overflow) pass the check together, one wins, 14 fail, and the other 35 wait for a connection and see the row. The size of a race is bounded by the pool.
- Rolling back a whole batch on a failed publish looks safe and is not: everything the broker already confirmed goes out again on every pass. Keeping what was confirmed turned a duplicate storm into at most one batch after a crash.
- `FOR UPDATE` alone is enough for correctness between relays, `SKIP LOCKED` only stops them waiting for each other. Removing it broke no duplicate test; it needed a test of its own.
- A test of order passed without `ORDER BY`, because a heap returns rows in insert order. Inserting ids in reverse order made the test mean something.
- An experiment script can quietly do less than it says: three kills in a row printed the same number, because the restarted relay had not published yet. One kill per run happened, not three.

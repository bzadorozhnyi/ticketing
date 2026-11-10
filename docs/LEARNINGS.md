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
- `BackgroundTasks` is a `for` loop that awaits one task after another, after the response. Measured on Starlette 1.10.0: a task that raises does not block the event loop, but the next task never runs and the client already has 200. What really blocks is a blocking call inside `async def`: `time.sleep(2)` made `/ping` wait 2.0 s. 45 hung sync tasks took the thread pool, so async routes answered and sync ones hung. SIGKILL loses the task. Fine for a thank-you email, not for a step that moves money.
- Alembic autogenerate adds columns and indexes but not a `CHECK` on an existing table. The model said there was a constraint and the database had none; only a test that writes a bad value shows it.
- A partial index on `next_attempt_at` hides a missing `ORDER BY`: the plan returns rows in order by accident. The test had to switch index scans off to mean anything.
- `rabbitmqctl list_queues` lags by a few seconds: it showed 0 replies and a moment later 2. Queue depth is not a measurement until it has settled.
- The charge is the point of no return of the saga. Before it the saga may give up; after it a timeout proves nothing, because confirm may have worked and only its answer was lost. A refund then gives the seat away for free, so the step repeats until a definite 200 or 409.
- A mutation result is only as good as the test that killed it: `no retry pause` was reported killed by a test that never touches the pause, right after a run that ended in a timeout. Repeated alone, the right test killed it. The name of the killing test is part of the result.
- Some surviving mutations are not weak tests: removing the `None` check before `RefundCommand` changes nothing, because pydantic refuses the value too and the step is postponed the same way.
- A queue is not an order of execution. With a prefetch of 10, payment handled 2 refunds and 3 charges of one order within 3 ms, and the refund won although the charge was published first. The design survived because it never relied on order, not because the order held.
- A mock without memory hides the worst bug it should show: `authorize` gets no idempotency key, and the charge is written only after the provider call. Counting our own rows proves one row, not one charge.
- An experiment can test something else than planned and still look right. Deleting the charge from the outbox did not hold it back, because the saga had already repeated it: the run ended with charge and refund, which is correct, and was not the case being tested.
- Recovery needs no process of its own when waiting is just another step with a timer: blocking, retries and pauses were already there and already tested.
- A race that never shows up in 150 tries can still be certain. Charges and refunds of 150 orders kept no money; with a provider slow by 0.2 s the same code kept 10,000 on 10 of 10 orders. A slow dependency is the normal case, so the window has to be widened on purpose before a test means anything.
- Holding a database connection while waiting for an external call is a deadlock once the call needs the pool too: 50 charges took 15 connections and waited for 15 more, and every one failed after the 30 s pool timeout. Commit before the call, and the problem is gone.
- A test can kill its own runner: the repeat of a crashed charge reused the stored crash token inside pytest, and `os._exit` ended the run without a report. Output that stops in the middle is a result to explain, not a hang to wait out.
- Alembic autogenerate again missed a changed `CHECK`: the model allowed `pending` and the database would have refused every reservation.
- An equivalent mutation can point at dead code: a `WHERE status = 'pending'` guard changed nothing, because every finisher gets the same answer for the same key. It was removed instead of being covered by a test made up for it.
- Docker's `restart: on-failure` doubles the pause before each restart of a container that keeps dying: after about 20 crashes the saga stood still for 9 minutes. My supervisor waited for `exited`, but a container waiting for its restart reports `restarting`, so it never stepped in. In production a crash loop looks like an outage, not like many small hiccups.
- Crashes in one process spread to another. Each death of the relay published its batch again, payment answered every duplicate, and the crash point in the reply consumer fired on replies that were only ignored: the saga and the relay ended up with the same number of restarts, 23 and 23.
- A count from logs can be double: every JSON line carries the message twice, in `text` and in `record.message`. Halved, the counts matched the restarts exactly; without that check the crash points would have looked twice as often hit.
- A crash point at 10 % per message is far more than 10 % per order: the relay sees every message, including the duplicates its own crashes create. Rates have to be set per event, not per order.

## M4

- The first retry rule repeated only refused connections, and in the very case it was meant for it repeated nothing: a killed replica does not refuse connections, Docker's DNS stops naming it at once. It drops the requests it was serving, and those are resets. For `GET` a repeat is safe whatever the upstream did, which is what idempotent means.
- A closed-loop load test hides retry amplification: with 20 connections, unbudgeted retries sent fewer requests to catalog in absolute numbers (5,936 against 11,765), because every pause slowed the next request. Per client request it was 3.01. Real users do not wait for each other, so the ratio is the number that matters.
- Not repeating a timeout needed no code: each attempt gets the rest of the deadline, so a timeout leaves no time for another. The mutation that repeated every error survived for that reason and was equivalent.
- A recovery measurement can measure the script. Twice the breaker seemed to recover in 0.0 s: first because the script waited 16 s, longer than the open time, before it ended the fault; then because the fault ended exactly when a probe was due and Toxiproxy let the delayed data through at once. A refused connection ended at an odd moment, 12.3 s, gave the real cost: 2.8 s.
- A summary over a run hides what a breaker does: 1.3 million fast answers made p99 2 ms, and the 50 requests that waited 10 s before it opened disappeared in it. Only a timeline showed when it opened.
- The breaker opens when the first slow calls end, so its speed is the timeout. A per-upstream timeout of 2 s opened it in 2.0 s instead of 10.0 s, which a shared state between replicas could not have done.
- A client that goes away does not hang a half-open breaker: hyper drops the handler, and the probe frees itself in `Drop`. A test proved hyper does that; I had not been sure.
- The first bulkhead experiment measured the wrong limit: with 1,500 client connections and 1,024 files, the gateway could not accept new clients at all, and a bulkhead only limits calls to upstreams. Both modes looked alike. Incoming connections need their own limit, which is load shedding in M5.
- One mechanism can hide another in an experiment: the gateway's failed sockets were connection errors, so the breaker opened and both runs drowned in its fast 503s. The breaker had to be off, and the second try at that failed silently, because a heredoc ate the backslash of `\n` and the replace matched nothing. Checking the variable inside the container caught it.
- A bulkhead trades the slow upstream's throughput for everyone else's: catalog gave 400 successes instead of 1,006, and seat-hold went from 4 answers in 25 s to 229.
- A dead dependency was the mild case for the saga, a hung one the bad one. A refused connection fails in milliseconds and only wastes calls; a hanging one held the single runner for 3 s per order, and refunds that never touch seat-hold waited behind it for a whole minute.
- Where resilience code lives is an architecture choice, not a detail: in every service (duplicated, no extra hop), in a gateway for internal traffic too (one copy, but a critical hop and two roles in one process) or in a service mesh (configuration, no code, but Kubernetes and its complexity). We chose every service for now: two languages, two copies.
- Experiments use up their data: demo seats ran out after earlier runs had confirmed 620 holds, and the script died half way and left the saga stopped. A run has to check its preconditions and restore what it changed in a `finally`.

## M5

- A baseline has to explain every error before it counts. The first run sold only 220 of 300 seats; the 503 turned out to be the gateway's own bulkhead, and the missing seats my client, which gave up on an order after one 503 instead of repeating it with the same key.
- Under a spike the entry spends its effort on refusals: at 5,000 users the gateway used 2 cores, and 17,700 of 24,000 hold requests were refusals that clients repeated at once. A refusal without `Retry-After` invites the next request; that is what a rate limit and load shedding have to fix, not more capacity.
- The slowest part under the spike was not where the contention is: seat-hold stayed near 40 % CPU while booking's single Python process sat at a full core.
- A fail-open limit switched itself off under exactly the load it exists for. Alone, a bot with 500 connections got 99 of 432,000 requests through; together with 1,000 buying users it got 80,175 through in 12 s. Under the combined load the Valkey calls timed out, each timeout let the request pass, and the breaker around Valkey then turned the limit off for whole periods.
- A timeout measures the caller as much as the callee. In a run with 751 timeouts Valkey served only 626 calls, at 20 µs each: the 50 ms were spent waiting for a CPU on a gateway that was busy refusing the bot. A longer timeout or a pool of connections would have fixed nothing.
- Refusing in the replica's memory first keeps a flood off the network: the shared store sees only what the replica allowed, and the limit survives the store. The price is that without Valkey a user gets the limit once per replica.
- `axum::serve` has no header timeout: hyper's default of 30 s works only with a timer, and axum never sets one. A client that sent `GET /he` was still connected after 60 s. Reading the docs said 30 s; only trying it showed the truth.
- An idle connection costs about 32 KB, and memory freed by Rust stays with the allocator: after 5,000 connections closed the gateway kept 145 MB, and the next rounds reused it (191 MB after three). Not a leak, but the memory limit has to fit that plateau, not the idle size.
- A connection limit cannot tell a flood from a customer; what it buys is that the gateway keeps its promises. Without it, accepting stopped for a second about 20 times in 20 s, and whether the gateway's own sockets to seat-hold were open before the clients took the files decided between no failed answers and every answer failed. One run would have told either story.
- The load tool ran out before the gateway: the fourth spike run in a row had 1,578 requests waiting 30 s and 12 failed connects, with nothing in the gateway's log. The host held 3,410 sockets in TIME_WAIT out of 16,384 ephemeral ports. Waiting until they cleared, three more runs had none.
- `Retry-After` moved work rather than removing it: with a cap of 5 tries both clients sent the same 23,850 hold requests, but the waiting ones reached seat-hold and got a definite 409 instead of another refusal. The gateway refused half as much, and seat-hold did more real work.
- `ON CONFLICT DO UPDATE ... WHERE` locks the conflicting row even when the `WHERE` is false. Every refusal for a hot seat queued on that one lock while holding a pooled connection, so one popular seat made holds of any other seat 35 times slower: the pool, not the seat, was the shared resource.
- A read before the write fixes it only if it knows who holds the seat. Refusing every seat that looks taken would turn a client's repeat with the same `Idempotency-Key` into a 409 for a seat it holds.
- The first hot-seat measurement measured the load tool: 220 Python clients at 7,400 requests/s gave 30 ms to everyone, which is just 220 / 7,400. Rust for the flood and a fixed-rate probe told the seat's cost apart from the client's queue.
- `cargo test` stops at the first test binary that fails, so a mutation looks killed only by the tests that run first. Mutation runs need `--no-fail-fast` to see which tests catch it.

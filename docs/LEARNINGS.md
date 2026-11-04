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
- `jsonwebtoken` with `rust_crypto` grew `Cargo.lock` from 124 to 180 packages, mostly for RSA and elliptic curves that HS256 does not use.

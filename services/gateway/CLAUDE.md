# gateway

The only public entry point: routing, trace start, limits. Project-wide rules: see the root CLAUDE.md.

## Layout

```
src/
  app.rs           builds the router: which layers, in which order
  upstream.rs      one upstream with its timeout, retry budget, breaker and bulkhead
  server/          accepts connections: their limit, the header timeout, graceful shutdown
  handlers/        health and the proxy handler
  limits/          decided before any work starts; protects the gateway itself
    concurrency.rs ConcurrencyLimit: one limit of work at once (bulkheads, requests in flight, connections)
    shedding.rs    the limit of requests in flight
    rate_limit/    per user: replica buckets (local.rs), then Valkey (shared.rs), joined in limiter.rs
  resilience/      calls to upstreams: breaker.rs, retry.rs, rotation.rs (the client, replaced every pool max age)
  telemetry/       OpenTelemetry: init.rs (providers, the tracer), metrics.rs (every instrument), method.rs,
                   request.rs (server span and request metrics), upstream.rs (a span per attempt, traceparent)
  auth.rs  errors.rs  settings.rs  logging.rs  shutdown.rs
```

## Run

All commands from `services/gateway/`:

```
docker compose run --rm -d -p 8001:8000 catalog  # from the repo root; publishes catalog for a local gateway
export AUTH__JWT_SECRET=local-dev-secret-with-at-least-32-bytes UPSTREAMS__CATALOG_URL=http://localhost:8001 UPSTREAMS__SEAT_HOLD_URL=http://localhost:8004 UPSTREAMS__BOOKING_URL=http://localhost:8002
cargo run --bin gateway
cargo run --bin issue-token -- user-1            # prints a token valid for 15 minutes
docker compose up -d --wait valkey                # from the repo root; the rate limit tests need it on localhost:6379
cargo test
cargo fmt && cargo clippy --all-targets -- -D warnings
```

## Run in Docker

From the repo root; the gateway is on `localhost:8000`:

```
docker compose up -d --wait --build
TOKEN=$(docker compose exec -T gateway issue-token user-1)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/events
```

## Settings

Read from the environment with the `config` crate; a wrong value stops the start.

| Variable | Default | Meaning |
|---|---|---|
| `AUTH__JWT_SECRET` | required | HS256 secret, at least 32 bytes |
| `UPSTREAMS__CATALOG_URL` | required | catalog base URL |
| `UPSTREAMS__SEAT_HOLD_URL` | required | seat-hold base URL |
| `UPSTREAMS__BOOKING_URL` | required | booking base URL |
| `UPSTREAMS__CATALOG_TIMEOUT_SECONDS` | `CLIENT__REQUEST_TIMEOUT_SECONDS` | all attempts to catalog together; the same for `SEAT_HOLD` and `BOOKING` |
| `UPSTREAMS__CATALOG_MAX_CONCURRENT` | `BULKHEAD__MAX_CONCURRENT` | requests in flight to catalog; the same for `SEAT_HOLD` and `BOOKING` |
| `CLIENT__CONNECT_TIMEOUT_SECONDS` | 2 | TCP connect to an upstream |
| `CLIENT__REQUEST_TIMEOUT_SECONDS` | 10 | default timeout of an upstream; above catalog's DB timeout (5s), or the gateway answers 504 before catalog answers 503 |
| `CLIENT__POOL_IDLE_TIMEOUT_SECONDS` | 3 | must stay below uvicorn's keep-alive (5s) |
| `CLIENT__POOL_MAX_AGE_SECONDS` | 10 | a new upstream client, with new connections, this often; 0 keeps one client |
| `RETRY__MAX_ATTEMPTS` | 3 | attempts per `GET` or `HEAD`, the first included; 1 turns retries off |
| `RETRY__BASE_DELAY_SECONDS` | 0.05 | the pause before attempt n is random, up to this times 2^(n-2) |
| `RETRY__BUDGET_RATIO` | 0.2 | retries allowed per request, per upstream: the most retries add to an upstream's load |
| `RETRY__BUDGET_CAPACITY` | 10 | retries allowed in a row while traffic is low; every gateway replica has its own |
| `BREAKER__WINDOW` | 20 | the last calls to an upstream the breaker looks at |
| `BREAKER__MIN_CALLS` | 10 | fewer calls never open it; above `WINDOW` turns the breaker off |
| `BREAKER__FAILURE_PERCENT` | 50 | share of failed calls in the window that opens it |
| `BREAKER__OPEN_SECONDS` | 5 | how long it stays open before one probe goes through |
| `BULKHEAD__MAX_CONCURRENT` | 200 | requests in flight to one upstream, retries included; more get 503 at once |
| `RATE_LIMIT__VALKEY_URL` | none | Valkey for the limit shared by replicas, e.g. `redis://valkey:6379`; without it there is no rate limit |
| `RATE_LIMIT__PER_SECOND` | 10 | requests per second per user, on average |
| `RATE_LIMIT__BURST` | 20 | requests a user may send at once after a quiet moment |
| `RATE_LIMIT__TIMEOUT_SECONDS` | 0.05 | connecting to Valkey and each answer; a slower one skips the shared limit |
| `LIMITS__MAX_BODY_BYTES` | 1048576 | larger request bodies get 413 |
| `LIMITS__MAX_IN_FLIGHT` | 1000 | requests in work in this replica; more get 503 `overloaded` at once. Bodies are buffered, so this times `MAX_BODY_BYTES` bounds their memory |
| `LIMITS__MAX_CONNECTIONS` | 10000 | open client connections; more are closed at once. Keep it below the open-file limit minus the sum of the bulkheads (sockets to upstreams). An idle connection costs about 32 KB |
| `LIMITS__HEADER_TIMEOUT_SECONDS` | 30 | time to send the headers of a request; also the longest a keep-alive connection may stay idle |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | none | OTLP/HTTP collector, e.g. `http://otel-collector:4318`; without it nothing is exported |
| `OTEL_SERVICE_NAME` | `unknown_service` | `gateway` in compose; the `job` label in Prometheus |
| `OTEL_METRIC_EXPORT_INTERVAL` | 60000 | milliseconds between metric exports; 10000 in the observability overlay |
| `PORT` | 8000 | listening port |
| `LOGGING__FORMAT` | `JSON` | `JSON` or `PRETTY` |
| `LOGGING__LEVEL` | `INFO` | `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE` |

## Behaviour notes

- Errors produced by the gateway use `{"code", "message", "details"}`. Upstream responses pass through unchanged.
- 503: connection to the upstream failed. 504: no complete response in time. 502: any other upstream failure.
- Each upstream has a circuit breaker. A failure is no answer, a timeout, or a 502, 503 or 504 from the upstream;
  404 and 500 are not. While it is open, the gateway answers 503 `circuit_open` with `Retry-After` at once and does
  not call the upstream. Every gateway replica has its own breaker.
- Each upstream has a bulkhead: at most `MAX_CONCURRENT` requests in flight, from the first attempt until the response
  headers arrive. One more gets 503 `bulkhead_full` with `Retry-After: 1` at once.
- `GET` and `HEAD` are repeated when no answer came (refused, reset or closed connection) or the upstream answered 503,
  within the request timeout and the retry budget. The client sees only the last answer. A timeout, a 500,
  a 502 or 504 from the upstream and every other method are never repeated: `POST /orders` relies on the client's `Idempotency-Key`.
- Background job: every `CLIENT__POOL_MAX_AGE_SECONDS` the upstream client is replaced by a new one with an empty pool
  (`resilience/rotation.rs`). A Kubernetes Service balances connections, and kept-alive ones would stay on the pods
  that existed when they opened: a pod added by the autoscaler got none (TIK-73). New connections reach the pods as
  they are now. A call on the old client finishes on it. Balancing is still per connection, refreshed this often.
- Load shedding, checked before anything else: at most `MAX_IN_FLIGHT` requests in work, from arrival until the
  response headers are sent; one more gets 503 `overloaded` with `Retry-After: 1`. `/health` and `/ready` are never
  shed: a busy replica is alive, and failing its probes would move its load to the others.
- The gateway serves connections itself (`server/serve.rs`), not with `axum::serve`, which has no timeout and no limit.
  A connection over `MAX_CONNECTIONS` is closed at once, so a flood cannot use up the open files: without the limit,
  accepting stopped for a second at a time and requests already accepted failed for lack of a socket to the upstream.
  The limit cannot tell a flood from a customer: during a flood a new client is refused too. HTTP/1.1 only.
- Each user has a token bucket (`RATE_LIMIT__*`), checked after the token and before retries, breakers and bulkheads.
  It has two tiers: a bucket in the replica's memory first, then one in Valkey shared by every replica.
  A user over the limit gets 429 `rate_limited` with `Retry-After`; a refusal by the first tier costs no network call.
  When Valkey is down or slower than the timeout, only the replica's own limit applies: a user gets at most
  the limit times the number of replicas. A breaker with the `BREAKER__*` settings stops asking a failing Valkey.
  The replica keeps at most 100,000 users; a full map drops idle users, and a new user beyond it is left to Valkey.
- `/health` and `/ready` do not check upstreams: the gateway stays in rotation and answers 503 for the broken route.
- Every `/api` route needs a token (HS256, `exp` and `sub` required). A public route is an explicit exception:
  it is added in `app.rs` outside the `protected` router. `/health` and `/ready` are public.
- Upstreams trust `X-User-Id`: it comes from the token `sub`; the client's value and the `Authorization` header never pass.
- A trace always starts here. Client `traceparent` and `tracestate` are dropped.
- Telemetry (ADR 0006): a server span per request, named by method and route template, and a client span per attempt
  to an upstream (named by method, the upstream in `server.address`), so a retry shows as a second attempt; the upstream
  gets that attempt's `traceparent`. `trace_request` is a route layer, so the route is known; the fallback has its own
  copy of it. Without a collector spans are not recorded (`AlwaysOff`) but still get ids, sent as not sampled.
  The ADR 0002 benchmark runs without the overlay, so its numbers are the gateway without telemetry; with the overlay
  every request is traced and exported, about 9 % of the maximum (`docs/CLAIMS.md`).
  Metrics: `http.server.request.duration` (method, route, status, `error.type` = the gateway's error code, so every
  refusal shows with its reason), `http.client.request.duration` (upstream, status or `timeout`/`connection`),
  `gateway.{bulkhead,in_flight,connections}.*` places in use, `gateway.breaker.transitions`,
  `gateway.rate_limit.shared_skipped`, `gateway.connections.refused`. Instruments live in `telemetry/metrics.rs`;
  a module records with one line where it already handles the event. Histograms are exponential (native in Prometheus),
  so no bucket list has to match the other services. Spans get ids even without a collector,
  because the logs and the upstreams need the trace id.
- The resource has `service.instance.id` (process id and start time), which the Rust SDK does not set itself: without
  it the collector exposes no `target_info` for the gateway, and `ProcessNotExporting` would page.
- In JSON logs the id is at `span.trace_id`; in the Python services it is at `record.extra.trace_id`.
- Paths with `..` or `%2e%2e` get 404: the URL library would resolve them to a route that was never matched.

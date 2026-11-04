# gateway

The only public entry point: routing, trace start, limits. Project-wide rules: see the root CLAUDE.md.

## Run

All commands from `services/gateway/`:

```
export AUTH__JWT_SECRET=local-dev-secret-with-at-least-32-bytes UPSTREAMS__CATALOG_URL=http://localhost:8001
cargo run --bin gateway
cargo run --bin issue-token -- user-1            # prints a token valid for 15 minutes
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
| `CLIENT__CONNECT_TIMEOUT_SECONDS` | 2 | TCP connect to an upstream |
| `CLIENT__REQUEST_TIMEOUT_SECONDS` | 10 | whole upstream call; must exceed catalog's DB timeout (5s), or the gateway answers 504 before catalog answers 503 |
| `CLIENT__POOL_IDLE_TIMEOUT_SECONDS` | 3 | must stay below uvicorn's keep-alive (5s) |
| `LIMITS__MAX_BODY_BYTES` | 1048576 | larger request bodies get 413 |
| `PORT` | 8000 | listening port |
| `LOGGING__FORMAT` | `JSON` | `JSON` or `PRETTY` |
| `LOGGING__LEVEL` | `INFO` | `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE` |

## Behaviour notes

- Errors produced by the gateway use `{"code", "message", "details"}`. Upstream responses pass through unchanged.
- 503: connection to the upstream failed. 504: no complete response in time. 502: any other upstream failure.
- `/health` and `/ready` do not check upstreams: the gateway stays in rotation and answers 503 for the broken route.
- Every `/api` route needs a token (HS256, `exp` and `sub` required). A public route is an explicit exception:
  it is added in `app.rs` outside the `protected` router. `/health` and `/ready` are public.
- Upstreams trust `X-User-Id`: it comes from the token `sub`; the client's value and the `Authorization` header never pass.
- A trace always starts here. Client `traceparent` and `tracestate` are dropped.
- In JSON logs the id is at `span.trace_id`; in the Python services it is at `record.extra.trace_id`.
- Paths with `..` or `%2e%2e` get 404: the URL library would resolve them to a route that was never matched.

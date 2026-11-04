# M1: liveness vs readiness when the database goes away

**Question.** When catalog loses its database, does it report the failure correctly, and does it recover without a restart?

**Answer.** Yes for both. The experiment also found two defects in how API requests fail; see Findings.

## Reproduce

```
docker compose up -d --wait
docker compose run --rm catalog python -m scripts.seed
experiments/m1-liveness-readiness/run.sh
```

Raw output of the first run: [results/latest.txt](results/latest.txt).

## Environment

macOS on Apple Silicon (10 cores), Docker 29.7.2, Postgres 18.6, Python 3.14, one uvicorn process, default SQLAlchemy pool.
The experiment ran 3 times; every run gave the same status codes and the same recovery time.

## Results

| Scenario | `/health` | `/ready` | `GET /api/v1/events` |
|---|---|---|---|
| Database up | 200 | 200 | 200 |
| Database stopped | 200 | 503 | 500 |
| Database started again | 200 | 200 | 200 |

- Recovery: `/ready` returned 200 again 0.31 s after `docker compose start` was issued. That time includes the command itself.
- The catalog container was never restarted: its `StartedAt` stayed the same through all three runs.
- First 10 requests after a quick database restart with a warm pool: `500 200 200 200 200 200 200 200 200 200`.

## Findings

1. **Readiness works as designed.** A restart would not fix a missing database, so `/health` stays 200 while `/ready` reports 503.
2. **Outage responses break the API contract.** `GET /api/v1/events` returns a plain-text `500 Internal Server Error`, not the `{"code", "message", "details"}` format. A dependency outage should be a 503, so that clients and the gateway can treat it as retryable.
3. **One request fails after every database restart.** The pool still holds dead connections, so the first request fails with `InterfaceError`. SQLAlchemy then discards the pool, and later requests succeed.

## Limits

- One process and sequential requests. With concurrent traffic, more than one request may fail at the moment of the restart; this was not measured.
- The database was stopped cleanly. A network partition, where packets are dropped without a connection error, was not tested.

# Reliability claims

Every claim points to a test or a recorded measurement. A claim without evidence does not belong here.

| Claim | Evidence | Known limits |
|---|---|---|
| Catalog reports a database outage through `/ready` (503) while `/health` stays 200 | `src/tests/test_health.py`; measured manually in TIK-5 with the database stopped: `/health` 200, `/ready` 503, same result in 3 runs | Clean stop only; a network partition is not tested |
| Catalog recovers from a database outage without a restart | Measured manually in TIK-7: `/ready` back to 200 0.31 s after `docker compose start`, the catalog container kept the same `StartedAt`; no automated test | Requests in flight during the restart still fail; this was not measured |
| The first request after a database restart succeeds | Measured manually in TIK-7, first 10 requests after a restart: all 200 with `pool_pre_ping`, `503 200 200 ...` without it; no automated test | Costs about 0.3 ms per request; a connection that dies during a query still fails with 503 |
| A database outage returns 503, and every error uses the `{"code", "message", "details"}` format | `src/tests/test_error_responses.py`; measured manually in TIK-7: `GET /api/v1/events` returned 503 with the database stopped | Any `OSError` inside a request with a session is reported as a database outage |
| `/ready` answers within `DB__CONNECT_TIMEOUT_SECONDS` when the database host is unreachable | Measured manually in TIK-1: 503 after 5.0 s, and after 1.0 s with the setting at 1; no automated test | Covers connecting only, not a query that hangs on an open connection |
| Every log line of a catalog request carries its `trace_id`, including an unhandled error and its 500 | `src/tests/test_trace_context.py`; measured manually in TIK-8: 30 parallel requests each logged their own id; the uvicorn traceback and 500 line kept the id | The id is not checked for authenticity, only for format; relies on uvicorn running each request in its own task |
| A price cannot reference a section of another venue, and a priced event cannot move to another venue | `src/tests/test_section_price_constraints.py` (enforced by composite foreign keys) | Enforced in catalog's database only |
| The catalog contract matches the code, and it documents every status a route returns on purpose | `src/tests/test_contract.py` | 500 and 405 are not documented; nothing rejects a breaking change automatically |
| Models and migrations do not drift apart | `src/tests/test_migrations.py` (`alembic check` in a fresh process) | Alembic does not compare everything, for example check constraints |

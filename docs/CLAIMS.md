# Reliability claims

Every claim points to something reproducible. A claim without evidence does not belong here.

| Claim | Evidence | Known limits |
|---|---|---|
| Catalog reports a database outage through `/ready` (503) while `/health` stays 200 | [m1-liveness-readiness](../experiments/m1-liveness-readiness/REPORT.md), `src/tests/test_health.py` | Clean stop only; a network partition is not tested |
| Catalog recovers from a database outage without a restart | [m1-liveness-readiness](../experiments/m1-liveness-readiness/REPORT.md) | The first API request after a database restart fails with 503 (TIK-7) |
| A database outage returns 503, and every error uses the `{"code", "message", "details"}` format | [m1-liveness-readiness](../experiments/m1-liveness-readiness/REPORT.md), `src/tests/test_error_responses.py` | The OpenAPI schema still documents FastAPI's default 422 body; any `OSError` inside a request with a session is reported as a database outage |
| `/ready` answers within `DB__CONNECT_TIMEOUT_SECONDS` when the database host is unreachable | Measured manually in TIK-1: 503 after 5.0 s, and after 1.0 s with the setting at 1; no automated test | Covers connecting only, not a query that hangs on an open connection |
| A price cannot reference a section of another venue, and a priced event cannot move to another venue | `src/tests/test_section_price_constraints.py` (enforced by composite foreign keys) | Enforced in catalog's database only |
| Models and migrations do not drift apart | `src/tests/test_migrations.py` (`alembic check` in a fresh process) | Alembic does not compare everything, for example check constraints |

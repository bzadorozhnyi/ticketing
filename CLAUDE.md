# CLAUDE.md

Ticketing: a learning/portfolio project for practicing distributed-systems patterns with deliberate microservices.
Domain: selling tickets to events with limited seats and sales-start traffic spikes.
Features are secondary; failure experiments and evidence are the point.

## Working mode

- Reply to the user in Ukrainian (never Russian). Code, comments, README, ADRs, commit messages: English.
- Comments in code only when necessary: a docstring for a complex function is fine, a comment on a simple `if` is not.
  Answers to the user's questions and explanations of decisions go in the chat, not into code comments.
- Work milestone by milestone. For each milestone: propose a plan, wait for approval, then implement in small steps.
- Incremental approach: each increment is small and leaves the code working (tools and tests pass)
  before the next one starts. Build the minimal working version first, then extend it.
- Before implementing a concept (saga, idempotency, circuit breaker, ...), explain it in plain words with trade-offs.
- **Ask before adding any tool, library, or infrastructure component** not already listed in this file.
  Propose it with the reason, alternatives, and the verified version; wait for approval.
- Dependencies are chosen while building the first service; that service becomes the template for the next ones.
- Automation over tokens: if a tool can check or fix something (pre-commit, ruff, ty, cargo fmt/clippy, Makefile targets),
  run the tool instead of reviewing by hand; analyze only what the tool reports.
- Skills only for routine that existing tools cannot cover; prefer a Makefile target or script when it is enough.
- This file holds project-wide rules. Service-specific rules go in `services/<name>/CLAUDE.md`.
- Never guess library APIs or versions. Verify via the installed version, official docs, or by running code.
  If unsure, say so. Mark every assumption as "Assumption: ...".
- When a design is challenged, answer with evidence (test, experiment, measurement).
  If something is not guaranteed, say so and describe the failure scenario.
- Experiments are throwaway: no scripts or reports in the repository.
  Record the conclusion and the key numbers in `docs/CLAIMS.md` and `docs/LEARNINGS.md`.
  Exception: the benchmark behind a Rust ADR stays in `infra/benchmark/`, so its numbers can be reproduced.
- After each milestone: a short summary of the concepts used and 3-5 interview-style questions (no answers).

## Design principles

- Idempotency first: every process is a sequence of steps, and each step is safe to re-run.
  If a process crashes at any step, running it again must not create duplicates or repeat a side effect (such as a charge).
- A check before acting is not enough on its own: two concurrent runs can both pass it.
  Enforce the guarantee where it is atomic: a unique constraint in the database, or an idempotency key on the external call.
- For every multi-step process, state what happens if it crashes after each step. Explain this to the user in detail
  before implementing it (first in M3, the saga).

## Constraints

- Python (FastAPI) by default. Rust (Axum) ONLY for `gateway` and `seat-hold`;
  each Rust component needs an ADR justifying Rust plus an honest benchmark against a Python baseline.
- Each service owns its database. No shared tables, no cross-service DB access.
- A periodic/background job (hold expiry, stuck-saga recovery) lives inside the owning service and must be flagged to the user.
- No frontend. Simple auth at the gateway only.

## Inter-service communication

See ADR 0005. booking and payment talk through RabbitMQ, with a transactional outbox on the sending side.
Calls to seat-hold are synchronous HTTP. Delivery is at least once, so every consumer is idempotent.

## Observability

See ADR 0006. OpenTelemetry traces and metrics over OTLP to an OpenTelemetry Collector, then Prometheus (scrapes the
metrics from the collector), Tempo (traces) and Grafana, all in the `infra/observability/` compose overlay. Telemetry is off without
`OTEL_EXPORTER_OTLP_ENDPOINT`. Instrumentation stays at the edges, never in use cases; labels come from small fixed sets.

## Services

| Service | Lang | Role |
|---|---|---|
| gateway | Rust | routing, JWT auth, rate limiting, timeouts, retries (idempotent only), circuit breaker, load shedding, trace propagation, metrics |
| catalog | Python | events, venues, seat maps, prices; read-heavy |
| seat-hold | Rust | authoritative seat holds with TTL; no seat held by two people |
| booking | Python | orders, idempotency keys, saga orchestrator with persisted state |
| payment | Python | mock payment provider with fault injection; idempotent charges/refunds |

## Repository layout

```
services/<name>/        one directory per service, self-contained (own dependencies, Dockerfile, tests, CLAUDE.md)
contracts/              generated OpenAPI per service (code-first, see ADR 0001)
infra/                  repository scripts, compose helpers, benchmarks, observability, k8s
docs/BOARD.md           task board (TIK-<n> tickets)
docs/adr/               NNNN-title.md
docs/CLAIMS.md          reliability claim -> evidence (test or recorded measurement) -> known limits
docs/LEARNINGS.md       short running log of what was learned / what surprised us
```

## Python services

Decided tools: loguru (logging), ruff (lint + format), ty (type checking), pre-commit (runs ruff and ty),
OpenTelemetry (`opentelemetry-sdk`, the OTLP HTTP exporter, FastAPI's built-in telemetry, the asyncpg instrumentation;
see `core/telemetry.py` in catalog and ADR 0006). Everything else: ask first.

### Code organization (onion, grouped by entity)

```
src/
  main.py                 app factory: middlewares, exception handlers, routers
  routes.py               mounts every entity's versioned router under /api
  core/                   cross-cutting only: settings, db, logging, exceptions, health, middlewares
  <entity>/               one package per domain entity (e.g. events/, venues/)
    endpoints/v1/
      routes/             one endpoint per file + router.py
      schemas/request/    API input models
      schemas/response/   API output models
    use_cases/            one use case per file, class with async execute()
    services/             domain logic reused by several use cases
    repositories/         data access only, no business decisions
    models/               ORM models
    dto/                  internal data passed between layers
  migrations/
  tests/
```

- Dependencies point inward: endpoints -> use_cases -> services/repositories -> models. Never the other way.
- Requests, responses and DTOs are pydantic models, not dataclasses.
- Endpoints are thin: parse input, call `use_case.execute(...)`, map the result to a response schema.
- The use case owns the transaction: it calls `commit()`/`rollback()`. Services and repositories never commit.
- Each injectable class (use case, service, repository) is defined in the same file as its FastAPI wiring:
  a `get_<name>()` factory and `<Name>Dep = Annotated[<Name>, Depends(...)]`. No separate dependency module.
- Constructors and methods use keyword-only arguments (`*`).
- `__init__.py` files stay empty: no re-exports, no `__all__`. Import from the module that defines the name.
  Side-effect imports (`# noqa: F401`) only where registration needs them: `core/models_registry.py`
  (every models module) and `<entity>/endpoints/v1/routes/__init__.py` (every endpoint module).
- Errors: raise `AppException` subclasses (`NotFoundException`, `ConflictException`, ...) carrying an `ErrorCode` StrEnum;
  one exception handler maps codes to HTTP statuses and returns `{"code", "message", "details"}`.
- Settings: pydantic-settings, nested groups with `__` delimiter (`DB__HOST`), read only from `os.environ`, no env_file.
- Health: `/health` (process is up) and `/ready` (dependencies reachable, 503 otherwise).
- Complexity limits: cyclomatic complexity <= 10, nesting depth <= 4.

### Tests

- Real Postgres, dedicated test database per suite (e.g. `testing_<service>_db`), configured via `.env.test`
  loaded into `os.environ` before any app import.
- Session fixture: drop + create the test DB, run migrations to head, drop it at the end.
- Per test: one connection-level transaction rolled back at the end; the app's session dependency is overridden to use it.
- API tests go through `httpx.AsyncClient` + `ASGITransport` against the real app.
- Test data via factory fixtures in `tests/factories/`, auto-registered with `pytest_plugins`.
- A separate e2e suite in `src/tests_e2e/` uses real commits, no rollback isolation. It loads `.env.test`, overrides `DB__NAME`
  with its own database, and runs in its own process (`pytest src/tests_e2e`), because settings are read once.
  Tests that need several concurrent connections (concurrency, saga, recovery) belong there,
  because a rolled-back transaction is invisible to other connections.

## Rust services

- Edition 2024, toolchain pinned in `rust-toolchain.toml`.
- Lints: `unsafe_code = "forbid"`; clippy `all` + `pedantic` plus `unwrap_used`, `indexing_slicing`, `wildcard_enum_match_arm`,
  `fallible_impl_from`, `excessive_nesting` as warnings, `excessive-nesting-threshold = 4`; `cargo fmt --check` and clippy must pass.
  `unwrap` and indexing are allowed in tests through `clippy.toml`.
- Panics: `panic = "unwind"`, so a bug kills one request and not the process (measured, see `docs/CLAIMS.md`).
  A panic hook writes the panic to the JSON log.
- Limits are explicit: request body size, upstream connect and total timeouts. SIGTERM triggers graceful shutdown.
- Docker: `cargo-chef` caches the dependency build; runtime image `debian:trixie-slim`, non-root user (no distroless).
- `lib.rs` exposes modules, `main.rs` only wires dependencies and starts the server.
- `mod.rs` only declares modules: no logic and no re-exports, the same as `__init__.py`. Import from the module that defines the name.
- A service with entities follows the Python layout: `core/` for cross-cutting code, `routes.rs` mounts the versioned routers,
  and each entity has `endpoints/v1/routes/` (one endpoint per file + `router.rs`), `endpoints/v1/schemas/{request,response}/`,
  `use_cases/`, `services/`, `repositories/`, `dto/`. The gateway has no entities: its modules are grouped by what they protect (see `services/gateway/CLAUDE.md`).
- The layers mean the same as in Python. Endpoints are thin. A use case is a struct with `execute()`, one per file;
  it holds the business rules and owns the transaction. `services` appear only for logic reused by several use cases.
  A repository is a unit struct with associated functions: SQL only, no business decisions.
- `AppState` holds dependencies only (database, settings). Each use case implements `FromRef<AppState<D>>` in its own file,
  and an endpoint asks for it with `State<...>`, like `Depends` in Python.
- Repositories take the executor/connection as a parameter, so a service can compose several of them in one transaction.
- Errors: `thiserror` enums per area.
- Settings: `config` crate without default features, nested groups with `__` delimiter (`UPSTREAMS__CATALOG_URL`),
  upper case names, read only from the environment. A group with defaults uses `#[serde(default)]`
  and a hand-written `impl Default`; required values live in a group without defaults.
- pre-commit runs `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings`.
- SQL goes through `sqlx::query!` and `query_as!`, checked at compile time against the `.sqlx` cache, which is committed.
  `SQLX_OFFLINE` is on in `.cargo/config.toml`, so a build needs no database. CI runs `cargo sqlx prepare --check` (`sqlx-cli` 0.9.0).
- API contract: `utoipa` generates `contracts/<service>.openapi.json`, a test fails on drift.
- Code reaches the database through the `DbPool` trait: the pool in production, `TestPool` in tests. Test code stays in `tests/`.
- Tests with a database mirror the Python services: a dedicated `testing_<service>_db`, recreated and migrated once per test binary.
  Each test gets a `TestPool`: one connection inside one transaction that is never committed. A `commit` in the code under test
  is a savepoint there. The server URL comes from `DATABASE_URL` in `.cargo/config.toml`.
- Tests that need several connections (concurrency, recovery) use `#[sqlx::test]`: a fresh database per test with real commits,
  about 6 times slower.
- Test data: factories built with `bon` (added with the first entity).
- Gateway tests run against a real axum server started inside the test (real sockets, no mock library),
  and the path through real services is checked in the running compose stack.

## CI

GitHub Actions on pull requests into `main` and on pushes to `main` (`.github/workflows/ci.yml`), because pre-commit can be skipped:
- Python: ruff, ty, pytest against the compose database.
- Rust: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`, `cargo test --release`.
- `cargo-deny` for advisories and licenses (`services/gateway/deny.toml`).
- `infra/check-contracts.sh` runs `oasdiff`: a breaking API change fails the build. A break made on purpose is listed in
  `infra/contracts/<service>.accepted` ("METHOD PATH" and the full text oasdiff prints, one line per error), so it is visible
  in the pull request. The file holds the latest such change; the next one replaces its lines.
- `infra/check-observability.sh`: the collector and Prometheus configs, `promtool test rules` for the SLO alerts,
  valid dashboard JSON.
- All Docker images build, with the layers cached between runs.

`infra/changed_areas.py` decides what runs in a pull request: only documentation is skipped, a service runs its own checks,
and an unknown path runs everything. A push to `main` always runs everything. The only required check is `ci-passed`:
a skipped job counts as success, a failed one fails it, so a new service needs no change in the repository settings.

## Git

- Tasks live in `docs/BOARD.md` (sections: In progress, To do, Done). New tickets take the number from its `Next ticket` counter.
- Every ticket (or part of a ticket) is a separate task on its own branch, created from `main`: `TIK-<n>-<short-slug>`.
- When the task is done, the branch goes to a pull request into `main`, titled like the commit message. CI must pass.
  It lands as one commit with **Squash and merge** on GitHub. The branch is then deleted, locally with `git branch -D`
  (git does not treat a squashed branch as merged, so `-d` refuses).
- Moving a ticket on the board is part of the same pull request as the work, never a separate one.
- Commit message: `TIK-<n> <Summary>`, where `<n>` is the ticket number from `docs/BOARD.md`.
  Summary: English, past tense, starts with a verb, at most 50 characters (not counting the ticket prefix).
  Example: `TIK-12 Added username field in patch profile endpoint`.
- `TIK-0` marks small technical fixes that don't deserve their own ticket (not tracked on the board).
  They follow the same branch + pull request flow (`TIK-0-<short-slug>`); the only exception is the repository's root commit.

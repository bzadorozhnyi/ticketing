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
- After each milestone: a short summary of the concepts used and 3-5 interview-style questions (no answers).

## Constraints

- Python (FastAPI) by default. Rust (Axum) ONLY for `gateway` and `seat-hold`;
  each Rust component needs an ADR justifying Rust plus an honest benchmark against a Python baseline.
- Each service owns its database. No shared tables, no cross-service DB access.
- A periodic/background job (hold expiry, stuck-saga recovery) lives inside the owning service and must be flagged to the user.
- No frontend. Simple auth at the gateway only.

## Services

| Service | Lang | Role |
|---|---|---|
| gateway | Rust | routing, auth, rate limiting, timeouts, retries (idempotent only), circuit breaker, load shedding, trace propagation, metrics |
| catalog | Python | events, venues, seat maps, prices; read-heavy |
| seat-hold | Rust | authoritative seat holds with TTL; no seat held by two people |
| booking | Python | orders, idempotency keys, saga orchestrator with persisted state |
| payment | Python | mock payment provider with fault injection; idempotent charges/refunds |

## Repository layout

```
services/<name>/        one directory per service, self-contained (own dependencies, Dockerfile, tests, CLAUDE.md)
contracts/              API contracts
infra/                  compose helpers, observability, k8s
experiments/<mN-name>/  scripts + REPORT.md; results/ only for raw data that does not fit in the report
docs/BOARD.md           task board (TIK-<n> tickets)
docs/adr/               NNNN-title.md
docs/CLAIMS.md          reliability claim -> evidence (reproducible test) -> known limits
docs/LEARNINGS.md       short running log of what was learned / what surprised us
```

## Python services

Decided tools: loguru (logging), ruff (lint + format), ty (type checking), pre-commit (runs ruff and ty). Everything else: ask first.

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
- A separate e2e suite (own `.env.test.e2e` and DB) uses real commits, no rollback isolation.
  Tests that need several concurrent connections (concurrency, saga, recovery) belong there,
  because a rolled-back transaction is invisible to other connections.

## Rust services

- Edition 2024, toolchain pinned in `rust-toolchain.toml`.
- Lints: clippy `all` + `pedantic` as warnings, `excessive-nesting-threshold = 4`; `cargo fmt --check` and clippy must pass.
- `lib.rs` exposes modules, `main.rs` only wires dependencies and starts the server.
- Modules by layer: `settings`, `errors`, `dto`, `enums`, `repositories`, `services`, handlers.
- Repositories take the executor/connection as a parameter, so a service can compose several of them in one transaction.
- Errors: `thiserror` enums per area.
- pre-commit runs `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings`.
- Tests with a database: candidate is `#[sqlx::test]` (fresh database per test, migrations applied automatically,
  real commits allowed). Decide when building seat-hold; it is several times slower than rollback-per-test.
  Gateway tests run against a mock upstream; the tool is chosen when we get there.

## Git

- Tasks live in `docs/BOARD.md` (sections: In progress, To do, Done). New tickets take the number from its `Next ticket` counter.
- Every ticket (or part of a ticket) is a separate task on its own branch, created from `main`: `TIK-<n>-<short-slug>`.
- When the task is done, it lands in `main` as one squash commit (`git merge --squash`); the branch is then deleted
  with `git branch -D` (git does not treat a squashed branch as merged, so `-d` refuses).
- Moving a ticket on the board is part of the same commit as the work, never a separate commit.
- Commit message: `TIK-<n> <Summary>`, where `<n>` is the ticket number from `docs/BOARD.md`.
  Summary: English, past tense, starts with a verb, at most 50 characters (not counting the ticket prefix).
  Example: `TIK-12 Added username field in patch profile endpoint`.
- `TIK-0` marks small technical fixes that don't deserve their own ticket (not tracked on the board).
  They follow the same branch + squash flow (`TIK-0-<short-slug>`); the only exception is the repository's root commit.

## Open decisions

- Inter-service communication: synchronous HTTP vs messaging (decide when we reach it).
- Contracts: code-first vs design-first.
- Gateway auth: static API keys vs JWT.
- CI platform.

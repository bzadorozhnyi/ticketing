# 0001: Code-first API contracts

**Status:** accepted

## Context

Services need a shared description of their APIs. The two usual approaches are:

- **Design-first:** write OpenAPI by hand, then make the code follow it.
- **Code-first:** write the code, then generate OpenAPI from it.

The project has one developer and no external clients. FastAPI already generates a schema, but it had two problems:
an API change could pass unnoticed inside a code diff, and the schema listed only what was declared.
Catalog returned 404 and 503 that the schema did not mention, and it documented a 422 body that the API never sends.

## Decision

Use code-first, and keep the generated schema in the repository as `contracts/<service>.openapi.json`.

- A pre-commit hook regenerates the file. If it changes, the commit stops, so the change is seen in its own diff.
- A test fails if the file differs from the code. The hook can be skipped with `--no-verify`; the test still catches it.
- Every status a route returns on purpose is declared on the route. 500 and 405 are not declared.

## Consequences

- There is one source of truth, and no hand-written YAML to keep in sync.
- The contract describes what exists, not what is planned, so a consumer cannot be built against an API that has no code yet.
- CI rejects a breaking change with `oasdiff` (`infra/check-contracts.sh`). On `main` this happens after the commit, because the project has no pull requests.
- Rust services need their own way to generate a schema, chosen when we build them.

# mcp

The ticketing API as MCP tools for an LLM agent, behind the gateway at `/mcp` (ADR 0017). Project-wide rules: see the
root CLAUDE.md. No database.

## Run

All commands from `services/mcp/`:

```
uv run pytest                                    # fake catalog, seat-hold and booking on a local port
uv run ruff check && uv run ruff format --check && uv run ty check
```

In Claude Code: start the stack (`docker compose up -d --wait --build` from the repo root), then
`export TICKETING_TOKEN=$(docker compose exec -T gateway issue-token user-1)` and open Claude Code in the repository:
`.mcp.json` connects it to `http://localhost:8000/mcp` with that token (15 minutes).

## Evals

`src/evals/` runs an agent against the running stack and grades what it did (TIK-111). From `services/mcp/src`:

```
uv run python -m evals.run                       # 9 scenarios, 3 trials each, on the kind cluster (about 6 minutes)
uv run python -m evals.run --scenario hold_two --trials 1 --stack compose
```

- The agent is Claude Code in print mode (`ClaudeCodeRunner`), signed in with the developer's own subscription: no API
  key. It runs in an empty directory with only this MCP server and its tools (`--strict-mcp-config`, `--tools ""`,
  `--setting-sources local`). `CLAUDE_BIN` points at Claude Code when it is not on the PATH; the copy inside the VS
  Code extension is found by itself. `AgentRunner` is the interface: an API-key runner for CI fits the same graders.
- Each run creates its own venue, event and user, and the graders read seat-hold's and booking's databases, not the
  agent's words, wherever they can; a few checks look for phrases in the answer.
- In print mode Claude Code does not answer elicitation, so no scenario can pay: `no_payment_without_user` checks that
  nothing is paid without the user. A full payment is covered by `tests/test_tools.py`.
- Every run uses the subscription (about 1 dollar of equivalent usage for the full set on Opus). Results, with each
  answer and tool call, go to a temporary file; the summary table goes to `docs/CLAIMS.md` when it changes.
- Eval data stays in the stack: events named `<scenario name> <tag>` in cities of their own.

## Layout

```
src/
  main.py          create_app(): the Streamable HTTP app, stateless, JSON answers
  server.py        the MCPServer, its instructions, the clients made once per process (lifespan), user_of()
  tools/           events.py (read), holds.py, orders.py (payment confirmed by the user through elicitation)
  clients/         catalog, seat-hold, booking; core/http.py is the only place that knows aiohttp
  core/            settings, logging, health
```

## Notes

- The user is `X-User-Id`, set by the gateway from the token; the server never sees a token. Without it a tool
  answers "Not signed in".
- A tool's docstring is what the model reads: when to use it, what it returns, what to do on an error.
- A failure the model can act on is a `ToolError` with the next step in its text; it reaches the model as a tool
  result with `isError`.
- `create_order` asks for the payment token through elicitation (multi round-trip on protocol 2026-07-28) and sends
  `Idempotency-Key: order:{hold_id}`: a repeat returns the same order.
- The payment question is a multi round-trip request: the answer comes back in a retry with the sealed
  `requestState`, which may reach another pod. The SDK's default key is made by each process at start, so the
  keys are a setting; a state is bound to the `X-User-Id` that received it (`tests/test_request_state.py`).
- Tests go over HTTP through the ASGI app (`httpx2`, which the SDK brings) with the header the gateway would send.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `UPSTREAMS__CATALOG_URL`, `SEAT_HOLD_URL`, `BOOKING_URL` | `http://<service>:8000` | the services the tools call |
| `UPSTREAMS__CONNECT_TIMEOUT_SECONDS`, `UPSTREAMS__TIMEOUT_SECONDS` | 2, 10 | per call |
| `SERVER__REQUEST_STATE_KEYS` | required | JSON list of keys (32+ bytes) for the state a tool hands out with an "input required" answer; every pod must share them (Secret `mcp` in the cluster). The first seals, every one opens: rotate by putting a new key first, then drop the old one after 10 minutes |
| `SERVER__PAYMENT_TOKEN_HINT` | `tok_ok` | the test card named in the payment question |
| `LOGGING__FORMAT`, `LOGGING__LEVEL` | `JSON`, `INFO` | |

# 0017: the ticketing API as MCP tools, behind the gateway

**Status:** accepted

## Context

An LLM agent (Claude Code, or any host that speaks the Model Context Protocol) should be able to find events and buy
tickets for a user. MCP lets one server describe its tools once for every host. The specification of 2026-07-28 is
stateless: no handshake and no session, every request carries its protocol version, and a server that needs the
user's input mid-call answers `input_required` (multi round-trip requests) instead of calling the client back.

The MCP security rules forbid "token passthrough": a server must not accept a token that was not issued for it, nor
forward one to an API. Our own rule keeps authentication at the gateway only.

## Decision

- **A Python service `mcp`** (`mcp` SDK 2.3.0), with seven tools over the public API: `search_events`,
  `get_event_sections`, `get_section_seats`, `hold_seats`, `release_hold`, `create_order`, `get_order`.
- **Behind the gateway at `/mcp`**, as another upstream (`UPSTREAMS__MCP_URL`; without it the route does not exist).
  The gateway checks the token, rate-limits the user and passes `X-User-Id` without the token. The server never sees
  a token, so nothing is passed through, and it calls catalog, seat-hold and booking inside the cluster with
  `X-User-Id`, as booking calls seat-hold.
- **Stateless, JSON answers** (`stateless_http=True`, `json_response=True`): any pod serves any request, and the
  gateway, which buffers bodies, passes one JSON document.
- **The user, not the model, confirms a payment.** `create_order` asks for the payment token through elicitation; a
  decline pays nothing. Its idempotency key is `order:{hold_id}`, so a repeated call returns the same order and pays
  once; booking also refuses a second active order for one hold.
- **Errors say what to do next** ("these seats are taken: …; choose other seats"), and tools carry MCP annotations
  (`read_only_hint`, `destructive_hint`, `idempotent_hint`) for hosts that ask before a destructive call.
- **No DNS rebinding protection** in the SDK: it guards a server on a user's localhost that a web page could reach.
  This one has no published port, every request carries a bearer token a page cannot add, and the gateway passes the
  client's `Host`, which the check refused (421).
- **Claude Code connects through `.mcp.json`** with a token from `issue-token` in `TICKETING_TOKEN`.

| Option | Why not |
|---|---|
| The server checks tokens itself | A second place with authentication; forwarding the token would be passthrough, minting new ones needs the signing key in the server |
| OAuth 2.1 between Claude Code and the server | The specification's full answer, but an authorization server is a project of its own |
| The tools inside an existing service | Every service would learn MCP, or one would call the others' APIs anyway |
| Rust (`rmcp`) | A thin adapter over HTTP gains nothing from Rust, and the project keeps Rust to gateway and seat-hold |

## Consequences

- A token from `issue-token` lives 15 minutes, so a developer issues a new one now and then.
- The seat map shows no availability: an agent learns that a seat is taken from `hold_seats`.
- In `claude -p`, elicitation is served only in interactive sessions (Claude Code docs), so an automated eval cannot
  confirm a payment the same way; TIK-111 deals with it.
- The server has no telemetry yet.

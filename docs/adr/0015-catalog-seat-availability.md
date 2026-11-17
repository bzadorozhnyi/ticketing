# 0015: catalog keeps a copy of seat availability

**Status:** withdrawn, see "Measured at a sales start"; the code was removed in TIK-104.

## Context

seat-hold publishes every change of a seat with the seat's version (ADR 0014). catalog should tell a buyer which
seats are free without asking seat-hold, whose database decides every hold. The copy may lag. It may not get stuck on
a wrong state, because duplicates and reordering are normal with this broker.

## Decision

- **A projector process** (`python -m scripts.project`, `catalog-projector`, one pod, a background job) consumes
  `catalog.seat-events`. That is a quorum queue bound to `seat-hold.events` with `seats.#`. It applies the event,
  commits and then acknowledges, so a crash delivers the event again.
- **One row per seat of an event** in `seat_availability` (`event_id`, `seat_id`, `status`, `expires_at`, `version`).
  A row changes only to a newer version: `INSERT ... ON CONFLICT DO UPDATE ... WHERE version < excluded.version`. The
  check is part of the statement, so duplicates, late events and two projectors at once cannot write an older state.
- **A released seat keeps its row** as `free` with the release's version. Without it, a late older "held" would find
  no row and take the seat in the copy.
- **Expiry is decided at read time.** A seat is taken when it is `sold`, or `held` with `expires_at > now()`. seat-hold
  sends no event when a hold runs out, so the copy shows it free on time even while the projector is down.
- **No foreign keys.** seat-hold accepts any seat id, and an event must never fail on a seat catalog lacks. Reads join
  `seats`, so an unknown seat is simply never shown.
- **Reads:** `GET /api/v1/events/{event_id}/sections/{section_id}/availability` lists the taken seats of a section.
  The seat map stays a separate, static response that can be cached. `available_count` is added to the list of
  sections.
- **Failures:** a malformed event goes to `catalog.seat-events.dead` at once. Any other failure retries after a pause
  that doubles up to 30 s. After 20 deliveries (policy `catalog-seat-events` in `infra/rabbitmq/policies.sh`) the event
  goes to the dead letter queue too.
- **aio-pika 10.2.2**, the client and version of booking and payment.

| Option | Why not |
|---|---|
| Deleting the row on release | A late older event would take the seat in the copy |
| An event when a hold expires | seat-hold would need a job that finds expired holds in time; the time is already in the row |
| Checking the version in Python before writing | Two runs at once could both pass the check |
| Asking seat-hold on each read | Its database would carry the reads of a spike |

## Consequences

- The copy lags by the relay's poll interval. Measured in the kind cluster, 59 holds one by one, from the 201 to the
  row in catalog: p50 276 ms, p95 493 ms, max 505 ms with the relay polling every 0.5 s. With 0.05 s it was p50 38 ms,
  p95 57 ms, max 60 ms. So nearly all of it is the poll.
- A seat that changed before catalog's queue existed is unknown to the copy and shows free. In the cluster, 48,103
  seats sold in earlier experiments are such seats, and a hold on one got 409. A rebuild from a snapshot closes this
  gap (TIK-101).
- A crash of the projector costs each unacknowledged event a delivery: on RabbitMQ 4.4.0 a message returned by a
  closed connection counts like a rejected one (measured). An outage of catalog's database longer than about 8 minutes
  of pauses sends the events in flight, at most `RABBITMQ__PREFETCH_COUNT` (10) of them, to the dead letter queue. The
  copy of those seats then stays wrong until each seat changes again or the copy is rebuilt.
- A seat shown free that is taken costs one 409 from seat-hold. That is no worse than without the copy.

## Measured at a sales start (TIK-103)

The rule was agreed before measuring: keep the copy only if buyers choosing by it get at least half fewer refusals,
the events cost seat-hold at most 20 % more, and `availability` has no 5xx under the spike. In the kind cluster, a
client pod calling the gateway's Service, 5,000 users within 1 s on 300 seats, 3 runs each:

- Refusals 409 per seat sold: blind 67.4 (median), by the copy 46.6, that is 31 % fewer; blind without events 71.5.
- Cost of the events: seat-hold's database CPU 6.39 s against 6.11 s (+4.6 %), median hold 850 against 724 ms (+17 %,
  within the spread of the runs). catalog's database +1.7 s and the projector 2.5 to 3 s of CPU a run.
- `availability`: 13,000 to 20,000 answers 503 `bulkhead_full` a run; catalog's pods failed their liveness probe.
- With the relay polling every 0.05 s and 1,000 users, 2 runs each: 12.0 and 12.3 refusals blind, 9.1 and 8.3 by the
  copy (28 to 31 % fewer), still about 2,500 answers 503 a run.
- Buyers reading before every try lose time: the first 300 arrivals bought 19 to 28 seats by the copy, about 200 blind.

The copy lags by about the length of the spike itself, so it is stale exactly when people choose. Outside a spike,
catalog could ask seat-hold directly. Decision: revert. Choosing seats on the server ("best available") would remove
the refusals without any copy.

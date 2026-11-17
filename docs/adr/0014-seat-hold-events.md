# 0014: seat-hold publishes every change of a seat

**Status:** accepted

## Context

A buyer chooses seats without knowing which are free. At a sales start this cost up to 80 refusals (409) per seat
sold (TIK-85). Catalog should show which seats are free, but only seat-hold knows. If catalog asked seat-hold on every
read, the reads of a spike would land on seat-hold's database, the part that decides who gets a seat. Instead, catalog
keeps its own copy and updates it from events (a read model, CQRS). The copy may lag. That is safe, because seat-hold
still decides every hold: a seat shown free that is already taken costs one 409, as it does today.

ADR 0005 kept seat-hold free of a broker client. This decision changes that.

## Decision

- **A transactional outbox in seat-hold.** The transaction that changes seats also writes the event to `outbox`.
  The `relay` binary publishes it. There is one pod, `seat-hold-relay`, a background job. A row is deleted only once
  the broker confirms it. Without the outbox, a crash between the commit and the publish would lose the event, and the
  copy would stay wrong until it is rebuilt.
- **A topic exchange `seat-hold.events`** (durable, declared by the relay). Each event type has its own routing key:
  `seats.held`, `seats.extended`, `seats.sold`, `seats.released`. A reader binds its own queue, so later readers need no
  change in seat-hold. Messages are persistent.
- **An event names the seats and their new state:** `{type, event_id, hold_id, seats: [{seat_id, version, expires_at?}]}`.
  `expires_at` comes with a hold and an extension. A hold that runs out has no event, because a reader treats a seat
  past its `expires_at` as free. A sold seat never expires.
- **Each seat has a version.** It comes from one sequence (`seat_versions`) and is taken while the seat's row is locked,
  so a later change of the same seat always has a larger version. A reader keeps a seat's latest version and drops
  anything older. That covers duplicates (delivery is at least once) and the reordering a failed batch can cause. A
  release deletes the row, so its version is taken in `DELETE ... RETURNING`, and a reader has to remember the
  released seat with that version.
- **An event only for a real change.** A repeated confirm or extension, and a replayed hold, write nothing.
  `RETURNING old.*` (Postgres 18) shows whether the row changed.
- **The relay sends a batch of up to 100 messages, then waits for the confirms.** A batch costs one round trip to the
  broker, not one per message. It deletes the confirmed rows up to the first failure. It looks again every 0.5 s when
  the outbox is empty, and connects again after a broker error.
- **lapin 4.12.3** without default features, with `tokio`. No TLS is needed inside the cluster.

| Option | Why not |
|---|---|
| Catalog asks seat-hold on each read | The read load of a spike lands on the database that decides holds |
| Publishing from the request after the commit | A crash between the two loses the event for good |
| Change data capture (Debezium) from seat-hold's WAL | A new component (Kafka Connect or a server) for one table; the outbox fits what booking already does |
| `LISTEN/NOTIFY` | Lost while no one listens; no confirms |
| Kafka | Its log would make a rebuild simple, but it is a second broker; the rebuild uses a snapshot instead (TIK-101) |

## What happens on a crash

| Crash | Effect | Protection |
|---|---|---|
| Before the commit of a change | Neither the change nor its event exists | One transaction |
| After the commit, before the publish | The event waits in the outbox | The relay publishes it |
| After the publish, before the delete | The event is published again | The reader drops a version it already has |
| The broker is down | Holds go on; events wait in the outbox | The relay connects again and publishes them (measured in compose) |
| One message of a batch fails | The next ones are published again later, after it | Versions, not the order of delivery |

## Consequences

- A queue receives only events published after it was bound. A message that no queue receives is dropped. A new or lost
  copy is rebuilt from a snapshot of seat-hold plus the events after it (TIK-101).
- Every change of seats costs a second `UPDATE` (a hold) or a sequence value, and one outbox `INSERT`, inside the hold's
  transaction. This cost at a sales start has not been measured yet (TIK-101).
- The copy lags by the relay's poll interval plus the broker and the reader. This lag has not been measured yet either.
- The relay is a single pod, and a second one would only skip locked rows. Events of seat-hold's request pods are not
  published while it is down. They wait in the outbox.
- The events carry no `traceparent`, so a reader's work does not join the trace of the request that changed the seat.

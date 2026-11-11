# 0007: A read replica for booking

**Status:** accepted

## Context

Clients poll `GET /orders/{id}` until the order is confirmed or failed, every 0.3 s in our load scripts: booking reads
far more than it writes, and every read goes to the one database that also takes every write. M7 is about what a copy
of the database costs in correctness: a copy is always a little behind.

## Decision

- **Physical streaming replication**, asynchronous: a standby of `booking-db` (`booking-db-replica`), same image,
  read-only. `pg_basebackup -R` makes it on the first start; it then streams WAL from the primary through a
  **replication slot**, so a replica that was down catches up instead of being copied again.
- **Only `GET /orders/{id}` reads from it** (`DB_REPLICA__*`). Every write, the saga and the relay stay on the primary.
  Without `DB_REPLICA` the reads go to the primary, so tests and local runs need no replica.
- **Lag is measured on the primary**: `pg_stat_replication.replay_lag` for the slot, the time from writing WAL to the
  replica applying it. The age of the replica's last applied transaction was tried first and overstated the lag after
  a quiet period (10.7 s for a 3 s delay). A background job of the booking API reads it every 5 s with the WAL the slot
  keeps; both are gauges, and two alerts watch them: a replica more than 5 s behind for a minute, and a slot that keeps
  WAL for a replica that is not streaming.
- **Read-your-writes with a consistency token** (TIK-62). `POST /orders` returns `X-Consistency-Token`, the primary's
  WAL position after its commit. A `GET /orders/{id}` that sends it reads from the replica if the replica has applied
  that position, and from the primary otherwise, without waiting: under load a 200 ms wait made reads slower and sent
  more of them to the primary (TIK-65). Without a token a read goes to the
  replica and, only when the replica does not have the order, to the primary. A replica that cannot be reached sends
  every read to the primary. `db.replica.reads` counts where reads went and why (`core/consistency.py`).
- The setup on the primary (the `replicator` role, the slot, `pg_hba.conf`) is a one-shot compose service whose steps
  are safe to repeat, since the image runs its init scripts only on an empty volume.

| Option | Why not |
|---|---|
| Synchronous replication (`remote_apply`) | No stale reads, but every commit waits for the replica, and the primary stops confirming commits while the replica is down |
| Logical replication | Copies chosen tables and allows writes on the copy; a read-only copy of everything needs neither |
| A replica of catalog | catalog has no write API, so its lag would show only after hand-written SQL |
| Lag from a heartbeat table | Exact even with no traffic, but needs a writer job; `replay_lag` gives the same without one |
| Only "not on the replica, read the primary" | Fixes the 404 of a new order as well, and under load it was as fast and sent as many reads to the primary. But it sees only whether a row exists: a change to an existing order (a cancel) would still read stale. It is kept for clients without a token |
| Waiting on the replica for the token | Each waiting read checked the replica every 20 ms. Under load the API was the bottleneck, the checks came late, the wait ran out and the read went to the primary anyway: GET p50 319 to 544 ms against 244 to 345 without waiting, and more reads on the primary |
| Synchronous replication instead of a token | Every order would wait for the replica; the token makes only the reads that need it wait |

## Consequences

- A client sees its own order at once, with a token or without. Changes made later by the saga (`confirmed`) are not
  covered by the token: a read from the replica shows them up to the lag late.
- While the replica is behind, reads of new orders go to the primary: the replica saves the primary least when it is
  slowest. Under load (300 purchases, 150 at once) the primary stayed at 10 to 25 % CPU and the booking API at a full
  core: there, the replica was not what limited reads.
- A read with a token costs one more query on the replica, the position check.
- While the replica is down every read goes to the primary, and the primary keeps WAL for the replica: 19 MB in the
  first minute of a test. A replica that never comes back must have its slot dropped. A replica that does not answer
  at all (not refused, but silent) costs each read the connect timeout; there is no breaker in front of it.
- No failover: the replica is never promoted. Promoting one safely (fencing the old primary, moving the clients) is a
  topic of its own.

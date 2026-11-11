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
- The setup on the primary (the `replicator` role, the slot, `pg_hba.conf`) is a one-shot compose service whose steps
  are safe to repeat, since the image runs its init scripts only on an empty volume.

| Option | Why not |
|---|---|
| Synchronous replication (`remote_apply`) | No stale reads, but every commit waits for the replica, and the primary stops confirming commits while the replica is down |
| Logical replication | Copies chosen tables and allows writes on the copy; a read-only copy of everything needs neither |
| A replica of catalog | catalog has no write API, so its lag would show only after hand-written SQL |
| Lag from a heartbeat table | Exact even with no traffic, but needs a writer job; `replay_lag` gives the same without one |

## Consequences

- Reads may be as old as the lag: an order just created can answer 404, a confirmed one `pending`. TIK-62 measures it
  and adds the protection.
- While the replica is down, `GET /orders/{id}` answers 503 (until TIK-62), and the primary keeps WAL for it:
  19 MB in the first minute of a test. A replica that never comes back must have its slot dropped.
- No failover: the replica is never promoted. Promoting one safely (fencing the old primary, moving the clients) is a
  topic of its own.

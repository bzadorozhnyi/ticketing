# 0003: Postgres as seat-hold storage

**Status:** accepted

## Context

Seat-hold has one job: no seat is held by two people, and a hold ends by itself after a TTL.
At the start of sales many clients ask for the same seats at the same moment.
A check followed by a write is not enough: two concurrent requests can both see a free seat.
The guarantee has to come from an atomic operation in the storage.

A client may hold several seats at once, and it gets all of them or none.

| | Postgres | Redis | Process memory |
|---|---|---|---|
| Guarantee | Primary key | `SET NX EX` | A lock in the process |
| Several seats, all or nothing | Transaction | Lua script | One lock over all seats |
| TTL | `expires_at` column, cleaned up separately | Built in | By hand |
| Crash | Committed holds survive | Depends on persistence settings | All holds are lost |
| Failover | Depends on replication | Asynchronous replication can lose a write, so a seat can be held twice | Not applicable |
| Replicas of the service | Yes | Yes | One instance only |
| New infrastructure | None | One more component | None |

## Decision

Use Postgres, with its own database. Correctness is the requirement, and speed is measured against what the system needs.

The guarantee is the primary key `(event_id, seat_id)`. One statement takes a free seat or an expired hold:

```sql
INSERT INTO holds (event_id, seat_id, owner_id, expires_at)
VALUES ($1, $2, $3, now() + $4)
ON CONFLICT (event_id, seat_id) DO UPDATE
SET owner_id = EXCLUDED.owner_id, expires_at = EXCLUDED.expires_at
WHERE holds.expires_at <= now();
```

No row in the result means the seat is taken.

- An expired hold counts as free inside this statement, so correctness does not depend on a background job. The job only removes old rows.
- Time comes from the database clock, so replicas of the service cannot disagree about it.
- Several seats go into one transaction. The statement itself succeeds partially: it returns only the seats it got. The code compares the count with the request and rolls back, otherwise a client keeps some of its seats.
- Seats are locked in a fixed order (sorted by id). Measured: two requests for the same seats in opposite order hit a deadlock, and Postgres aborted one after about 1 s; in the same order the second one waited and got nothing.

## What happens on a crash

- **Before the commit:** nothing is held. The client gets an error and tries again.
- **After the commit, before the response:** the seats are held, but the client does not know. It can retry and get a conflict with itself, or the hold ends with the TTL.
  An idempotency key for this case is part of M3.

## Measurements

`pgbench`, 16 clients, 3 runs of 15 s, one table with the primary key, no CPU limit, `synchronous_commit = on`:

| Scenario | Operations/s | Average latency |
|---|---|---|
| One seat, different seats | 32,000 | 0.50 ms |
| One seat out of 100, nearly always taken | 30,800 | 0.52 ms |
| Four seats in one transaction | 20,700 | 0.77 ms |

In the second scenario the table held exactly 100 rows after about 1.4 million attempts.
For comparison, the gateway carries about 14,700 requests/s per CPU, so this storage is not the first limit.

## Consequences

- One more Postgres, no new kind of infrastructure.
- Rows stay after they expire until a background job removes them. The job lives inside seat-hold.
- Every hold is a write to disk, so write capacity limits the service. Sharding by event is the way out if it ever becomes a problem.
- This decision is revisited if the benchmark in TIK-24 shows that Postgres is the limit under the spike scenario.

## Known limits

- The numbers come from Docker Desktop on an M1 Max. Assumption: `fsync` in that VM is cheaper than on a real disk, so production numbers would be lower. Only the order of magnitude matters here.
- `pgbench` skips the service, the pool and the network between them. TIK-24 measures the whole path.
- The primary key protects against a double hold in one database. With replication, a failover can lose committed holds unless replication is synchronous; this is not covered yet.

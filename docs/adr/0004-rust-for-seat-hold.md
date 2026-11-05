# 0004: Rust for seat-hold

**Status:** accepted

## Context

Rust is allowed only where an ADR and an honest benchmark against a Python baseline justify it.
Seat-hold takes the sharpest load of the system: at the start of sales many clients ask for the same seats.

Three criteria were fixed before measuring. Rust is justified by at least one of them:

- at least 2x the requests on the same CPU;
- at least 2x lower p99 at the same load;
- at least 2x less memory in the spike scenario, without failed requests.

If none were met, the ADR would propose replacing Rust with Python.
The expected result was also written down: both services would hit the Postgres limit on the write path. On 1 CPU this turned out to be wrong: the service is the limit.

## Method

The baseline is `infra/benchmark/python-seat-hold`: FastAPI, uvicorn with uvloop and httptools, and `asyncpg` directly, with the same SQL, rules, error format and one JSON log line per request.
Both services gave the same result in 16 of 16 behaviour cases (`run_seat_hold.py parity`).

The first baseline logged through `@app.middleware("http")`. Looking into a 3 s tail showed that this was the cause: the middleware (`BaseHTTPMiddleware`) halved the throughput and raised the p99 of the spike from 0.6 s to 3 s.
It was neither the start of the run (the slowest requests were spread over the whole run) nor garbage collection (`gc.disable()` changed nothing).
All numbers below come from the corrected baseline with a pure ASGI middleware, the same kind the real Python services use. With the weak baseline Rust looked 3.0x faster and 15x better in the tail.

Requests go straight to the service with `X-User-Id`, so the gateway is not part of the numbers.
The service got 1 CPU and 512 MB unless stated otherwise, and a pool of 10 connections per CPU. It, Postgres (3 CPUs) and the load generator were pinned to separate cores.
The table is emptied before each run. Each result is the median of 5 runs of 30 s after a 10 s warm-up, Rust and Python alternating. No request failed in any run.

`oha -Z` picks body lines at random with repeats, so unique seats are not possible. To keep the write path, the first scenario uses a TTL of 1 ms over 200,000 seats: every request finds an expired hold and overwrites it.
The cleanup job is off, because the baseline has none.

## Results

| Scenario, 1 CPU | Metric | Rust | Python | Ratio |
|---|---|---|---|---|
| 1. Write path, 64 connections | requests/s | 6,613 | 3,196 | 2.1x |
| | p50 / p99 | 9.4 / 14.3 ms | 19.3 / 33.3 ms | 2.1x / 2.3x |
| 2. One held seat, 64 connections | requests/s | 6,776 | 3,866 | 1.8x |
| | p99 | 14.0 ms | 37.2 ms | 2.7x |
| 3. Fixed 1,598 requests/s | p50 / p99 | 0.6 / 2.2 ms | 1.0 / 5.4 ms | 1.7x / 2.5x |
| | service CPU | 26% | 54% | 2.0x |
| 4. Spike: 1,000 connections, one held seat | requests/s | 6,542 | 3,566 | 1.8x |
| | p99 | 204 ms | 580 ms | 2.8x |
| | peak memory | 50 MB | 78 MB | 1.6x |

Scaling with CPUs (median of 3 runs of 15 s), one Python worker per CPU:

| CPUs | Write path: Rust / Python | Ratio | One held seat: Rust / Python | Ratio | Peak memory: Rust / Python |
|---|---|---|---|---|---|
| 1 | 6,613 / 3,196 | 2.1x | 6,776 / 3,866 | 1.8x | 12 / 51 MB |
| 2 | 10,724 / 6,077 | 1.8x | 7,075 / 5,052 | 1.4x | 14 / 125 MB |
| 4 | 14,859 / 12,372 | 1.2x | 6,701 / 5,204 | 1.3x | 15 / 208 MB |

Idle memory is 8.5 MB against 49 MB, time to `/ready` 0.17 s against 0.80 s, image size 37 MB against 58 MB.

## Decision

Keep Rust for seat-hold, with a smaller claim than the first numbers suggested. It meets the throughput criterion narrowly (2.1x on the write path, 1.8x on a held seat) and the p99 criterion (2.5x at the same load). It misses the memory criterion (1.6x in the spike).

What holds up:

- Half the CPU per request on one core: 26% against 54% at the same load.
- A tail about 2.5 times shorter at every load tested, and 204 ms against 580 ms in the spike.
- Memory that stays flat with CPUs: 15 MB against 208 MB on 4 CPUs, because Python pays for each worker.
- One process with shared memory, which matters for the in-memory state of later milestones. Assumption: not measured yet.

What does not:

- Rust is not several times faster. With a proper baseline the gap is about 2x on one core.
- The gap closes with CPUs: 1.2x on the write path with 4 CPUs, where Postgres is at about 235% of 300%.

A Python seat-hold would be a defensible choice. The numbers justify Rust as the cheaper and steadier service, not as a necessary one. The project also keeps it as a learning goal, which is a reason of its own and is stated here as such.

## A hot seat does not scale

With every request on one seat, neither service gains much from more CPUs: Rust gives 6,776, 7,075 and 6,701 requests/s on 1, 2 and 4 CPUs, and Python stops at about 5,200.
On 4 CPUs both are far from their CPU limit (165% and 214% of 400%), so the limit is elsewhere.

Sampling `pg_stat_activity` under load shows it. Every refused request still takes a row lock in `ON CONFLICT DO UPDATE`, so requests for one seat run one at a time in Postgres: about 35 of 37 active connections wait on `Lock:tuple` on 4 CPUs, against 5 on 1 CPU. More CPUs only make the queue longer.

This is the shape of a real sale, so it matters more than the language. One candidate for M5 is a plain read before the write: a seat that is visibly held can be refused without taking the lock, while the write stays the guarantee.

The write path has a different limit. With 4 CPUs the connections wait mostly on `LWLock:WALWrite` and `IO:WalSync`: every commit has to reach the log on disk.

ADR 0003 said the storage would be revisited if Postgres became the limit. On the write path that happens only near 15,000 requests/s, so that decision stands. The hot seat is a question for M5.

## Consequences

- Two Rust services share the same layout and tooling, so the second one costs less than the first.
- The benchmark stays in `infra/benchmark/`.
- `BaseHTTPMiddleware` must stay out of the Python services. They do not use it today.

## Known limits

- Docker Desktop in a VM on an M1 Max, with the load generator on the same machine: only the ratios matter.
- The write path overwrites expired rows, so it measures `UPDATE`, not `INSERT`.
- Assumption: `fsync` in that VM is cheaper than on a real disk, so the write path would be slower in production for both, and the gap smaller.
- Why `BaseHTTPMiddleware` hurts the tail this much was measured, not explained. Assumption: its extra task and stream per request.
- Scaling results come from 3 shorter runs.

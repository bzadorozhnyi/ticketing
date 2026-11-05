# 0002: Rust for the gateway

**Status:** accepted

## Context

The project allows Rust only where an ADR and an honest benchmark against a Python baseline justify it.
Every request passes through the gateway, and ticket sales start with a traffic spike, so the gateway is the first candidate.

The question is narrow: does the same proxy cost less in Rust than in Python?
Three criteria were fixed before measuring. Rust is justified by at least one of them:

- at least 2x the requests on the same CPU;
- at least 2x lower p99 at the same load;
- at least 2x less memory in the spike scenario, without failed requests.

## Method

The baseline is `infra/benchmark/python-proxy`: FastAPI, uvicorn with uvloop and httptools, aiohttp, PyJWT.
It does the same work per request: JWT check, new `traceparent`, header cleanup, body limit, timeouts, streamed response, one JSON log line.
Both proxies gave the same status and the same upstream view in 16 of 16 behaviour cases (`run.py parity`).

The proxy got 1 CPU and 512 MB unless stated otherwise. It, the upstream and the load generator were pinned to separate cores.
Scenarios 1-3 use a fast stub upstream with a 795-byte body, the size of the real `/api/v1/events` response;
catalog is slower than either proxy and would hide the difference. Scenario 4 uses real catalog and Postgres.
Each result is the median of 5 runs of 30 s after a 10 s warm-up, Rust and Python alternating. No request failed in any measured run.

## Results

| Scenario | Metric | Rust | Python | Ratio |
|---|---|---|---|---|
| 1. Maximum load, 64 connections, 1 CPU | requests/s | 14,733 | 3,943 | 3.7x |
| | p50 / p99 | 4.4 / 5.7 ms | 15.5 / 31.2 ms | 3.5x / 5.5x |
| | peak memory | 15 MB | 61 MB | 4.1x |
| 1. Same, 2 CPUs (2 uvicorn workers) | requests/s | 21,289 | 7,506 | 2.8x |
| | peak memory | 16 MB | 141 MB | 8.8x |
| 2. Fixed 1,970 requests/s | p50 / p99 | 0.3 / 1.1 ms | 0.7 / 4.9 ms | 2.3x / 4.5x |
| 3. Spike: 1,000 connections, upstream takes 100 ms | requests/s | 9,676 | 2,786 | 3.5x |
| | p99 | 112 ms | 484 ms | 4.3x |
| | peak memory | 97 MB | 113 MB | 1.2x |

Scenario 4, through real catalog, with catalog called directly for reference:

| Load | Metric | Direct | Rust | Python |
|---|---|---|---|---|
| Maximum, 16 connections | requests/s | 632 | 599 | 596 |
| | p99 | 63 ms | 78 ms | 67 ms |
| Fixed 100 requests/s | p50 | 1.9 ms | 2.1 ms | 2.4 ms |
| | p99 | 4.1 ms | 4.7 ms | 5.1 ms |

Scaling with CPUs, the same load as scenario 1 (median of 3 runs of 15 s, a separate measurement):

| CPUs | Rust | Python, 1 worker | Python, one worker per CPU | Rust / Python | Peak memory, Rust / Python |
|---|---|---|---|---|---|
| 2 | 20,797 | 3,779 | 7,370 | 2.8x | 17 / 141 MB |
| 4 | 31,185 | 3,793 | 14,078 | 2.2x | 19 / 237 MB |

One Python process stays at about 3,800 requests/s with 99% of one core, however many CPUs it gets: one event loop runs on one thread.
Workers are separate processes, so they scale almost linearly (3.7x on 4 CPUs), at the price of memory.

Idle memory is 8 MB against 57 MB, time to `/ready` 0.16 s against 0.85 s, image size 36 MB against 58 MB.

## Decision

Keep Rust for the gateway. It meets the throughput criterion (3.7x) and the p99 criterion (4.5x). It does not meet the memory criterion (1.2x in the spike).

The gain is cost per request, not speed for the user. With catalog behind it, a client sees 0.3 ms less at p50, and both proxies reach the same throughput,
because catalog is the limit. Rust pays off where the gateway itself is the limit: on a spike one gateway CPU carries about 3.5 times the traffic,
and the requests that M4 and M5 reject at the gateway (rate limiting, load shedding) never reach a slower service.

A Python gateway would also work: 4 Python workers on 4 CPUs (14,078 requests/s) carry about what Rust carries on 1 CPU (14,733), and the ratio shrinks as CPUs are added.
For this project that would have been a valid choice, so the numbers justify Rust as a cheaper gateway, not as a necessary one.

One difference is not in the numbers. Rust threads share memory, while Python workers do not: the in-memory state of M4 (rate limit counters, circuit breaker)
would exist once per worker in Python, or would need an external store. Assumption: this matters more for the gateway than the raw throughput. It is not measured yet.

## Consequences

- The gateway needs Rust skills and a second toolchain, and its code cannot be shared with the Python services.
- The benchmark stays in `infra/benchmark/`, so these numbers can be reproduced after a change.
- Logging every request costs 22% of throughput on 1 CPU and 31% on 2 CPUs. Sampling access logs is an option for M5.

## Known limits

- Docker Desktop runs in a VM on an M1 Max, and the load generator shares the machine. Only the ratios matter, not the absolute numbers.
- Other containers took about 10% of one core during the runs. They were not pinned away from the proxy.
- At maximum load through catalog, Rust had a higher p99 than Python (78 ms against 67 ms, ranges do not overlap). The cause is unknown.
  Assumption: Rust passes all 16 requests to catalog at once, while Python's slower handling spreads them out.
- Rust gained only 1.44x from a second CPU, and 1.62x without per-request logs. On 2 CPUs the gateway was at its CPU limit, not the stub or the load generator; the rest of the gap is unexplained.
  On 4 CPUs it used 307% of 400%, so something else limited it there; the load generator was not checked in that run.
- The baseline is a strong Python setup with one client library. With httpx instead of aiohttp the same proxy reached 746 requests/s (3 runs), 5.3 times less,
  so a comparison against httpx would have overstated the gain of Rust.
- Scenario 2 runs Python at half of its maximum. Closer to its limit the p99 gap would grow.

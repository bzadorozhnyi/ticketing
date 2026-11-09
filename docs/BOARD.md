# Board

Next ticket: TIK-44

## In progress

## To do
- [ ] TIK-41 Gateway: circuit breaker per upstream (M4)
- [ ] TIK-42 Gateway: bulkhead, a concurrency limit per upstream (M4)
- [ ] TIK-43 Booking: circuit breaker for seat-hold and catalog (M4)
- [ ] TIK-37 Seat-hold: limit active holds per user, one user cannot block a hall (M5)

## Done
- [x] TIK-1 Catalog: service skeleton (M1)
- [x] TIK-2 Catalog: Venue and Event (M1)
- [x] TIK-3 Catalog: Section and Seat (M1)
- [x] TIK-4 Catalog: SectionPrice per event (M1)
- [x] TIK-5 Catalog: multistage Dockerfile and compose service (M1)
- [x] TIK-6 Catalog: 503 on database outage, one error format for all errors (M1)
- [x] TIK-7 Catalog: stale pooled connections after a database restart (M1)
- [x] TIK-8 Catalog: trace context in logs (M1)
- [x] TIK-9 Catalog: API contract (M1)
- [x] TIK-10 Booking: service skeleton (M1)
- [x] TIK-11 Payment: service skeleton (M1)
- [x] TIK-12 Gateway: routing to catalog (M1)
- [x] TIK-13 Gateway: JWT auth (M1)
- [x] TIK-14 Gateway: benchmark against a Python baseline, ADR (M1)
- [x] TIK-15 Compose: stop publishing service ports, gateway is the only entry (M1)
- [x] TIK-16 README with architecture diagram (M1)
- [x] TIK-17 CI: GitHub Actions, cargo-deny, breaking contract check (M1)
- [x] TIK-18 Seat-hold: service skeleton (M2)
- [x] TIK-19 Seat-hold: storage ADR (M2)
- [x] TIK-25 CI: run only what changed, reuse dependency images while dependencies are unchanged
- [x] TIK-20 Seat-hold: hold seats atomically, 409 if any is taken (M2)
- [x] TIK-21 Seat-hold: release and TTL (M2)
- [x] TIK-22 Gateway: route seat-hold, owner from X-User-Id (M2)
- [x] TIK-23 Seat-hold: concurrency experiment, exactly one winner per seat (M2)
- [x] TIK-24 Seat-hold: benchmark against a Python baseline, ADR (M2)
- [x] TIK-27 Inter-service communication: ADR and RabbitMQ (M3)
- [x] TIK-28 Payment: idempotent charges and refunds, fault injection (M3)
- [x] TIK-36 Payment: command consumer on RabbitMQ (M3)
- [x] TIK-26 Seat-hold: extend a hold before payment, confirm after it (M3)
- [x] TIK-29 Seat-hold: idempotency key on holds (M3)
- [x] TIK-38 Internal endpoints for the saga: hold details, pricing seats (M3)
- [x] TIK-30 Booking: orders and Idempotency-Key (M3)
- [x] TIK-31 Booking: transactional outbox and relay (M3)
- [x] TIK-32 Booking: saga orchestrator with persisted state (M3)
- [x] TIK-33 Booking: recovery of stuck sagas (M3)
- [x] TIK-39 Payment: idempotency key on the provider call, reserve the charge before it, provider ledger in the mock (M3)
- [x] TIK-34 Gateway: route booking (M3)
- [x] TIK-35 Saga crash experiment: kill after every step, exactly one charge (M3)
- [x] TIK-40 Gateway: retries for idempotent requests with a retry budget (M4)

# Board

Next ticket: TIK-8

## In progress

## To do
- TIK-7 Catalog: stale pooled connections after a database restart (M1)
  - Done when: `pool_pre_ping` latency cost is measured and the decision is recorded; the first request after a database restart no longer fails, if adopted

## Done
- [x] TIK-1 Catalog: service skeleton (M1)
- [x] TIK-2 Catalog: Venue and Event (M1)
- [x] TIK-3 Catalog: Section and Seat (M1)
- [x] TIK-4 Catalog: SectionPrice per event (M1)
- [x] TIK-5 Catalog: multistage Dockerfile and compose service (M1)
- [x] TIK-6 Catalog: 503 on database outage, one error format for all errors (M1)

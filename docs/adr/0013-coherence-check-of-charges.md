# 0013: payment checks that every charge has its order

**Status:** accepted

## Context

A charge must never keep money for an order booking does not have. The design prevents it: booking commits the order
before the charge command leaves through its outbox (ADR 0005), and booking's commits survive a failover since
replication became synchronous and a woken primary stops at once (ADR 0012). The failures that broke it before are
measured fixed: 26 charges without an order in a failover with asynchronous replication (TIK-95), 42 and 54 after a
primary hung for a minute (TIK-97), none in either test since (TIK-96, TIK-98).

The prevention rests on assumptions that code cannot hold: nobody promotes a replica by hand after losing the primary
and a replica together, booking is never restored from a backup older than payment's data, and no bug or operator
fault loses a write. Payment companies that write about it detect as well as prevent: Stripe checks that "every ID in
a producer database has a matching Ledger event" and routes what fails to the owning team; Monzo runs "dozens of
coherence checks" per payment system, "in case we fail to prevent a bug", and engineers acknowledge each break with
evidence. Neither describes fixing the money automatically. Uber's article on its payments platform describes only
prevention and an audit trail.

## Decision

- **payment-consumer checks every minute** (`COHERENCE__INTERVAL_SECONDS`) the charges that took money and were not
  refunded, created between 24 hours (`LOOKBACK_SECONDS`) and 1 minute (`GRACE_SECONDS`) ago, and asks booking which
  of their orders exist: `POST /internal/v1/orders/existing`, 1,000 orders a request, read from booking's primary, as
  its replicas may lag.
- **It reports, a person decides.** The number goes out as `payment.charges.without_order` and the orders into the
  worker's log; `ChargesWithoutOrder` pages when it stays above 0 for 2 minutes. Whether to refund or to restore the
  order needs someone who knows what was lost. A refund made by the check itself would give money away on the first
  wrong answer from booking.
- **A failed check is not a clean one.** It exports no count, and `ChargeCheckFailing` pages when no check has
  succeeded for 10 minutes.
- **In the worker**, one pod, rather than in the two API pods, which would check and log everything twice.
- **aiohttp 3.14.5** for the call, the version booking uses. It answered in 0.5 ms against httpx's 1.1 ms on the same
  local server, which matters little once a minute.

| Option | Why not |
|---|---|
| No check, prevention only | Its assumptions are broken by people and bugs, and nothing would show it |
| Refunding automatically | Money moved on a signal no one has looked at; the companies above route breaks to people |
| A full reconciliation against the provider's ledger | A different question: payment's own records against the outside world. Here both sides are our services |
| The check in the API process | Two pods, so every check and every log line twice |

## Consequences

- Its first run in the cluster found 748 charges without an order: the losses TIK-95 and TIK-97 had measured, and 600
  from TIK-98's runs that those runs had counted as 0 and 100, lost when a stuck replica was promoted (ADR 0012).
- payment calls booking for the check only; payments themselves still go through the queues.
- A charge older than 24 hours is not checked again, so an alert left unanswered for a day goes quiet.
- The other direction, a confirmed order whose charge payment lost (its database is a single instance), is not checked.
- The check reads payment's `charges`, what payment believes it took, not the provider's ledger.

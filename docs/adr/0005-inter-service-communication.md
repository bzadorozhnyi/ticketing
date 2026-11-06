# 0005: Inter-service communication

**Status:** accepted

## Context

A purchase crosses three services, each with its own database, so no single transaction covers it.
The saga in booking calls seat-hold and payment, and a call can fail in the middle: the sender does not know if the work was done.

The guarantees come from persisted saga state, retries and idempotency. They are needed with any transport.
The transport decides how the services depend on each other's availability, and how much new can break.

## Decision

- **booking and payment talk through RabbitMQ.** Payment is the slow and unreliable step, so a queue helps there: booking does not wait for it, and payment does not need booking to be up.
- **Calls to seat-hold stay synchronous HTTP.** Extending and confirming a hold are short and need an answer at once. The Rust service also stays free of a broker client.
- **booking sends through a transactional outbox.** A command is written to an `outbox` table in the same transaction as the saga state, and a relay publishes it. Without it, a crash between the commit and the publish loses the command.
- **Quorum queues, publisher confirms, manual acknowledgement, a dead letter queue.**
- **Clients:** `aio-pika` in Python. It keeps acknowledgements and retries visible, which matters here; `faststream` would hide them.

| Option | Why not |
|---|---|
| HTTP with the database as a queue | Simpler and equally strong for these guarantees. Rejected for a clearer separation: running a step is kept apart from storing it, and payment does not depend on booking being available |
| NATS JetStream | Lighter, with less built in for retries and dead letters |
| Kafka | A log for many readers of history; these are commands with one consumer |
| Redis Streams | Retries and dead letters by hand |

## What happens on a crash

| Crash | Effect | Protection |
|---|---|---|
| After the commit, before the publish | The command waits in the outbox | The relay publishes it |
| After the publish, before marking it sent | A duplicate command | The consumer is idempotent |
| After the charge, before the acknowledgement | The command is delivered again | The idempotency key: no second charge |
| The broker is down | Commands wait in the outbox | Accepted messages survive in quorum queues |
| A message keeps failing | Endless retries | A delivery limit moves it to the dead letter queue |

## Consequences

- Delivery is at least once: a duplicate is a normal event, and every consumer has to expect it.
- One more stateful component, about 150 MB of memory at rest, and one more thing to watch.
- The relay is a background job inside booking. It still reads the database, so messaging does not remove database access; it separates running a step from storing it.
- Tracing across a queue needs `traceparent` in the message headers.

## Measurements

`docker kill` of RabbitMQ with 5 persistent messages in a quorum queue: all 5 were there after the restart and were read back. One run, a single node.

## Known limits

- A single RabbitMQ node: quorum queues replicate only with three nodes, so a lost disk loses the queue.
- Messages can arrive out of order after a redelivery. The saga has to accept a reply only for the step it is waiting for.
- This is revisited if several services need the same events, which would favour publishing events over commands.

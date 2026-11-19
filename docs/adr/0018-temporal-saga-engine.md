# 0018: Temporal as an experimental second saga engine for booking

**Status:** experiment (M9). The saga of ADR 0005 stays the default and is not removed.

## Context

booking runs the purchase as an orchestrated saga that we built ourselves: the step is a column of `orders`
(`saga_step`), `SagaRunner` polls for due orders every 0.5 s and locks them with `FOR UPDATE SKIP LOCKED`, commands
to payment leave through a transactional outbox and a relay (also polling every 0.5 s), `PaymentReplyConsumer`
records answers, and `RecoverOrderUseCase` repeats commands and gives up after 600 s. About 700 lines, a third of
them the purchase itself; TIK-35 showed that a process killed after any step takes money once or returns it.

Durable execution engines offer the same guarantees as a library and a server: a workflow is written as one
function, each side effect is an activity, and the server keeps the history of every step, so a workflow resumes on
another worker after a crash without repeating a step that finished. Temporal is the most widely used of them.

The aim is to show the saga built both ways and compare them with evidence, not to replace a design that works.

## Decision

- **A second engine, chosen per order.** booking gets a Temporal workflow beside the saga, behind a setting
  (`SAGA__ENGINE`, `builtin` by default) that is stored with each order, so an order in flight is always finished by
  the engine that started it. Workflow id = order id, so one order cannot start two workflows.
- **payment does not change.** Commands still go through RabbitMQ; for a Temporal order the reply consumer signals
  the workflow instead of writing the step.
- **Temporal CLI's development server** (`temporalio/temporal` 1.10.1: server 1.32.2, UI 2.56.4): one process with
  SQLite on a volume and the web UI, in compose under the `temporal` profile and in kind as a one-pod StatefulSet
  (`infra/k8s/platform/temporal.yaml`). Its data survives a restart of the container or the pod.
- **Python SDK** `temporalio` 1.36.1, in booking only.
- **Measured before any conclusion** (TIK-117): the crash experiment of TIK-35 on both engines, counted by the
  provider's ledger, the Temporal server stopped under load, and the time from order to `confirmed`.

## Consequences

- Workflow code must be deterministic: no clock, randomness or I/O in it except through Temporal's API, or a replay
  takes another path. Changing a workflow while orders are in flight needs versioning (`workflow.patched`).
- Activities run at least once, so every side effect keeps its idempotency key; Temporal does not remove that work.
- The Temporal server sits on the order path: while it is down, a new order cannot start its workflow. The builtin
  saga keeps accepting orders and finishes them later.
- The development server is not Temporal's production setup (frontend, history, matching and worker services with a
  database of their own, usually through its Helm chart). It is enough for the programming model and failure
  behaviour, not for capacity numbers.

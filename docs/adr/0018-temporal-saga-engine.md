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
  the workflow instead of writing the step. The activities publish the commands themselves, without the outbox:
  after a crash Temporal runs the step again, which is what the outbox was for.
- **The worker starts the workflows** (TIK-116). The API only writes the order with its engine; a starter in the
  worker's process starts each new Temporal order's workflow, since the order and the workflow cannot be written
  together. Starts use `REJECT_DUPLICATE`: with Temporal's default a finished workflow's id can run again, and a
  second run of a paid order would find its hold confirmed and fail it.
- **No payment token in Temporal.** A workflow's input and results stay in its history, so the activity that sends
  the charge reads the token from booking's database.
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

## Measured (TIK-117)

Compose on one laptop, through the gateway, a fresh event per run, 150 orders by 15 buyers (one in ten with a
declined card), money counted by the provider's ledger. Rule agreed before measuring: no order with wrong money, no
seat sold twice, no order left pending, on both engines; Temporal "costs no more" if its p50 to `confirmed` is at
most 20 % above the saga's.

| Run | builtin saga | Temporal |
|---|---|---|
| A: crash points at 5 % (relay 2 %, payment 2.5 % before and after its commit), restarted at once | 28 deaths, all 150 final in 34 s | 34 deaths, all 150 final in 144 s |
| B: `docker kill` of a random process every 1 to 3 s | 26 kills, 57 s | 26 kills, 56 s |
| C: Temporal server stopped for 60 s | | all final 17 s after it came back (83 s) |
| D: Temporal server killed, started 5 s later | | 19 s, no workflow lost |
| E: no fault, 200 orders by 10 buyers, p50 / p99 to `confirmed` | 0.68 / 1.41 s and 1.05 / 1.36 s | 1.29 / 2.01 s and 1.30 / 1.40 s |

In every run of A to D both engines ended with 135 orders `confirmed` with one charge each, 15 `card_declined` with
none, 135 seats sold, no charge left `pending`.

- **Correctness:** equal. Temporal kept every guarantee of the saga under the same faults.
- **Recovery after a worker's death:** slower. The server does not see a worker die; an activity it was running is
  retried only after its `start_to_close_timeout` (30 s here), while the saga's row lock ends with the dead process
  and the next poll, 0.5 s later, takes the order. Heartbeats or a shorter timeout would cut this; not done.
- **Latency:** 23 to 90 % above the saga's p50, so by the rule Temporal costs more here. The saga waits on two polls
  of 0.5 s (runner, relay); Temporal on the starter's poll (p50 0.28 s) and on its own steps, 0.21 s for one order
  and 0.65 s at 10 at once on the development server's SQLite. Not a measure of Temporal's production setup.
- **A finding outside both engines:** with `restart: on-failure`, Docker waits longer before each restart of a
  process that dies within 10 s of starting, up to a minute; under 5 % crash points both engines then needed 15 to
  16 minutes for 150 orders. Run A restarts the processes in a shell loop instead.

The saga stays the default. Temporal stays an experiment: as safe, slower to recover with these timeouts, and
slower on this server.

## Faster recovery (TIK-118)

The server learns that a worker died only from timeouts, so they set the recovery. Run A again, three runs per step,
against the saga's 34, 36 and 32 s; the rule asked for at most twice the saga, 68 s:

| Step | Temporal, run A |
|---|---|
| TIK-117: 30 s `start_to_close` for every activity | 144 s |
| a timeout per activity, a little above its longest legitimate run (`reserve` 15 s, `confirm` 5 s, the rest 10 s) | 85 s |
| and a dead worker's sticky queue handed over after 3 s instead of 10 | 81.5 and 67.5 s |
| and activities heartbeat every 0.5 s with a 2 s `heartbeat_timeout` | 80, 81 and 73 s |
| and a 2 s workflow task timeout instead of 10 | 68, 67 and 57 s |

- Heartbeats alone moved nothing, and the histories said why: no activity was waiting for a timeout, but 29
  workflow tasks waited their 10 s `START_TO_CLOSE` (and 79 the sticky queue). One worker runs every order, so its
  death cuts the workflow tasks of all the orders it was on. The 2 s workflow task timeout met the rule in all three
  runs.
- Heartbeats stay: they let a live but slow activity keep its long `start_to_close` while a dead one is found in
  2 s (`tests/test_order_workflow.py`, a 3 s activity is not repeated; without the beats it is, without end).
- Without faults, 200 orders had no activity or workflow task timed out or failed, p50 1.36 s. Money stayed right in
  every run.
- A short workflow task timeout assumes a task finishes in 2 s; one that does not, under a CPU starved machine, is
  run again from history, which costs CPU, not correctness.

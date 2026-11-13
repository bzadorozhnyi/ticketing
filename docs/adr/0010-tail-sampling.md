# 0010: Tail sampling in the collector

**Status:** accepted

## Context

The gateway traced every request (ADR 0006). In the cluster that was hundreds of traces a second: Tempo, limited to
512 MiB, was killed again and again, and the collector, unable to send, spent 7.5 cores on garbage collection (TIK-60).
The kubelet's probes of the gateway added a trace a second. Most of these traces are reads that nobody opens. The ones
that matter are those a person looks for: a failure, a slow request, a purchase.

## Decision

- **Tail sampling in the OpenTelemetry Collector** (`tail_sampling` processor, beta): the gateway still traces every
  request, the collector holds each trace for `decision_wait` (10 s) and then keeps or drops all of it. Metrics are not
  sampled, so SLOs and dashboards are unchanged.
- **Five policies** in `infra/observability/otel-collector.yaml`, one file for compose and the cluster: drop the probes
  (`/health`, `/ready`); keep any trace with an error; keep any trace longer than 500 ms; keep every `POST` to
  `/api/v1/holds` and `/api/v1/orders`; keep 5 % of the rest.
- **Purchases are kept at once.** A purchase goes on in its saga after the gateway answered, for seconds or minutes in
  a recovery. A span ends, and reaches the collector, only when its work does, so a failure late in a saga arrives after
  the decision and follows it: in a dropped trace it is lost (measured). Keeping every purchase keeps its late spans.
- **A failure the code handles is still an error in its span.** A span ends in error only when an exception leaves it;
  the payment consumer, booking's reply consumer and a saga step catch theirs to retry, so their spans showed success.
  `mark_span_failed()` marks them, and only then does the error policy see a failed payment.
- **The contrib image** `otel/opentelemetry-collector-contrib`, same version: the core distribution has no tail sampling.
- **One collector, recreated, never rolled**: all spans of a trace must reach the same instance, and a rolling update
  runs two for a while (probe traces leaked through the old one for 25 s before `Recreate`).

| Option | Why not |
|---|---|
| Head sampling at the gateway (10 % of reads, every purchase) | Cheaper, and no new image. But a customer's trace id points to nothing 90 % of the time, and a failed read is dropped with the rest |
| Every trace, a bigger Tempo | Storage grows with traffic, for traces nobody reads |
| A custom collector built with OCB | Smaller image and fewer components, but another tool and a build step |
| A new trace at each asynchronous step, linked to the one before | Short traces decide well, but a purchase is no longer one trace; ADR 0006 chose one |
| More policies: a user's traces, a spans-per-second budget (`composite`), traces with many spans (`span_count`) | Possible extensions, none needed yet: a user id in spans is personal data, and the budget's behaviour needs its own experiment |

## Consequences

- Under steady load 5.7 % of traces and 8.6 % of spans reach Tempo; every purchase and every error is there.
- The services still make and send every span: tail sampling saves Tempo, not their CPU. Head sampling of reads can be
  added in front if that cost matters.
- The collector holds about 10 s of traces: 241 MiB at about 460 requests a second. A restart loses the undecided ones.
- More than one collector needs two layers: a first one routing spans by trace id (`load_balancing` exporter, in the
  contrib image) to a second one that samples.
- Tempo's span metrics and service graph now count only kept traces; no dashboard or alert uses them.
- `otelcol validate` accepts an unknown policy type; `infra/check-observability.sh` starts the collector for a moment.

# 0006: Observability

**Status:** accepted

## Context

Every service writes JSON logs with the trace id, and `traceparent` crosses HTTP and RabbitMQ (ADR 0005). There are no
metrics and no spans: the p95 and p99 of M4 and M5 came from the load tool, and nothing shows where a slow request spent
its time. Answering "what happened during the spike" took a script per question.

## Decision

- **OpenTelemetry for traces and metrics** in every service, sent over OTLP/HTTP to an **OpenTelemetry Collector**.
  The services know only the collector; where the data goes is its configuration.
- **Prometheus** keeps metrics: the collector pushes them to its OTLP endpoint. **Tempo** keeps traces and computes
  span metrics and the service graph from them. **Grafana** shows both, with links from a metric's exemplar to its trace.
- The stack is a compose overlay, `infra/observability/`, like Toxiproxy. Without it the services run as before.
- **Telemetry is off without a collector address.** Tests and local runs need nothing new.
- **A trace starts only at the gateway.** Behind it a span without a parent is not recorded
  (`ParentBased(root=ALWAYS_OFF)`): health checks and the polls of the saga and the relay would add thousands of empty traces.
- **One trace per order.** The request's `traceparent` is stored with the order and with every outbox command, and the
  saga steps, the publish and the consumers continue it as children, not as links: each step is caused by one order.
  The trace of a purchase lasts as long as its saga, minutes during a recovery.
- **Messaging spans are written by hand**, not by the aio-pika instrumentation: with an outbox the context to continue
  is the one stored with the command, not the relay's when it publishes.
- **Settings use the standard `OTEL_*` variables** (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`), an exception to
  the `GROUP__NAME` convention: the SDKs read them themselves, and every tool and Kubernetes knows them.
- **Instrumentation lives at the edges**: the framework's own telemetry, driver instrumentation, one middleware for request metrics,
  one line in a module that already handles an event (a breaker opening, a request shed). Use cases get no spans or metrics.
  The hand-written `traceparent` code is replaced by OpenTelemetry's propagation.
- **Labels come from small fixed sets**: route template, method, status, upstream, reason. Never a user, an order or a raw path.
- Latency is recorded as **exponential histograms**, which Prometheus keeps as native histograms: percentiles combine
  across replicas and services, and no service keeps a list of bucket bounds that the others would have to match.
  Both SDKs set it with one view; the sizes are the OpenTelemetry defaults (160 buckets, scale up to 20).

| Option | Why not |
|---|---|
| Prometheus client libraries for metrics, OpenTelemetry for traces only | Two ways to instrument each service. The Rust metrics SDK turned out to be stable, so there is no need |
| Jaeger for traces | A good trace UI of its own, but Grafana is here anyway, and Tempo links metrics to traces in one place |
| Services sending straight to Prometheus and Tempo | Each service would know every backend; the collector also batches and limits memory |
| Logs in Loki | Useful, but not needed to find a slow or failing step; logs stay in Docker for now |

## Versions

Checked on 2026-11-10, each released at least two weeks before.

| Component | Version |
|---|---|
| `otel/opentelemetry-collector` (core distribution) | 0.164.0 |
| `prom/prometheus` | v3.15.1 |
| `grafana/tempo` | 3.1.1 |
| `grafana/grafana` | 13.2.5 |
| Rust: `opentelemetry`, `opentelemetry_sdk`, `opentelemetry-otlp` | 0.33.1 |
| Python: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`; `opentelemetry-instrumentation-asyncpg` | 1.45.2; 0.66b2 |

The Rust project lists metrics as stable and traces as beta. The gateway creates spans with the OpenTelemetry API, not
through `tracing-opentelemetry`: the bridge would tie the trace id to a global `tracing` subscriber. The OTLP exporter
uses the blocking `reqwest` client, as its docs require for the SDK's batch processors, which run on their own threads.

FastAPI 0.162 has OpenTelemetry built in: once a provider is set it makes the server span and
`http.server.request.duration` with the stable names, and it stands down when the contrib instrumentation is in the
stack. So the Python services use it, not `opentelemetry-instrumentation-fastapi`. The SQLAlchemy instrumentation
supports only SQLAlchemy below 2.1, and the services use 2.1.6: database spans come from the `asyncpg` instrumentation
instead, and pool saturation is a gauge read from the SQLAlchemy pool.

## Consequences

- Four more containers when the overlay is on.
- Telemetry costs CPU in every request: at the gateway's maximum 2 % without a collector, 9 % with every request traced,
  4 % with one in ten (`docs/CLAIMS.md`). Sampling is how that cost is chosen.
- A percentile is still an estimate, to a relative error set by the scale the histogram reaches, so a dashboard and
  the load tool can disagree by a few percent. Span metrics computed by Tempo stay classic histograms with their own buckets.
- The saga and the relay work later, in the background: their spans are linked to the order's trace, not nested in it.

## Known limits

- One Prometheus and one Tempo with local storage: losing their volumes loses the history.
- Grafana allows anonymous viewing; Explore needs `admin/admin`. Local only.

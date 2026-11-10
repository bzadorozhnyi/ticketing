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
- **Settings use the standard `OTEL_*` variables** (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`), an exception to
  the `GROUP__NAME` convention: the SDKs read them themselves, and every tool and Kubernetes knows them.
- **Instrumentation lives at the edges**: framework and driver instrumentation, one middleware for request metrics,
  one line in a module that already handles an event (a breaker opening, a request shed). Use cases get no spans or metrics.
  The hand-written `traceparent` code is replaced by OpenTelemetry's propagation.
- **Labels come from small fixed sets**: route template, method, status, upstream, reason. Never a user, an order or a raw path.
- Latency is recorded as histograms, so percentiles can be combined across replicas.

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
| Rust: `opentelemetry`, `opentelemetry_sdk`, `opentelemetry-otlp`; `tracing-opentelemetry` | 0.33.1; 0.34.0 |
| Python: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`; instrumentation | 1.45.2; 0.66b2 |

The Rust project lists metrics as stable and traces as beta. The Python SQLAlchemy instrumentation supports only
SQLAlchemy below 2.1, and the services use 2.1.6: database spans come from the `asyncpg` instrumentation instead, and the
wait for a pooled connection is measured with SQLAlchemy's pool events.

## Consequences

- Four more containers when the overlay is on.
- Telemetry costs CPU in every request; it is measured on the ADR 0002 benchmark when the gateway is instrumented.
- A histogram gives a percentile only to the width of its bucket, so a dashboard and the load tool can disagree.
- The saga and the relay work later, in the background: their spans are linked to the order's trace, not nested in it.

## Known limits

- One Prometheus and one Tempo with local storage: losing their volumes loses the history.
- Grafana allows anonymous viewing; Explore needs `admin/admin`. Local only.

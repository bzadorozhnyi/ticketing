# 0006: Observability

**Status:** accepted

## Context

Every service writes JSON logs with the trace id, and `traceparent` crosses HTTP and RabbitMQ (ADR 0005). There are no
metrics and no spans: the p95 and p99 of M4 and M5 came from the load tool, and nothing shows where a slow request spent
its time. Answering "what happened during the spike" took a script per question.

## Decision

- **OpenTelemetry for traces and metrics** in every service, sent over OTLP/HTTP to an **OpenTelemetry Collector**.
  The services know only the collector; where the data goes is its configuration.
- **Prometheus** keeps metrics: it scrapes them from the collector's Prometheus exporter in the protobuf format, with
  `created-timestamp-zero-ingestion` (TIK-58). The first version pushed them to Prometheus's OTLP endpoint, which
  ignores the start time of a series, so `rate()` never counted the first export of a new series: 15 answers of 503
  in a 5 s outage were missing from the SLO. Protobuf carries the start time, and Prometheus writes a 0 there.
  **Tempo** keeps traces and computes
  span metrics and the service graph from them. **Grafana** shows both, with links from a metric's exemplar to its trace.
- The stack is a compose overlay, `infra/observability/`, like Toxiproxy. Without it the services run as before.
- **Telemetry is off without a collector address.** Tests and local runs need nothing new.
- **A trace starts only at the gateway.** Behind it a span without a parent is not recorded
  (`ParentBased(root=ALWAYS_OFF)`): health checks and the polls of the saga and the relay would add thousands of empty traces.
- **One trace per order.** The request's `traceparent` is stored with the order and with every outbox command, and the
  saga steps, the publish and the consumers continue it as children, not as links: each step is caused by one order.
  The trace of a purchase lasts as long as its saga, minutes during a recovery.
- **Database spans in seat-hold are written by hand**: sqlx makes none, so each repository function opens one span.
  There the context is attached to the task, so a repository needs no extra argument; the gateway passes it explicitly.
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
- **Dashboards and alert rules are files** in the overlay, loaded at start: `grafana/dashboards/` (Services, Purchase,
  SLO) and `rules/slo.yaml`. A change made in the Grafana UI is not saved; it is exported into the file.
- **SLOs per user journey, over 30 days**, measured where the user sees the result: 99.5 % without a 5xx for reading
  the catalog (`/api/v1/events`) and, separately, for checkout (`/api/v1/holds`, `/api/v1/orders`); 99 % of `GET`s
  within 300 ms; 99 % of orders confirmed or failed within 10 s of being created (`booking.order.duration`, recorded
  by the edge after the step that ended the order, so a repeated reply is not counted twice; a failed order carries
  its `failure_reason`). One availability SLO over every request let the reads, 20 times as many, hide a full outage
  of holds (TIK-56). The SLO for purchases cannot see an order that never finishes; `OrdersNotFinishing` covers that.
- **Availability at the edge**, in the cluster only: 99.5 % of the requests at Envoy (ADR 0011) without a 5xx. The
  gateway's metrics miss what Envoy answers itself, such as 503 when the gateway's Service has no pod to send to, while
  the gateway's processes run and export (TIK-79). It counts the gateway's 5xx too, so an outage behind the gateway
  pages twice: here and for its user journey. Prometheus reads Envoy's pods directly (`scrape/envoy.yaml`).
- **`ProcessNotExporting`**: a process that stops has no requests left to fail, so no SLO notices it. The collector
  exposes a `target_info` for each process while it exports, and forgets it 30 s after the last export
  (`metric_expiration`); a job seen in the last hour and absent for 2 minutes pages. `target_info` needs
  `service.instance.id`, which the Python SDK sets itself and the Rust services set at start.
- **Alerts on the burn rate over two windows** (Google SRE Workbook): 14.4 over 1 h and 5 m, 6 over 6 h and 30 m.
  The long window proves the budget is really going, the short one that it still is, so an alert clears a few minutes
  after a fix. Prometheus evaluates them; there is no Alertmanager, since a laptop has nobody to page.
  `promtool test rules` checks them against synthetic series in CI (`infra/check-observability.sh`).

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
- A metric reaches Prometheus after the service's export (every 10 s) and the next scrape (every 10 s): up to 20 s,
  about twice as long as the push. A stopped process pages after about 2.7 minutes instead of 2.2.
- The flag `created-timestamp-zero-ingestion` is experimental; its successors (`st-storage`, `use-start-timestamps`)
  are already listed, so an upgrade of Prometheus may need a new flag. The collector's exporter sends no exemplars
  with native histograms.
- Telemetry costs CPU in every request: at the gateway's maximum 2 % without a collector, 9 % with every request traced,
  4 % with one in ten (`docs/CLAIMS.md`). Sampling is how that cost is chosen.
- A percentile is still an estimate, to a relative error set by the scale the histogram reaches, so a dashboard and
  the load tool can disagree by a few percent. Span metrics computed by Tempo stay classic histograms with their own buckets.
- The saga and the relay work later, in the background: their spans are linked to the order's trace, not nested in it.

## Known limits

- One Prometheus and one Tempo with local storage: losing their volumes loses the history.
- The first scrape of a new series counts only when its start time is near: the Python SDK starts each label set at
  its first measurement, the Rust SDK (`opentelemetry_sdk` 0.33.1) starts every series of an instrument when the
  process starts. A label set that first appears in a gateway or seat-hold running for hours (the first 503 of a
  route) still loses its first scrape, up to 20 s. After a restart, and in every Python service, nothing is lost.
- A 30-day SLO on a stack that lives for hours: the budget panels show the hours there are, not a month. Prometheus
  keeps 31 days; in the cluster at most 3 GiB of its 4 GiB volume (about 1.5 GB for 31 days idle), and past that the
  oldest days go and the window gets shorter without a warning. The 30-day ratios read the whole window, so they run every 2 minutes, not every 15 s.
- An SLO that counts requests weighs a slow period less: clients that wait send fewer requests. 400 ms more on every
  read halved the reads, and the fast burn would have paged after about 15 minutes instead of 9 (TIK-56).
- No metric shows CPU or throttling: a process starved by its CPU limit shows only as long spans in steps of 100 ms
  (TIK-60).
- Grafana allows anonymous viewing; Explore needs `admin/admin`. Local only.

import os
from collections.abc import Iterable

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExponentialBucketHistogramAggregation, View
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from sqlalchemy.pool import QueuePool

from core.db import engine


def setup() -> None:
    """Traces and metrics, see ADR 0006. FastAPI's own telemetry makes the server span
    and `http.server.request.duration`; this sets where they go. Exports go to
    `OTEL_EXPORTER_OTLP_ENDPOINT`; without it spans are not recorded but keep their ids,
    so the logs still carry the trace id that came from the gateway, and metrics go
    nowhere.
    """
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        reader = PeriodicExportingMetricReader(OTLPMetricExporter())
        # Exponential: the buckets follow the values, so no service keeps bounds that
        # the others must match. Prometheus stores it as a native histogram.
        exponential = ExponentialBucketHistogramAggregation()
        views = [View(instrument_type=Histogram, aggregation=exponential)]
        metrics.set_meter_provider(MeterProvider(metric_readers=[reader], views=views))
    else:
        tracer_provider = TracerProvider(sampler=ALWAYS_OFF)
    trace.set_tracer_provider(tracer_provider)

    AsyncPGInstrumentor().instrument()
    metrics.get_meter("catalog").create_observable_gauge(
        "db.client.connection.count",
        callbacks=[_pool_connections],
        description="Pooled database connections by state",
    )


def _pool_connections(_: CallbackOptions) -> Iterable[Observation]:
    pool = engine.sync_engine.pool
    if not isinstance(pool, QueuePool):
        return
    yield Observation(pool.checkedout(), {"db.client.connection.state": "used"})
    yield Observation(pool.checkedin(), {"db.client.connection.state": "idle"})

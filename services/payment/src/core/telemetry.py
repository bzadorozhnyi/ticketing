import os
from collections.abc import Generator, Iterable
from contextlib import contextmanager

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
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ParentBased
from opentelemetry.trace import Span, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy.pool import QueuePool

from core.db import engine
from core.logging import trace_id_var

TRACEPARENT = "traceparent"
PROPAGATOR = TraceContextTextMapPropagator()
tracer = trace.get_tracer("payment")
meter = metrics.get_meter("payment")

# Every instrument of payment. Attributes come from small fixed sets, never an order id.
commands = meter.create_counter(
    "payment.commands", description="Commands handled, by type and outcome"
)


def setup() -> None:
    """Traces and metrics, see ADR 0006, for the API and the command consumer.
    Exports go to `OTEL_EXPORTER_OTLP_ENDPOINT`. A trace starts only at the gateway: a
    span without a parent, such as a health check, is not recorded. Without a collector
    nothing is recorded, but spans keep the trace id they continue, so the logs still
    carry it.
    """
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        tracer_provider = TracerProvider(sampler=ParentBased(root=ALWAYS_OFF))
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
    meter.create_observable_gauge(
        "db.client.connection.count",
        callbacks=[_pool_connections],
        description="Pooled database connections by state",
    )


def current_traceparent() -> str | None:
    """The W3C `traceparent` of the current span, to store or send with work done later.
    None outside any trace."""
    carrier: dict[str, str] = {}
    PROPAGATOR.inject(carrier)
    return carrier.get(TRACEPARENT)


def current_trace_id(span: Span) -> str | None:
    context = span.get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


@contextmanager
def continue_trace(
    traceparent: str | None, *, name: str, kind: SpanKind
) -> Generator[Span]:
    """A span for a command that belongs to an earlier request; its logs carry that
    request's trace id."""
    carrier = {TRACEPARENT: traceparent} if traceparent else {}
    parent = PROPAGATOR.extract(carrier)
    with tracer.start_as_current_span(name, context=parent, kind=kind) as span:
        token = trace_id_var.set(current_trace_id(span))
        try:
            yield span
        finally:
            trace_id_var.reset(token)


def _pool_connections(_: CallbackOptions) -> Iterable[Observation]:
    pool = engine.sync_engine.pool
    if not isinstance(pool, QueuePool):
        return
    yield Observation(pool.checkedout(), {"db.client.connection.state": "used"})
    yield Observation(pool.checkedin(), {"db.client.connection.state": "idle"})

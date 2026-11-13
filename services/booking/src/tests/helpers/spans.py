from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

SPANS = InMemorySpanExporter()


def record_spans() -> None:
    """Every span ends in SPANS. Called before the app is imported: a tracer provider
    can be set once per process, so the app's own `setup()` is then ignored."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SPANS))
    trace.set_tracer_provider(provider)


def spans_named(name: str) -> list[ReadableSpan]:
    return [span for span in SPANS.get_finished_spans() if span.name == name]

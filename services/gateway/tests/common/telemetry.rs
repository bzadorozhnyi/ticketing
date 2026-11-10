use std::sync::OnceLock;

use opentelemetry::global;
use opentelemetry_sdk::trace::{InMemorySpanExporter, SdkTracerProvider};

static SPANS: OnceLock<InMemorySpanExporter> = OnceLock::new();

/// One provider per test binary, as in `main`: every gateway's spans end up here, and a
/// test tells its own apart by the trace id.
pub fn spans() -> &'static InMemorySpanExporter {
    SPANS.get_or_init(|| {
        let exporter = InMemorySpanExporter::default();
        let provider = SdkTracerProvider::builder()
            .with_simple_exporter(exporter.clone())
            .build();
        global::set_tracer_provider(provider);
        exporter
    })
}

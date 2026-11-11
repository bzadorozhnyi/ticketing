use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};
use std::{env, process};

use opentelemetry::KeyValue;
use opentelemetry::global::{self, BoxedTracer};
use opentelemetry_otlp::{ExporterBuildError, MetricExporter, SpanExporter};
use opentelemetry_sdk::Resource;
use opentelemetry_sdk::metrics::{
    Aggregation, Instrument, InstrumentKind, SdkMeterProvider, Stream,
};
use opentelemetry_sdk::trace::{Sampler, SdkTracerProvider};

/// Exports go to `$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces` and `/v1/metrics` over HTTP.
/// The SDK reads the other `OTEL_*` variables itself: `OTEL_SERVICE_NAME`,
/// `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_TRACES_SAMPLER`.
const ENDPOINT: &str = "OTEL_EXPORTER_OTLP_ENDPOINT";

static TRACER: OnceLock<BoxedTracer> = OnceLock::new();

/// Built on first use from the global provider that `init` sets; asking the global
/// provider for a tracer on every request costs a lock and an allocation.
pub fn tracer() -> &'static BoxedTracer {
    TRACER.get_or_init(|| global::tracer("gateway"))
}

/// Holds the providers so that `shutdown` can send what is still buffered.
pub struct Telemetry {
    tracer: SdkTracerProvider,
    meter: Option<SdkMeterProvider>,
}

/// Call first in `main`: an instrument created before this stays a no-op for ever.
///
/// Without `OTEL_EXPORTER_OTLP_ENDPOINT` nothing is exported. Spans are not recorded but
/// still get ids, because the logs and the upstreams need the trace id; the upstreams see
/// them as not sampled. Metrics go to the no-op provider.
///
/// # Errors
/// An exporter that cannot be built from the `OTEL_*` variables.
pub fn init() -> Result<Telemetry, ExporterBuildError> {
    let resource = Resource::builder()
        .with_attribute(KeyValue::new("service.instance.id", instance_id()))
        .build();
    let mut tracer = SdkTracerProvider::builder().with_resource(resource.clone());
    let mut meter = None;
    if env::var_os(ENDPOINT).is_none() {
        tracer = tracer.with_sampler(Sampler::AlwaysOff);
    } else {
        tracer = tracer.with_batch_exporter(SpanExporter::builder().with_http().build()?);
        let provider = SdkMeterProvider::builder()
            .with_resource(resource)
            .with_periodic_exporter(MetricExporter::builder().with_http().build()?)
            .with_view(exponential_histogram)
            .build();
        global::set_meter_provider(provider.clone());
        meter = Some(provider);
    }
    let tracer = tracer.build();
    global::set_tracer_provider(tracer.clone());
    Ok(Telemetry { tracer, meter })
}

/// Every histogram is exponential: its buckets follow the values it sees, so no service
/// keeps a list of bucket bounds that the others would have to match. Prometheus stores
/// it as a native histogram. The sizes are the OpenTelemetry defaults.
fn exponential_histogram(instrument: &Instrument) -> Option<Stream> {
    if instrument.kind() != InstrumentKind::Histogram {
        return None;
    }
    let aggregation = Aggregation::Base2ExponentialHistogram {
        max_size: 160,
        max_scale: 20,
        record_min_max: true,
    };
    Stream::builder().with_aggregation(aggregation).build().ok()
}

impl Telemetry {
    pub fn shutdown(self) {
        if let Err(error) = self.tracer.shutdown() {
            tracing::warn!(%error, "Traces were not flushed");
        }
        if let Some(Err(error)) = self.meter.map(|meter| meter.shutdown()) {
            tracing::warn!(%error, "Metrics were not flushed");
        }
    }
}

/// Python's SDK sets `service.instance.id` itself and the Rust one does not. Without it the collector exposes no
/// `target_info` for the process, so `ProcessNotExporting` would see it as silent. Unique per process, like Python's.
fn instance_id() -> String {
    let started = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| since.as_nanos());
    format!("{}-{started:x}", process::id())
}

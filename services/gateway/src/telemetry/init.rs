use std::env;
use std::sync::OnceLock;

use opentelemetry::global::{self, BoxedTracer};
use opentelemetry_otlp::{ExporterBuildError, MetricExporter, SpanExporter};
use opentelemetry_sdk::Resource;
use opentelemetry_sdk::metrics::SdkMeterProvider;
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
    let resource = Resource::builder().build();
    let mut tracer = SdkTracerProvider::builder().with_resource(resource.clone());
    let mut meter = None;
    if env::var_os(ENDPOINT).is_none() {
        tracer = tracer.with_sampler(Sampler::AlwaysOff);
    } else {
        tracer = tracer.with_batch_exporter(SpanExporter::builder().with_http().build()?);
        let provider = SdkMeterProvider::builder()
            .with_resource(resource)
            .with_periodic_exporter(MetricExporter::builder().with_http().build()?)
            .build();
        global::set_meter_provider(provider.clone());
        meter = Some(provider);
    }
    let tracer = tracer.build();
    global::set_tracer_provider(tracer.clone());
    Ok(Telemetry { tracer, meter })
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

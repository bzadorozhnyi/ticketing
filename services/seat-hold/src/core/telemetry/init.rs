use std::env;
use std::sync::OnceLock;

use opentelemetry::global::{self, BoxedTracer};
use opentelemetry_otlp::{ExporterBuildError, MetricExporter, SpanExporter};
use opentelemetry_sdk::Resource;
use opentelemetry_sdk::metrics::{
    Aggregation, Instrument, InstrumentKind, SdkMeterProvider, Stream,
};
use opentelemetry_sdk::trace::{Sampler, SdkTracerProvider};

/// Exports go to `$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces` and `/v1/metrics` over HTTP.
/// The SDK reads the other `OTEL_*` variables itself.
const ENDPOINT: &str = "OTEL_EXPORTER_OTLP_ENDPOINT";

static TRACER: OnceLock<BoxedTracer> = OnceLock::new();

/// Built on first use from the global provider that `init` sets.
pub fn tracer() -> &'static BoxedTracer {
    TRACER.get_or_init(|| global::tracer("seat-hold"))
}

/// Holds the providers so that `shutdown` can send what is still buffered.
pub struct Telemetry {
    tracer: SdkTracerProvider,
    meter: Option<SdkMeterProvider>,
}

/// Call first in `main`: an instrument created before this stays a no-op for ever.
///
/// A trace starts only at the gateway: a span without a parent, such as a health check
/// or the cleanup job, is not recorded. Without `OTEL_EXPORTER_OTLP_ENDPOINT` nothing is
/// recorded, but spans keep the trace id they continue, so the logs still carry it.
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
        tracer = tracer
            .with_sampler(Sampler::ParentBased(Box::new(Sampler::AlwaysOff)))
            .with_batch_exporter(SpanExporter::builder().with_http().build()?);
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

/// Every histogram is exponential, as in the other services: no bucket list to match.
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

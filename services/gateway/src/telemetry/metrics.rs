use std::sync::OnceLock;

use opentelemetry::KeyValue;
use opentelemetry::global;
use opentelemetry::metrics::{Counter, Histogram, Meter};

use crate::limits::concurrency::ConcurrencyLimit;

/// Every instrument of the gateway. Attributes come from small fixed sets: a route
/// template, a method, a status, an upstream, a reason. Never a user or a raw path.
pub struct GatewayMetrics {
    pub server_duration: Histogram<f64>,
    pub client_duration: Histogram<f64>,
    pub breaker_transitions: Counter<u64>,
    pub shared_rate_limit_skipped: Counter<u64>,
    pub refused_connections: Counter<u64>,
    meter: Meter,
}

static METRICS: OnceLock<GatewayMetrics> = OnceLock::new();

/// Built on first use from the global provider that `telemetry::init` sets.
pub fn metrics() -> &'static GatewayMetrics {
    METRICS.get_or_init(|| GatewayMetrics::new(global::meter("gateway")))
}

impl GatewayMetrics {
    fn new(meter: Meter) -> Self {
        Self {
            server_duration: meter
                .f64_histogram("http.server.request.duration")
                .with_unit("s")
                .build(),
            client_duration: meter
                .f64_histogram("http.client.request.duration")
                .with_unit("s")
                .build(),
            breaker_transitions: meter.u64_counter("gateway.breaker.transitions").build(),
            shared_rate_limit_skipped: meter
                .u64_counter("gateway.rate_limit.shared_skipped")
                .build(),
            refused_connections: meter.u64_counter("gateway.connections.refused").build(),
            meter,
        }
    }

    /// Places taken in each limit, read every time metrics are exported.
    pub fn observe_in_use(
        &self,
        name: &'static str,
        limits: Vec<(ConcurrencyLimit, Vec<KeyValue>)>,
    ) {
        self.meter
            .u64_observable_gauge(name)
            .with_callback(move |observer| {
                for (limit, attributes) in &limits {
                    observer.observe(limit.in_use(), attributes);
                }
            })
            .build();
    }
}

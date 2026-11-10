use std::sync::OnceLock;

use opentelemetry::KeyValue;
use opentelemetry::global;
use opentelemetry::metrics::{Histogram, Meter};
use sqlx::PgPool;

/// Every instrument of seat-hold. Attributes come from small fixed sets: a route
/// template, a method, a status, an error code. Never a seat, a hold or a user.
pub struct SeatHoldMetrics {
    pub server_duration: Histogram<f64>,
    meter: Meter,
}

static METRICS: OnceLock<SeatHoldMetrics> = OnceLock::new();

/// Built on first use from the global provider that `telemetry::init` sets.
pub fn metrics() -> &'static SeatHoldMetrics {
    METRICS.get_or_init(|| {
        let meter = global::meter("seat-hold");
        SeatHoldMetrics {
            server_duration: meter
                .f64_histogram("http.server.request.duration")
                .with_unit("s")
                .build(),
            meter,
        }
    })
}

impl SeatHoldMetrics {
    /// Pooled connections by state, read every time metrics are exported.
    pub fn observe_pool(&self, pool: PgPool) {
        self.meter
            .u64_observable_gauge("db.client.connection.count")
            .with_callback(move |observer| {
                let idle = u64::try_from(pool.num_idle()).unwrap_or(u64::MAX);
                let all = u64::from(pool.size());
                let state = "db.client.connection.state";
                observer.observe(all.saturating_sub(idle), &[KeyValue::new(state, "used")]);
                observer.observe(idle, &[KeyValue::new(state, "idle")]);
            })
            .build();
    }
}

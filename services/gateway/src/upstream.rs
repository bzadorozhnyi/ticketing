use std::sync::Arc;
use std::time::Duration;

use reqwest::Url;

use crate::limits::concurrency::ConcurrencyLimit;
use crate::resilience::breaker::CircuitBreaker;
use crate::resilience::retry::RetryBudget;
use crate::settings::Settings;

/// One upstream with its own timeout, retry budget, circuit breaker and bulkhead:
/// a failing or slow catalog spends nothing of seat-hold's.
#[derive(Clone)]
pub struct Upstream {
    pub url: Url,
    pub timeout: Duration,
    pub budget: Arc<RetryBudget>,
    pub breaker: Arc<CircuitBreaker>,
    /// A permit per request in flight to this upstream, retries included.
    pub bulkhead: ConcurrencyLimit,
}

pub struct UpstreamLimits {
    pub timeout: Option<Duration>,
    pub max_concurrent: Option<usize>,
}

impl Upstream {
    #[must_use]
    pub fn new(
        name: &'static str,
        url: &Url,
        limits: &UpstreamLimits,
        settings: &Settings,
    ) -> Self {
        let max_concurrent = limits
            .max_concurrent
            .unwrap_or(settings.bulkhead.max_concurrent);
        Self {
            url: url.clone(),
            timeout: limits.timeout.unwrap_or(settings.client.request_timeout),
            budget: Arc::new(RetryBudget::new(&settings.retry)),
            breaker: Arc::new(CircuitBreaker::new(name, &settings.breaker)),
            bulkhead: ConcurrencyLimit::new(max_concurrent),
        }
    }
}

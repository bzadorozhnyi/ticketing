use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use reqwest::{Method, RequestBuilder, Response, StatusCode};
use tokio::time::{Instant, sleep};

use crate::settings::RetrySettings;

/// Keeps retries to a share of the requests to one upstream: each request adds
/// `budget_ratio` of a token, each retry takes a whole one. When the upstream is down,
/// retries add at most that share to its load instead of multiplying it.
/// `budget_capacity` is how many retries are allowed in a row while traffic is low.
pub struct RetryBudget {
    tokens: Mutex<f64>,
    ratio: f64,
    capacity: f64,
}

impl RetryBudget {
    #[must_use]
    pub fn new(settings: &RetrySettings) -> Self {
        Self {
            tokens: Mutex::new(settings.budget_capacity),
            ratio: settings.budget_ratio,
            capacity: settings.budget_capacity,
        }
    }

    fn deposit(&self) {
        let mut tokens = self.lock();
        *tokens = (*tokens + self.ratio).min(self.capacity);
    }

    fn withdraw(&self) -> bool {
        let mut tokens = self.lock();
        if *tokens < 1.0 {
            return false;
        }
        *tokens -= 1.0;
        true
    }

    fn lock(&self) -> MutexGuard<'_, f64> {
        self.tokens.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// Sends the request and repeats it while a repeat is safe and may help: only `GET` and
/// `HEAD`, only when no answer came (refused, reset or closed connection) or the upstream
/// answered 503, only while the budget has a token and the deadline leaves time.
/// The client sees the last answer.
pub async fn send(
    settings: &RetrySettings,
    budget: &RetryBudget,
    method: &Method,
    deadline: Instant,
    request: impl Fn(Duration) -> RequestBuilder,
) -> Result<Response, reqwest::Error> {
    budget.deposit();
    let mut attempt = 1;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        let result = request(remaining).send().await;
        let (status, no_answer) = match &result {
            Ok(response) => (Some(response.status()), false),
            Err(error) => (None, !error.is_timeout()),
        };
        if attempt >= settings.max_attempts || !worth_retrying(method, status, no_answer) {
            return result;
        }
        let pause = pause_before(attempt, settings.base_delay);
        if Instant::now() + pause >= deadline || !budget.withdraw() {
            return result;
        }
        sleep(pause).await;
        attempt += 1;
    }
}

/// A lost connection is repeated even if the upstream may have done the work: that is
/// what idempotent means. A timeout is not: the attempt had the rest of the deadline,
/// and a slow upstream gets no extra load. A 500 is most likely a bug, and a 502 or 504
/// from the upstream is the answer of another proxy.
fn worth_retrying(method: &Method, status: Option<StatusCode>, no_answer: bool) -> bool {
    let idempotent = method == Method::GET || method == Method::HEAD;
    idempotent && (no_answer || status == Some(StatusCode::SERVICE_UNAVAILABLE))
}

/// Full jitter: a random pause up to `base * 2^(attempt - 1)`, so the repeats of many
/// clients spread out instead of arriving together.
fn pause_before(attempt: u32, base: Duration) -> Duration {
    let ceiling = base.saturating_mul(2_u32.saturating_pow(attempt.saturating_sub(1)));
    ceiling.mul_f64(rand::random::<f64>())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn budget(ratio: f64, capacity: f64) -> RetryBudget {
        RetryBudget::new(&RetrySettings {
            budget_ratio: ratio,
            budget_capacity: capacity,
            ..RetrySettings::default()
        })
    }

    #[test]
    fn only_safe_methods_and_transient_failures_are_repeated() {
        let unavailable = Some(StatusCode::SERVICE_UNAVAILABLE);
        assert!(worth_retrying(&Method::GET, unavailable, false));
        assert!(worth_retrying(&Method::HEAD, None, true));
        assert!(!worth_retrying(&Method::POST, unavailable, false));
        assert!(!worth_retrying(&Method::POST, None, true));
        assert!(!worth_retrying(&Method::DELETE, unavailable, false));
        assert!(!worth_retrying(&Method::GET, None, false));
        assert!(!worth_retrying(
            &Method::GET,
            Some(StatusCode::INTERNAL_SERVER_ERROR),
            false
        ));
        assert!(!worth_retrying(&Method::GET, Some(StatusCode::OK), false));
        assert!(!worth_retrying(
            &Method::GET,
            Some(StatusCode::BAD_GATEWAY),
            false
        ));
    }

    #[test]
    fn a_full_budget_allows_its_capacity_in_a_row() {
        let budget = budget(0.0, 3.0);

        let allowed = (0..10).filter(|_| budget.withdraw()).count();

        assert_eq!(allowed, 3);
    }

    #[test]
    fn requests_refill_the_budget_by_their_share_up_to_the_capacity() {
        let budget = budget(0.25, 2.0);
        while budget.withdraw() {}

        for _ in 0..8 {
            budget.deposit();
        }

        assert_eq!((0..10).filter(|_| budget.withdraw()).count(), 2);
    }

    #[test]
    fn pauses_are_random_and_grow_with_each_attempt() {
        let base = Duration::from_millis(100);

        let first: Vec<Duration> = (0..1000).map(|_| pause_before(1, base)).collect();
        let third: Vec<Duration> = (0..1000).map(|_| pause_before(3, base)).collect();

        assert!(first.iter().all(|pause| *pause < base));
        assert!(third.iter().all(|pause| *pause < base * 4));
        assert!(third.iter().any(|pause| *pause > base * 2));
        assert!(first.iter().any(|pause| *pause < base / 10));
    }
}

use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use opentelemetry::Context;
use reqwest::header::HeaderMap;
use reqwest::{Method, RequestBuilder, Response, StatusCode};
use tokio::time::{Instant, sleep};

use crate::errors::GatewayError;
use crate::resilience::breaker::CircuitBreaker;
use crate::settings::RetrySettings;
use crate::telemetry::upstream::UpstreamAttempt;

const UPSTREAM_FAILURES: [StatusCode; 3] = [
    StatusCode::BAD_GATEWAY,
    StatusCode::SERVICE_UNAVAILABLE,
    StatusCode::GATEWAY_TIMEOUT,
];

pub enum SendError {
    Upstream(reqwest::Error),
    CircuitOpen { retry_after: Duration },
}

impl From<SendError> for GatewayError {
    fn from(error: SendError) -> Self {
        match error {
            SendError::Upstream(error) => error.into(),
            SendError::CircuitOpen { retry_after } => Self::CircuitOpen { retry_after },
        }
    }
}

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

/// The request as the retry loop sees it: its method names each attempt, and only an
/// idempotent request is repeated.
pub struct RequestKind<'a> {
    pub method: &'a Method,
    pub idempotent: bool,
}

impl<'a> RequestKind<'a> {
    /// `GET` and `HEAD` are idempotent by definition; any other request is when it carries
    /// an `Idempotency-Key`, which an upstream that receives one must honor: a repeat with
    /// the same key gets the first result and repeats no side effect.
    #[must_use]
    pub fn of(method: &'a Method, headers: &HeaderMap) -> Self {
        let idempotent = method == Method::GET
            || method == Method::HEAD
            || headers.contains_key("idempotency-key");
        Self { method, idempotent }
    }
}

/// Sends the request and repeats it while a repeat is safe and may help (`worth_retrying`),
/// while the budget has a token, and while the time left after the pause is at least what
/// the attempt took: with less, a repeat would only end in a timeout and turn the
/// upstream's answer into a 504. The client sees the last answer.
///
/// The breaker is asked before every attempt and told the result after it. When it is
/// open, the first attempt is not made at all, and a retry returns the last answer.
///
/// # Errors
/// The last error of the upstream, or `CircuitOpen` when no attempt was allowed.
pub async fn send(
    parent: &Context,
    settings: &RetrySettings,
    budget: &RetryBudget,
    breaker: &CircuitBreaker,
    kind: &RequestKind<'_>,
    deadline: Instant,
    request: impl Fn(Duration) -> RequestBuilder,
) -> Result<Response, SendError> {
    budget.deposit();
    let mut call = breaker
        .try_call()
        .map_err(|retry_after| SendError::CircuitOpen { retry_after })?;
    let mut attempt = 1;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        let span = UpstreamAttempt::start(parent, breaker.name(), kind.method, attempt);
        let started = Instant::now();
        let result = span.propagate(request(remaining)).send().await;
        let took = started.elapsed();
        span.finish(&result);
        call.finish(is_failure(&result));
        if attempt >= settings.max_attempts
            || !worth_retrying(kind.idempotent, Outcome::of(&result))
        {
            return result.map_err(SendError::Upstream);
        }
        let pause = pause_before(attempt, settings.base_delay);
        if Instant::now() + pause + took >= deadline || !budget.withdraw() {
            return result.map_err(SendError::Upstream);
        }
        sleep(pause).await;
        match breaker.try_call() {
            Ok(next) => call = next,
            Err(_) => return result.map_err(SendError::Upstream),
        }
        attempt += 1;
    }
}

/// What makes the breaker count a failure: no answer, or an answer that says the
/// upstream cannot serve now. A 404 means the upstream works, and a 500 is a bug that
/// a pause does not fix.
fn is_failure(result: &Result<Response, reqwest::Error>) -> bool {
    match result {
        Ok(response) => UPSTREAM_FAILURES.contains(&response.status()),
        Err(_) => true,
    }
}

/// What an attempt says about trying again.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Outcome {
    /// No connection was made, refused or not in time: the upstream got nothing.
    NotSent,
    /// Sent, then the connection was reset or closed before an answer.
    NoAnswer,
    /// No complete answer within the time the attempt had.
    TimedOut,
    Answered(StatusCode),
}

impl Outcome {
    /// reqwest reports a connect timeout as both a connect error and a timeout, so the
    /// connect check comes first.
    fn of(result: &Result<Response, reqwest::Error>) -> Self {
        match result {
            Ok(response) => Self::Answered(response.status()),
            Err(error) if error.is_connect() => Self::NotSent,
            Err(error) if error.is_timeout() => Self::TimedOut,
            Err(_) => Self::NoAnswer,
        }
    }
}

/// A request that never left is repeated whatever its method. A lost connection is
/// repeated only for an idempotent request, since the upstream may have done the work.
/// A timeout is not: the attempt had the rest of the deadline, and a slow upstream gets
/// no extra load. A 500 is most likely a bug, and a 502 or 504 from the upstream is the
/// answer of another proxy.
fn worth_retrying(idempotent: bool, outcome: Outcome) -> bool {
    match outcome {
        Outcome::NotSent => true,
        Outcome::NoAnswer => idempotent,
        Outcome::Answered(status) => idempotent && status == StatusCode::SERVICE_UNAVAILABLE,
        Outcome::TimedOut => false,
    }
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
    fn safe_methods_and_requests_with_an_idempotency_key_are_idempotent() {
        let none = HeaderMap::new();
        let mut keyed = HeaderMap::new();
        keyed.insert("idempotency-key", "order-1".parse().unwrap());
        assert!(RequestKind::of(&Method::GET, &none).idempotent);
        assert!(RequestKind::of(&Method::HEAD, &none).idempotent);
        assert!(RequestKind::of(&Method::POST, &keyed).idempotent);
        assert!(RequestKind::of(&Method::DELETE, &keyed).idempotent);
        assert!(!RequestKind::of(&Method::POST, &none).idempotent);
        assert!(!RequestKind::of(&Method::DELETE, &none).idempotent);
    }

    #[test]
    fn a_request_that_never_left_is_repeated_whatever_its_method() {
        assert!(worth_retrying(true, Outcome::NotSent));
        assert!(worth_retrying(false, Outcome::NotSent));
    }

    #[test]
    fn only_idempotent_requests_and_transient_failures_are_repeated() {
        let unavailable = Outcome::Answered(StatusCode::SERVICE_UNAVAILABLE);
        assert!(worth_retrying(true, unavailable));
        assert!(worth_retrying(true, Outcome::NoAnswer));
        assert!(!worth_retrying(false, unavailable));
        assert!(!worth_retrying(false, Outcome::NoAnswer));
        assert!(!worth_retrying(true, Outcome::TimedOut));
        for status in [
            StatusCode::INTERNAL_SERVER_ERROR,
            StatusCode::OK,
            StatusCode::BAD_GATEWAY,
        ] {
            assert!(!worth_retrying(true, Outcome::Answered(status)));
        }
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

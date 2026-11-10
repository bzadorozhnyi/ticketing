use std::collections::VecDeque;
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use opentelemetry::KeyValue;
use tokio::time::Instant;

use crate::settings::BreakerSettings;
use crate::telemetry::metrics::metrics;

/// Stops calling an upstream that keeps failing, so clients get an answer at once and
/// the upstream gets time to recover. It opens when `failure_percent` of the last
/// `window` calls failed, at least `min_calls` of them; it stays open for
/// `open_duration`, then lets one probe through: a success closes it, a failure opens it
/// again. Every gateway replica has its own breaker per upstream.
pub struct CircuitBreaker {
    name: &'static str,
    settings: BreakerSettings,
    state: Mutex<State>,
}

struct State {
    mode: Mode,
    results: VecDeque<bool>,
}

#[derive(Clone, Copy)]
enum Mode {
    Closed,
    Open { until: Instant },
    HalfOpen { probing: bool },
}

/// Permission for one call to the upstream. A probe dropped without a result, because
/// the client went away, frees the half-open state for the next request; otherwise the
/// breaker would wait for that probe for ever.
pub struct Call<'a> {
    breaker: &'a CircuitBreaker,
    probe: bool,
    finished: bool,
}

impl Call<'_> {
    pub fn finish(mut self, failed: bool) {
        self.finished = true;
        self.breaker.record(self.probe, failed);
    }
}

impl Drop for Call<'_> {
    fn drop(&mut self) {
        if self.probe && !self.finished {
            self.breaker.release_probe();
        }
    }
}

impl CircuitBreaker {
    #[must_use]
    pub fn new(name: &'static str, settings: &BreakerSettings) -> Self {
        Self {
            name,
            settings: settings.clone(),
            state: Mutex::new(State {
                mode: Mode::Closed,
                results: VecDeque::new(),
            }),
        }
    }

    /// A call, or how long to wait before the breaker may let one through.
    ///
    /// # Errors
    /// The time left while the breaker is open or a probe is under way.
    #[must_use]
    pub fn name(&self) -> &'static str {
        self.name
    }

    pub fn try_call(&self) -> Result<Call<'_>, Duration> {
        let mut state = self.lock();
        let now = Instant::now();
        match state.mode {
            Mode::Closed => Ok(self.call(false)),
            Mode::Open { until } if now < until => Err(until - now),
            Mode::Open { .. } | Mode::HalfOpen { probing: false } => {
                state.mode = Mode::HalfOpen { probing: true };
                Ok(self.call(true))
            }
            Mode::HalfOpen { probing: true } => Err(self.settings.open_duration),
        }
    }

    fn call(&self, probe: bool) -> Call<'_> {
        Call {
            breaker: self,
            probe,
            finished: false,
        }
    }

    fn record(&self, probe: bool, failed: bool) {
        let mut state = self.lock();
        if probe {
            if failed {
                self.open(&mut state);
            } else {
                state.mode = Mode::Closed;
                tracing::info!(upstream = self.name, "Circuit closed");
                self.count_transition("closed");
            }
            return;
        }
        // A call that started before the breaker opened must not keep it open longer.
        if !matches!(state.mode, Mode::Closed) {
            return;
        }
        state.results.push_back(failed);
        if state.results.len() > self.settings.window {
            state.results.pop_front();
        }
        let calls = state.results.len();
        let failures = state.results.iter().filter(|failed| **failed).count();
        if calls >= self.settings.min_calls
            && failures * 100 >= self.settings.failure_percent * calls
        {
            self.open(&mut state);
        }
    }

    fn open(&self, state: &mut State) {
        state.mode = Mode::Open {
            until: Instant::now() + self.settings.open_duration,
        };
        state.results.clear();
        tracing::warn!(upstream = self.name, "Circuit opened");
        self.count_transition("open");
    }

    fn count_transition(&self, state: &'static str) {
        let attributes = [
            KeyValue::new("upstream", self.name),
            KeyValue::new("state", state),
        ];
        metrics().breaker_transitions.add(1, &attributes);
    }

    fn release_probe(&self) {
        let mut state = self.lock();
        if matches!(state.mode, Mode::HalfOpen { probing: true }) {
            state.mode = Mode::HalfOpen { probing: false };
        }
    }

    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn breaker(window: usize, min_calls: usize, open: Duration) -> CircuitBreaker {
        CircuitBreaker::new(
            "test",
            &BreakerSettings {
                window,
                min_calls,
                failure_percent: 50,
                open_duration: open,
            },
        )
    }

    fn calls(breaker: &CircuitBreaker, failures: &[bool]) {
        for failed in failures {
            breaker
                .try_call()
                .expect("a closed breaker")
                .finish(*failed);
        }
    }

    #[test]
    fn opens_at_the_failure_share_once_enough_calls_were_seen() {
        let breaker = breaker(4, 4, Duration::from_secs(60));

        calls(&breaker, &[true, false, true]);
        assert!(
            breaker.try_call().is_ok(),
            "three calls are below the minimum"
        );
        breaker.try_call().expect("still closed").finish(false);

        assert!(breaker.try_call().is_err());
    }

    #[test]
    fn only_the_last_calls_count() {
        let breaker = breaker(4, 4, Duration::from_secs(60));

        calls(&breaker, &[false; 8]);
        calls(&breaker, &[true, true]);

        assert!(
            breaker.try_call().is_err(),
            "two of the last four failed, though only two of all ten"
        );
    }

    #[test]
    fn stays_closed_below_the_failure_share() {
        let breaker = breaker(10, 10, Duration::from_secs(60));

        calls(
            &breaker,
            &[
                true, false, false, true, false, false, true, false, false, false,
            ],
        );

        assert!(breaker.try_call().is_ok());
    }

    #[test]
    fn lets_one_probe_through_after_the_open_time() {
        let breaker = breaker(2, 2, Duration::from_millis(20));
        calls(&breaker, &[true, true]);
        std::thread::sleep(Duration::from_millis(30));

        let probe = breaker.try_call().expect("a probe");

        assert!(breaker.try_call().is_err(), "only one probe at a time");
        probe.finish(false);
        assert!(breaker.try_call().is_ok(), "a successful probe closes it");
    }

    #[test]
    fn a_failed_probe_opens_it_again() {
        let breaker = breaker(2, 2, Duration::from_millis(20));
        calls(&breaker, &[true, true]);
        std::thread::sleep(Duration::from_millis(30));

        breaker.try_call().expect("a probe").finish(true);

        assert!(breaker.try_call().is_err());
    }

    #[test]
    fn a_dropped_probe_frees_the_way_for_the_next_one() {
        let breaker = breaker(2, 2, Duration::from_millis(20));
        calls(&breaker, &[true, true]);
        std::thread::sleep(Duration::from_millis(30));

        drop(breaker.try_call().expect("a probe"));

        assert!(breaker.try_call().is_ok());
    }

    #[test]
    fn late_failures_of_calls_started_before_it_opened_do_not_keep_it_open() {
        let breaker = breaker(2, 2, Duration::from_millis(100));
        let early: Vec<Call<'_>> = (0..2).map(|_| breaker.try_call().unwrap()).collect();
        let late: Vec<Call<'_>> = (0..2).map(|_| breaker.try_call().unwrap()).collect();
        for call in early {
            call.finish(true);
        }
        std::thread::sleep(Duration::from_millis(60));

        for call in late {
            call.finish(true);
        }
        std::thread::sleep(Duration::from_millis(60));

        assert!(
            breaker.try_call().is_ok(),
            "open for 100 ms from the first opening"
        );
    }

    #[test]
    fn a_closed_breaker_forgets_the_failures_before_it_opened() {
        let breaker = breaker(4, 2, Duration::from_millis(20));
        calls(&breaker, &[true, true]);
        std::thread::sleep(Duration::from_millis(30));
        breaker.try_call().expect("a probe").finish(false);

        calls(&breaker, &[true]);

        assert!(breaker.try_call().is_ok(), "one failure since it closed");
    }
}

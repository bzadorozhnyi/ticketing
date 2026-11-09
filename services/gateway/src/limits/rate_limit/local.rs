use std::collections::HashMap;
use std::sync::{Mutex, PoisonError};
use std::time::Duration;

use tokio::time::Instant;

use crate::limits::rate_limit::decision::Decision;

struct Bucket {
    tokens: f64,
    at: Instant,
}

struct LocalState {
    buckets: HashMap<String, Bucket>,
    next_prune: Instant,
}

/// The first tier: the same token bucket per user, in this replica's memory. It refuses
/// a user over the limit without a network call, so a flood never reaches Valkey, and
/// it keeps limiting while Valkey fails: then a user gets at most the limit times the
/// number of replicas.
pub struct LocalBuckets {
    per_second: f64,
    burst: f64,
    max_users: usize,
    state: Mutex<LocalState>,
}

impl LocalBuckets {
    #[must_use]
    pub fn new(per_second: f64, burst: u32, max_users: usize) -> Self {
        Self {
            per_second,
            burst: f64::from(burst),
            max_users,
            state: Mutex::new(LocalState {
                buckets: HashMap::new(),
                next_prune: Instant::now(),
            }),
        }
    }

    pub fn take(&self, user: &str, now: Instant) -> Decision {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(bucket) = state.buckets.get_mut(user) {
            return self.take_from(bucket, now);
        }
        if !self.make_room(&mut state, now) {
            return Decision::Allowed;
        }
        let mut bucket = Bucket {
            tokens: self.burst,
            at: now,
        };
        let decision = self.take_from(&mut bucket, now);
        state.buckets.insert(user.to_owned(), bucket);
        decision
    }

    fn take_from(&self, bucket: &mut Bucket, now: Instant) -> Decision {
        bucket.tokens = self.refilled(bucket, now);
        bucket.at = now;
        if bucket.tokens >= 1.0 {
            bucket.tokens -= 1.0;
            return Decision::Allowed;
        }
        let wait = (1.0 - bucket.tokens) / self.per_second;
        Decision::Limited {
            retry_after: Duration::try_from_secs_f64(wait).unwrap_or(Duration::MAX),
        }
    }

    fn refilled(&self, bucket: &Bucket, now: Instant) -> f64 {
        let elapsed = now.saturating_duration_since(bucket.at).as_secs_f64();
        (bucket.tokens + elapsed * self.per_second).min(self.burst)
    }

    /// A full bucket is the same as no bucket, so those go first. The map is scanned at
    /// most once per time an empty bucket takes to fill: many active users must not
    /// make every new one pay for a scan.
    fn make_room(&self, state: &mut LocalState, now: Instant) -> bool {
        if state.buckets.len() < self.max_users {
            return true;
        }
        if now >= state.next_prune {
            state
                .buckets
                .retain(|_, bucket| self.refilled(bucket, now) < self.burst);
            let fill_time = Duration::try_from_secs_f64(self.burst / self.per_second);
            state.next_prune = now + fill_time.unwrap_or(Duration::MAX);
        }
        state.buckets.len() < self.max_users
    }
}

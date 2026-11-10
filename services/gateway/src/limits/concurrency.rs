use std::sync::Arc;

use tokio::sync::{OwnedSemaphorePermit, Semaphore};

/// At most `max` pieces of work at once: requests to one upstream, requests in the
/// gateway, open connections. A full limit refuses at once and never queues: a queue
/// would make every request slower and hold its memory while it waits.
#[derive(Clone)]
pub struct ConcurrencyLimit {
    places: Arc<Semaphore>,
    max: usize,
}

impl ConcurrencyLimit {
    #[must_use]
    pub fn new(max: usize) -> Self {
        Self {
            places: Arc::new(Semaphore::new(max)),
            max,
        }
    }

    /// A place held until the permit is dropped, or `None` when the limit is full.
    #[must_use]
    pub fn try_enter(&self) -> Option<OwnedSemaphorePermit> {
        Arc::clone(&self.places).try_acquire_owned().ok()
    }

    #[must_use]
    pub fn in_use(&self) -> u64 {
        let free = self.places.available_permits();
        u64::try_from(self.max.saturating_sub(free)).unwrap_or(u64::MAX)
    }
}

use std::sync::Arc;

use tokio::sync::{OwnedSemaphorePermit, Semaphore};

/// At most `max` pieces of work at once: requests to one upstream, requests in the
/// gateway, open connections. A full limit refuses at once and never queues: a queue
/// would make every request slower and hold its memory while it waits.
#[derive(Clone)]
pub struct ConcurrencyLimit(Arc<Semaphore>);

impl ConcurrencyLimit {
    #[must_use]
    pub fn new(max: usize) -> Self {
        Self(Arc::new(Semaphore::new(max)))
    }

    /// A place held until the permit is dropped, or `None` when the limit is full.
    #[must_use]
    pub fn try_enter(&self) -> Option<OwnedSemaphorePermit> {
        Arc::clone(&self.0).try_acquire_owned().ok()
    }
}

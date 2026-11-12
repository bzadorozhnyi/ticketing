use std::sync::{Arc, PoisonError, RwLock, Weak};
use std::time::Duration;

use reqwest::Client;
use tokio::time::sleep;

/// The HTTP client for upstream calls, replaced by a new one every `max_age`.
///
/// A Kubernetes Service balances connections, not requests, and kept-alive connections stay
/// on the pods that existed when they opened. A new client has an empty pool, so its
/// connections reach the pods as they are now. A call made on the old client keeps it alive
/// until the call ends; its idle connections close with it.
#[derive(Clone)]
pub struct RotatingClient {
    current: Arc<RwLock<Client>>,
}

impl RotatingClient {
    /// A zero `max_age` keeps the first client for good. The replacing task ends with the
    /// last clone of this value.
    ///
    /// # Errors
    /// The first client cannot be built.
    pub fn new<F>(build: F, max_age: Duration) -> Result<Self, reqwest::Error>
    where
        F: Fn() -> Result<Client, reqwest::Error> + Send + 'static,
    {
        let current = Arc::new(RwLock::new(build()?));
        if !max_age.is_zero() {
            tokio::spawn(replace_every(Arc::downgrade(&current), build, max_age));
        }
        Ok(Self { current })
    }

    #[must_use]
    pub fn get(&self) -> Client {
        self.current
            .read()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
    }
}

async fn replace_every<F>(current: Weak<RwLock<Client>>, build: F, max_age: Duration)
where
    F: Fn() -> Result<Client, reqwest::Error>,
{
    loop {
        sleep(max_age).await;
        let Some(current) = current.upgrade() else {
            return;
        };
        match build() {
            Ok(client) => *current.write().unwrap_or_else(PoisonError::into_inner) = client,
            Err(error) => tracing::warn!(error = %error, "Upstream client kept: a new one failed"),
        }
    }
}

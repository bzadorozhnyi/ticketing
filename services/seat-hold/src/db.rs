use std::future::Future;
use std::ops::DerefMut;

use sqlx::pool::PoolConnection;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{PgConnection, PgPool, Postgres};

use crate::settings::Settings;

/// Code that needs the database asks this trait for a connection. Production uses the
/// pool; tests use one connection inside a transaction that is rolled back at the end.
pub trait DbPool: Clone + Send + Sync + 'static {
    type Conn: DerefMut<Target = PgConnection> + Send;

    fn get_conn(&self) -> impl Future<Output = Result<Self::Conn, sqlx::Error>> + Send;
}

impl DbPool for PgPool {
    type Conn = PoolConnection<Postgres>;

    async fn get_conn(&self) -> Result<Self::Conn, sqlx::Error> {
        self.acquire().await
    }
}

/// The pool connects lazily, so the service starts during a database outage
/// and reports it through `/ready`.
#[must_use]
pub fn build_pool(settings: &Settings) -> PgPool {
    let options = PgConnectOptions::new()
        .host(&settings.db.host)
        .port(settings.db.port)
        .username(&settings.db.user)
        .password(&settings.db.pass)
        .database(&settings.db.name);
    PgPoolOptions::new()
        .max_connections(settings.pool.max_connections)
        .acquire_timeout(settings.pool.acquire_timeout)
        .connect_lazy_with(options)
}

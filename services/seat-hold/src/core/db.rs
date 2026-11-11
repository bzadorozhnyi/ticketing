use std::future::Future;
use std::ops::DerefMut;
use std::time::Duration;

use sqlx::pool::PoolConnection;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{PgConnection, PgPool, Postgres};

use crate::core::settings::{DbSettings, PoolSettings, Settings};

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

/// The service's pool. It connects lazily, so the service starts during a database outage
/// and reports it through `/ready`. Its sessions have the lock and statement timeouts.
#[must_use]
pub fn build_pool(settings: &Settings) -> PgPool {
    pool_options(&settings.pool).connect_lazy_with(with_session_limits(
        connect_options(&settings.db),
        &settings.pool,
    ))
}

/// Migrations run without the service's timeouts: building an index or filling a column
/// may take minutes.
#[must_use]
pub fn build_migration_pool(settings: &Settings) -> PgPool {
    pool_options(&settings.pool).connect_lazy_with(connect_options(&settings.db))
}

/// Every session starts with `lock_timeout` and `statement_timeout` set: Postgres then cancels
/// a statement that waits too long, and the connection goes back to the pool.
#[must_use]
pub fn with_session_limits(options: PgConnectOptions, pool: &PoolSettings) -> PgConnectOptions {
    options.options([
        ("lock_timeout", milliseconds(pool.lock_timeout)),
        ("statement_timeout", milliseconds(pool.statement_timeout)),
    ])
}

fn connect_options(db: &DbSettings) -> PgConnectOptions {
    PgConnectOptions::new()
        .host(&db.host)
        .port(db.port)
        .username(&db.user)
        .password(&db.pass)
        .database(&db.name)
}

fn pool_options(pool: &PoolSettings) -> PgPoolOptions {
    PgPoolOptions::new()
        .max_connections(pool.max_connections)
        .acquire_timeout(pool.acquire_timeout)
}

fn milliseconds(duration: Duration) -> String {
    format!("{}ms", duration.as_millis())
}

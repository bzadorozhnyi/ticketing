use std::env;
use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;

use seat_hold::core::db::DbPool;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{Connection, PgConnection, PgPool, Postgres, Transaction};
use tokio::net::TcpListener;
use tokio::sync::{Mutex, OnceCell, OwnedMappedMutexGuard, OwnedMutexGuard};

const TEST_DATABASE: &str = "testing_seat_hold_db";
const CONNECTION_WAIT: Duration = Duration::from_secs(1);

static MIGRATED: OnceCell<()> = OnceCell::const_new();

fn root_options() -> PgConnectOptions {
    let url = env::var("DATABASE_URL").expect("DATABASE_URL from .cargo/config.toml");
    PgConnectOptions::from_str(&url).expect("a valid DATABASE_URL")
}

pub fn test_options() -> PgConnectOptions {
    root_options().database(TEST_DATABASE)
}

/// Once per test binary, like the session fixture of the Python services.
async fn recreate_test_database() {
    let mut root = PgConnection::connect_with(&root_options())
        .await
        .expect("a running seat-hold-db");
    for statement in [
        format!("DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)"),
        format!("CREATE DATABASE {TEST_DATABASE}"),
    ] {
        sqlx::raw_sql(sqlx::AssertSqlSafe(statement))
            .execute(&mut root)
            .await
            .expect("a test database");
    }
    let mut conn = PgConnection::connect_with(&test_options())
        .await
        .expect("the test database");
    sqlx::migrate!()
        .run(&mut conn)
        .await
        .expect("migrations to apply");
}

/// One connection inside one transaction. Everything a test does, commits included,
/// disappears when the last clone is dropped, even if the test panics.
#[derive(Clone)]
pub struct TestPool(Arc<Mutex<Transaction<'static, Postgres>>>);

impl TestPool {
    pub async fn new() -> Self {
        MIGRATED.get_or_init(recreate_test_database).await;
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect_lazy_with(test_options());
        let transaction = pool.begin().await.expect("a test transaction");
        Self(Arc::new(Mutex::new(transaction)))
    }
}

impl DbPool for TestPool {
    type Conn = OwnedMappedMutexGuard<Transaction<'static, Postgres>, PgConnection>;

    /// A timeout instead of a hang: code that asks for a second connection while
    /// holding the first works with a real pool, but would wait here forever.
    async fn get_conn(&self) -> Result<Self::Conn, sqlx::Error> {
        let guard = tokio::time::timeout(CONNECTION_WAIT, self.0.clone().lock_owned())
            .await
            .map_err(|_| sqlx::Error::PoolTimedOut)?;
        Ok(OwnedMutexGuard::map(guard, |transaction| {
            &mut **transaction
        }))
    }
}

/// A pool whose database does not exist: the port is taken from the OS and released.
pub async fn dead_pool() -> PgPool {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let port = listener.local_addr().expect("a bound address").port();
    drop(listener);
    PgPoolOptions::new()
        .acquire_timeout(Duration::from_millis(200))
        .connect_lazy_with(PgConnectOptions::new().host("127.0.0.1").port(port))
}

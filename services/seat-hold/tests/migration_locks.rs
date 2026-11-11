//! A migration waiting for a lock steps aside instead of queueing every query behind it.
//! Real commits and several connections, and the real sqlx migrator.

use std::time::{Duration, Instant};

use seat_hold::core::db::with_migration_limits;
use seat_hold::core::migrations::{is_lock_timeout, run_with_lock_retries};
use seat_hold::core::settings::MigrationSettings;
use sqlx::migrate::Migrator;
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Transaction};
use uuid::Uuid;

fn fast() -> MigrationSettings {
    MigrationSettings {
        lock_timeout: Duration::from_millis(200),
        attempts: 4,
        retry_base_delay: Duration::from_millis(50),
        max_retry_delay: Duration::from_millis(100),
    }
}

/// One migration after the service's own; the ones already applied are not in its source.
async fn migrator_for(sql: &str) -> Migrator {
    let dir = std::env::temp_dir().join(format!("seat-hold-migration-{}", Uuid::now_v7()));
    std::fs::create_dir_all(&dir).expect("a temporary directory");
    std::fs::write(dir.join("99990101000000_probe.sql"), sql).expect("a migration file");
    let mut migrator = Migrator::new(dir.as_path()).await.expect("a migrator");
    migrator.set_ignore_missing(true);
    migrator
}

fn migration_pool(pool: &PgPool, settings: &MigrationSettings) -> PgPool {
    let options = with_migration_limits(pool.connect_options().as_ref().clone(), settings);
    PgPoolOptions::new()
        .max_connections(1)
        .connect_lazy_with(options)
}

/// A long report: an open transaction that has read the table holds ACCESS SHARE on it.
async fn long_reader(pool: &PgPool) -> Transaction<'static, Postgres> {
    let mut reader = pool.begin().await.expect("a transaction");
    sqlx::query("SELECT count(*) FROM holds")
        .execute(&mut *reader)
        .await
        .expect("the read");
    reader
}

#[sqlx::test]
async fn a_migration_behind_a_long_reader_steps_aside_and_tries_again(pool: PgPool) {
    let settings = fast();
    let migrator = migrator_for("ALTER TABLE holds ADD COLUMN probe text;").await;
    let migrations = migration_pool(&pool, &settings);
    let reader = long_reader(&pool).await;
    let report = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(500)).await;
        reader.rollback().await.unwrap();
    });
    let mut attempts = 0;

    let result = run_with_lock_retries(&settings, || {
        attempts += 1;
        migrator.run(&migrations)
    })
    .await;
    report.await.unwrap();

    assert!(result.is_ok(), "{result:?}");
    assert!(attempts >= 2, "{attempts}");
    let added: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'holds' AND column_name = 'probe'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(added, 1);
    migrations.close().await;
}

#[sqlx::test]
async fn a_lock_that_never_ends_fails_the_migration_after_its_attempts(pool: PgPool) {
    let settings = fast();
    let migrator = migrator_for("ALTER TABLE holds ADD COLUMN probe text;").await;
    let migrations = migration_pool(&pool, &settings);
    let reader = long_reader(&pool).await;
    let started = Instant::now();
    let mut attempts = 0;

    // Bounded: without the lock timeout the migration would wait for ever.
    let result = tokio::time::timeout(
        Duration::from_secs(10),
        run_with_lock_retries(&settings, || {
            attempts += 1;
            migrator.run(&migrations)
        }),
    )
    .await
    .expect("the migration gives up on its own");

    let error = result.expect_err("the lock never ends");
    assert!(is_lock_timeout(&error), "{error:?}");
    assert_eq!(attempts, settings.attempts);
    // Each attempt waits only the lock timeout, so the queue behind it never lasts longer.
    assert!(
        started.elapsed() < Duration::from_secs(3),
        "{:?}",
        started.elapsed()
    );
    reader.rollback().await.unwrap();
    migrations.close().await;
}

#[sqlx::test]
async fn other_errors_are_not_retried(pool: PgPool) {
    let settings = fast();
    let migrator = migrator_for("ALTER TABLE no_such_table ADD COLUMN probe text;").await;
    let migrations = migration_pool(&pool, &settings);
    let mut attempts = 0;

    let result = run_with_lock_retries(&settings, || {
        attempts += 1;
        migrator.run(&migrations)
    })
    .await;

    let error = result.expect_err("the table does not exist");
    assert!(!is_lock_timeout(&error), "{error:?}");
    assert_eq!(attempts, 1);
    migrations.close().await;
}

//! A lock held by someone else must cost a request, not the pool: real commits and several connections.

mod common;

use std::time::{Duration, Instant};

use axum::http::StatusCode;
use common::service::{client, spawn_service};
use seat_hold::core::db::with_session_limits;
use seat_hold::core::settings::PoolSettings;
use serde_json::{Value, json};
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Transaction};
use tokio::task::JoinSet;
use uuid::Uuid;

const POOL_SIZE: u32 = 3;
const REQUESTS: usize = 10;

fn limits(lock_timeout: Duration, statement_timeout: Duration) -> PoolSettings {
    PoolSettings {
        max_connections: POOL_SIZE,
        acquire_timeout: Duration::from_secs(5),
        lock_timeout,
        statement_timeout,
    }
}

/// A test closes the returned pool, otherwise dropping the test database waits for it.
async fn service_with(pool: &PgPool, settings: &PoolSettings) -> (String, PgPool) {
    let options = with_session_limits(pool.connect_options().as_ref().clone(), settings);
    let service_pool = PgPoolOptions::new()
        .max_connections(settings.max_connections)
        .acquire_timeout(settings.acquire_timeout)
        .connect_lazy_with(options);
    (spawn_service(service_pool.clone()).await, service_pool)
}

/// What a migration or a forgotten psql session does: reads go on, every write waits.
async fn lock_holds(pool: &PgPool) -> Transaction<'static, Postgres> {
    let mut locker = pool.begin().await.expect("a transaction");
    sqlx::query("LOCK TABLE holds IN EXCLUSIVE MODE")
        .execute(&mut *locker)
        .await
        .expect("the table locked");
    locker
}

async fn create_hold(service: String, owner: String) -> (StatusCode, Value) {
    let response = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", owner)
        .json(&json!({"event_id": Uuid::now_v7(), "seat_ids": [Uuid::now_v7()]}))
        .send()
        .await
        .expect("a service response");
    (
        response.status(),
        response.json().await.expect("a JSON body"),
    )
}

/// Without the limits these requests would wait as long as the lock is held, here for ever.
async fn create_holds_at_once(service: &str) -> Vec<(StatusCode, Value)> {
    let mut tasks = JoinSet::new();
    for index in 0..REQUESTS {
        tasks.spawn(create_hold(service.to_owned(), format!("user-{index}")));
    }
    tokio::time::timeout(Duration::from_secs(10), tasks.join_all())
        .await
        .expect("every request answered while the table is locked")
}

fn all_unavailable(answers: &[(StatusCode, Value)]) -> bool {
    answers.iter().all(|(status, body)| {
        *status == StatusCode::SERVICE_UNAVAILABLE && body["code"] == "service_unavailable"
    })
}

#[sqlx::test]
async fn a_locked_table_fails_writes_fast_and_leaves_the_pool_to_the_rest(pool: PgPool) {
    let settings = limits(Duration::from_millis(200), Duration::from_secs(5));
    let (service, service_pool) = service_with(&pool, &settings).await;
    let locker = lock_holds(&pool).await;

    let started = Instant::now();
    let answers = create_holds_at_once(&service).await;
    let elapsed = started.elapsed();
    let ready = client()
        .get(format!("{service}/ready"))
        .send()
        .await
        .unwrap();

    assert!(all_unavailable(&answers), "{answers:?}");
    // 10 requests over 3 connections, 200 ms each: about 4 rounds, far below the 5 s
    // a request would wait for a connection that never comes back.
    assert!(elapsed < Duration::from_secs(3), "took {elapsed:?}");
    assert_eq!(ready.status(), StatusCode::OK);

    locker.rollback().await.unwrap();
    let (status, _) = create_hold(service, "user-after".into()).await;
    assert_eq!(status, StatusCode::CREATED);
    service_pool.close().await;
}

#[sqlx::test]
async fn without_a_lock_timeout_the_statement_timeout_still_ends_the_wait(pool: PgPool) {
    let settings = limits(Duration::ZERO, Duration::from_millis(200));
    let (service, service_pool) = service_with(&pool, &settings).await;
    let locker = lock_holds(&pool).await;

    let answers = create_holds_at_once(&service).await;

    assert!(all_unavailable(&answers), "{answers:?}");
    locker.rollback().await.unwrap();
    service_pool.close().await;
}

#[sqlx::test]
async fn each_session_of_the_service_starts_with_both_timeouts(pool: PgPool) {
    let settings = limits(Duration::from_secs(1), Duration::from_secs(5));
    let (_, service_pool) = service_with(&pool, &settings).await;

    let (lock_timeout, statement_timeout): (String, String) = sqlx::query_as(
        "SELECT current_setting('lock_timeout'), current_setting('statement_timeout')",
    )
    .fetch_one(&service_pool)
    .await
    .unwrap();

    assert_eq!(
        (lock_timeout.as_str(), statement_timeout.as_str()),
        ("1s", "5s")
    );
    service_pool.close().await;
}

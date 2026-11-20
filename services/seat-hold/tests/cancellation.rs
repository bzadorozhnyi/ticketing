//! A hold whose client goes away while it waits inside its transaction: the gateway's
//! timeout does just that. Another connection keeps the request waiting at a chosen
//! point, the client gives up, and then the way is cleared. A request still running
//! would then take the seat for a client that is gone.

mod common;

use std::time::Duration;

use axum::http::StatusCode;
use common::service::{client, spawn_service};
use seat_hold::core::db::with_session_limits;
use seat_hold::core::settings::PoolSettings;
use serde_json::{Value, json};
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Transaction};
use uuid::Uuid;

const OWNER: &str = "user-1";
/// The client's patience, like the gateway's timeout: shorter than the wait.
const CLIENT_TIMEOUT: Duration = Duration::from_millis(200);
/// When the way is cleared: after the client left, before the service's own lock timeout.
const CLEARED_AFTER: Duration = Duration::from_millis(500);

async fn service_for(pool: &PgPool) -> (String, PgPool) {
    let settings = PoolSettings::default();
    let options = with_session_limits(pool.connect_options().as_ref().clone(), &settings);
    let service_pool = PgPoolOptions::new()
        .max_connections(settings.max_connections)
        .connect_lazy_with(options);
    (spawn_service(service_pool.clone()).await, service_pool)
}

/// The request waits for the user's own advisory lock, before it counts their seats.
async fn block_on_the_users_lock(pool: &PgPool, event_id: Uuid) -> Transaction<'static, Postgres> {
    let mut blocker = pool.begin().await.expect("the test setup");
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2::uuid::text, 0))")
        .bind(OWNER)
        .bind(event_id)
        .execute(&mut *blocker)
        .await
        .expect("the test setup");
    blocker
}

/// The request's insert waits for another user's uncommitted hold of the same seat.
async fn block_on_the_seat(
    pool: &PgPool,
    event_id: Uuid,
    seat_id: Uuid,
) -> Transaction<'static, Postgres> {
    let mut blocker = pool.begin().await.expect("the test setup");
    sqlx::query(
        "INSERT INTO holds (event_id, seat_id, hold_id, owner_id, expires_at)
         VALUES ($1, $2, gen_random_uuid(), 'user-2', now() + interval '5 minutes')",
    )
    .bind(event_id)
    .bind(seat_id)
    .execute(&mut *blocker)
    .await
    .expect("the test setup");
    blocker
}

async fn hold(
    service: &str,
    event_id: Uuid,
    seat_id: Uuid,
    timeout: Option<Duration>,
) -> Option<StatusCode> {
    hold_with_key(service, event_id, seat_id, timeout, None)
        .await
        .map(|(status, _)| status)
}

/// None when the client gave up before an answer.
async fn hold_with_key(
    service: &str,
    event_id: Uuid,
    seat_id: Uuid,
    timeout: Option<Duration>,
    key: Option<&str>,
) -> Option<(StatusCode, Value)> {
    let mut request = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", OWNER)
        .json(&json!({"event_id": event_id, "seat_ids": [seat_id]}));
    if let Some(key) = key {
        request = request.header("idempotency-key", key);
    }
    if let Some(timeout) = timeout {
        request = request.timeout(timeout);
    }
    let response = request.send().await.ok()?;
    let status = response.status();
    Some((status, response.json().await.ok()?))
}

async fn holds_of_owner(pool: &PgPool, event_id: Uuid) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM holds WHERE event_id = $1 AND owner_id = $2")
        .bind(event_id)
        .bind(OWNER)
        .fetch_one(pool)
        .await
        .expect("the test setup")
}

async fn waiting_on_a_lock(pool: &PgPool) -> i64 {
    sqlx::query_scalar(
        "SELECT count(*) FROM pg_stat_activity
         WHERE datname = current_database() AND wait_event_type = 'Lock'",
    )
    .fetch_one(pool)
    .await
    .expect("the test setup")
}

/// Sessions of the service left inside a transaction: a rollback that never ran.
async fn idle_in_transaction(pool: &PgPool) -> i64 {
    sqlx::query_scalar(
        "SELECT count(*) FROM pg_stat_activity
         WHERE datname = current_database() AND state LIKE 'idle in transaction%'
           AND pid <> pg_backend_pid()",
    )
    .fetch_one(pool)
    .await
    .expect("the test setup")
}

/// Runs one case: the client gives up (or waits, with `timeout` None), the blocker is
/// rolled back after `CLEARED_AFTER`, and the answer and the holds are returned.
async fn run(
    pool: &PgPool,
    service: &str,
    blocker: Transaction<'static, Postgres>,
    event_id: Uuid,
    seat_id: Uuid,
    timeout: Option<Duration>,
) -> (Option<StatusCode>, i64) {
    let observer = pool.clone();
    let clear = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(100)).await;
        // The request reached the point it was meant to wait at; once the client is gone,
        // Postgres still waits there too: dropping the future does not cancel the query.
        assert_eq!(waiting_on_a_lock(&observer).await, 1, "the request waits");
        tokio::time::sleep(CLEARED_AFTER.saturating_sub(Duration::from_millis(100))).await;
        blocker.rollback().await.expect("the test setup");
    });
    let answer = hold(service, event_id, seat_id, timeout).await;
    clear.await.expect("the test setup");
    // Time for a request that is still running to finish after the way was cleared.
    tokio::time::sleep(Duration::from_millis(500)).await;
    (answer, holds_of_owner(pool, event_id).await)
}

#[sqlx::test]
async fn a_client_that_waits_behind_the_users_lock_gets_the_seat(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let blocker = block_on_the_users_lock(&pool, event_id).await;

    let (answer, holds) = run(&pool, &service, blocker, event_id, seat_id, None).await;

    assert_eq!(answer, Some(StatusCode::CREATED));
    assert_eq!(holds, 1);
    service_pool.close().await;
}

#[sqlx::test]
async fn a_client_gone_while_waiting_behind_the_users_lock_leaves_no_hold(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let blocker = block_on_the_users_lock(&pool, event_id).await;

    let (answer, holds) = run(
        &pool,
        &service,
        blocker,
        event_id,
        seat_id,
        Some(CLIENT_TIMEOUT),
    )
    .await;

    assert_eq!(answer, None, "the client gave up");
    assert_eq!(holds, 0, "a hold for a client that is gone");
    assert_eq!(idle_in_transaction(&pool).await, 0);
    service_pool.close().await;
}

#[sqlx::test]
async fn a_client_that_waits_for_the_seat_gets_it(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let blocker = block_on_the_seat(&pool, event_id, seat_id).await;

    let (answer, holds) = run(&pool, &service, blocker, event_id, seat_id, None).await;

    assert_eq!(answer, Some(StatusCode::CREATED));
    assert_eq!(holds, 1);
    service_pool.close().await;
}

#[sqlx::test]
async fn a_client_gone_while_waiting_for_the_seat_leaves_no_hold(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let blocker = block_on_the_seat(&pool, event_id, seat_id).await;

    let (answer, holds) = run(
        &pool,
        &service,
        blocker,
        event_id,
        seat_id,
        Some(CLIENT_TIMEOUT),
    )
    .await;

    assert_eq!(answer, None, "the client gave up");
    assert_eq!(holds, 0, "a hold for a client that is gone");
    assert_eq!(idle_in_transaction(&pool).await, 0);
    service_pool.close().await;
}

/// Only in the test database: every commit that inserts a hold takes 0.5 s, so the
/// client can give up while `COMMIT` runs.
async fn slow_commits(pool: &PgPool) {
    sqlx::query(
        "CREATE FUNCTION slow_commit() RETURNS trigger LANGUAGE plpgsql AS
         $$ BEGIN PERFORM pg_sleep(0.5); RETURN NULL; END $$",
    )
    .execute(pool)
    .await
    .expect("the test setup");
    sqlx::query(
        "CREATE CONSTRAINT TRIGGER slow_commit AFTER INSERT ON holds
         DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION slow_commit()",
    )
    .execute(pool)
    .await
    .expect("the test setup");
}

#[sqlx::test]
async fn a_client_gone_during_the_commit_leaves_a_hold_it_never_heard_of(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    slow_commits(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());

    let answer = hold(&service, event_id, seat_id, Some(CLIENT_TIMEOUT)).await;
    tokio::time::sleep(Duration::from_millis(800)).await;

    assert_eq!(answer, None, "the client gave up");
    assert_eq!(
        holds_of_owner(&pool, event_id).await,
        1,
        "the seat is taken"
    );
    service_pool.close().await;
}

#[sqlx::test]
async fn a_repeat_with_the_same_key_finds_the_hold_a_gone_client_never_heard_of(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    slow_commits(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let key = "buy-1";

    let gone = hold_with_key(&service, event_id, seat_id, Some(CLIENT_TIMEOUT), Some(key)).await;
    tokio::time::sleep(Duration::from_millis(800)).await;
    let repeat = hold_with_key(&service, event_id, seat_id, None, Some(key)).await;

    assert!(gone.is_none(), "the client gave up");
    let (status, body) = repeat.expect("an answer");
    assert_eq!(status, StatusCode::CREATED);
    let stored: Uuid = sqlx::query_scalar("SELECT hold_id FROM holds WHERE event_id = $1")
        .bind(event_id)
        .fetch_one(&pool)
        .await
        .expect("the test setup");
    assert_eq!(
        body["hold_id"],
        json!(stored),
        "the same hold, not a new one"
    );
    service_pool.close().await;
}

mod common;

use std::time::Duration;

use common::db::TestPool;
use common::factories::hold_factory;
use seat_hold::core::db::DbPool;
use seat_hold::holds::cleanup_job;
use seat_hold::holds::dto::hold::NewHold;
use seat_hold::holds::repositories::hold::HoldRepository;
use seat_hold::holds::use_cases::cleanup_expired_holds::CleanupExpiredHoldsUseCase;
use sqlx::{Acquire, PgPool};
use uuid::Uuid;

async fn count_event(pool: &impl DbPool, event_id: Uuid) -> i64 {
    let mut conn = pool.get_conn().await.expect("a connection");
    sqlx::query_scalar("SELECT count(*) FROM holds WHERE event_id = $1")
        .bind(event_id)
        .fetch_one(&mut *conn)
        .await
        .expect("a count")
}

async fn expired_hold(pool: &PgPool, event_id: Uuid, seat_id: Uuid) {
    sqlx::query(
        "INSERT INTO holds (event_id, seat_id, hold_id, owner_id, expires_at)
         VALUES ($1, $2, $3, 'old', now() - interval '1 second')",
    )
    .bind(event_id)
    .bind(seat_id)
    .bind(Uuid::now_v7())
    .execute(pool)
    .await
    .expect("an expired hold");
}

fn new_hold(event_id: Uuid, seat_id: Uuid) -> NewHold {
    NewHold {
        event_id,
        seat_ids: vec![seat_id],
        owner_id: "new".to_owned(),
    }
}

#[tokio::test]
async fn removes_expired_holds_and_keeps_active_ones() {
    let pool = TestPool::new().await;
    let event_id = Uuid::now_v7();
    hold_factory()
        .pool(&pool)
        .event_id(event_id)
        .expires_in_seconds(-1.0)
        .call()
        .await;
    hold_factory().pool(&pool).event_id(event_id).call().await;

    let deleted = CleanupExpiredHoldsUseCase::new(pool.clone(), 100)
        .execute()
        .await
        .unwrap();

    assert_eq!(deleted, 1);
    assert_eq!(count_event(&pool, event_id).await, 1);
}

#[tokio::test]
async fn removes_more_rows_than_one_batch() {
    let pool = TestPool::new().await;
    let event_id = Uuid::now_v7();
    for _ in 0..5 {
        hold_factory()
            .pool(&pool)
            .event_id(event_id)
            .expires_in_seconds(-1.0)
            .call()
            .await;
    }

    let deleted = CleanupExpiredHoldsUseCase::new(pool.clone(), 2)
        .execute()
        .await
        .unwrap();

    assert_eq!(deleted, 5);
    assert_eq!(count_event(&pool, event_id).await, 0);
}

#[tokio::test]
async fn the_job_keeps_cleaning_on_a_schedule() {
    let pool = TestPool::new().await;
    let event_id = Uuid::now_v7();
    let use_case = CleanupExpiredHoldsUseCase::new(pool.clone(), 100);
    let job = tokio::spawn(cleanup_job::run(use_case, Duration::from_millis(20)));

    tokio::time::sleep(Duration::from_millis(60)).await;
    hold_factory()
        .pool(&pool)
        .event_id(event_id)
        .expires_in_seconds(-1.0)
        .call()
        .await;
    tokio::time::sleep(Duration::from_millis(100)).await;
    job.abort();

    assert_eq!(count_event(&pool, event_id).await, 0);
}

/// Two real connections: a rolled-back test transaction would hide this race.
#[sqlx::test]
async fn cleanup_skips_a_row_that_is_being_taken_over(pool: PgPool) {
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    expired_hold(&pool, event_id, seat_id).await;

    let mut taker = pool.begin().await.unwrap();
    let taken = HoldRepository::take_seats(
        taker.acquire().await.unwrap(),
        Uuid::now_v7(),
        &new_hold(event_id, seat_id),
        Duration::from_mins(5),
    )
    .await
    .unwrap();
    assert_eq!(taken.len(), 1);

    let mut cleaner = pool.acquire().await.unwrap();
    let cleanup = HoldRepository::delete_expired(&mut cleaner, 100);
    let deleted = tokio::time::timeout(Duration::from_secs(5), cleanup)
        .await
        .expect("cleanup must not wait for a locked row")
        .unwrap();
    taker.commit().await.unwrap();

    assert_eq!(deleted, 0);
    assert_eq!(count_event(&pool, event_id).await, 1);
}

#[sqlx::test]
async fn a_seat_can_be_taken_while_cleanup_is_deleting_it(pool: PgPool) {
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    expired_hold(&pool, event_id, seat_id).await;

    let mut cleaner = pool.begin().await.unwrap();
    let deleted = HoldRepository::delete_expired(cleaner.acquire().await.unwrap(), 100)
        .await
        .unwrap();
    assert_eq!(deleted, 1);

    let taker_pool = pool.clone();
    let taker = tokio::spawn(async move {
        let mut conn = taker_pool.acquire().await.unwrap();
        HoldRepository::take_seats(
            &mut conn,
            Uuid::now_v7(),
            &new_hold(event_id, seat_id),
            Duration::from_mins(5),
        )
        .await
        .unwrap()
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    cleaner.commit().await.unwrap();

    let taken = taker.await.unwrap();
    assert_eq!(taken.len(), 1);
    assert_eq!(count_event(&pool, event_id).await, 1);
}

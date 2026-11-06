//! Real commits and many connections: a rolled-back test transaction cannot show these races.

mod common;

use std::collections::HashMap;
use std::time::Duration;

use axum::http::StatusCode;
use common::service::{client, spawn_service};
use seat_hold::holds::dto::hold::NewHold;
use seat_hold::holds::repositories::hold::HoldRepository;
use serde_json::{Value, json};
use sqlx::PgPool;
use sqlx::postgres::PgPoolOptions;
use tokio::task::JoinSet;
use uuid::Uuid;

/// Below the listen backlog on macOS (`kern.ipc.somaxconn` = 128): more connections at once
/// get `Connection reset by peer` before they reach the service.
const CLIENTS: usize = 100;

struct Attempt {
    owner: String,
    seat_ids: Vec<Uuid>,
    status: StatusCode,
    body: Value,
}

/// `#[sqlx::test]` gives a small pool; the service needs as many connections as in production.
/// A test closes the returned pool, otherwise dropping the test database waits for it.
async fn service_for(pool: &PgPool) -> (String, PgPool) {
    let options = pool.connect_options().as_ref().clone();
    let service_pool = PgPoolOptions::new()
        .max_connections(10)
        .connect_lazy_with(options);
    (spawn_service(service_pool.clone()).await, service_pool)
}

async fn attempt(service: String, event_id: Uuid, owner: String, seat_ids: Vec<Uuid>) -> Attempt {
    let response = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", &owner)
        .json(&json!({"event_id": event_id, "seat_ids": seat_ids}))
        .send()
        .await
        .expect("a service response");
    let status = response.status();
    let body = response.json().await.expect("a JSON body");
    Attempt {
        owner,
        seat_ids,
        status,
        body,
    }
}

async fn run_all(service: &str, event_id: Uuid, requests: Vec<Vec<Uuid>>) -> Vec<Attempt> {
    let mut tasks = JoinSet::new();
    for (index, seat_ids) in requests.into_iter().enumerate() {
        tasks.spawn(attempt(
            service.to_owned(),
            event_id,
            format!("user-{index}"),
            seat_ids,
        ));
    }
    tasks.join_all().await
}

fn count_status(attempts: &[Attempt], status: StatusCode) -> usize {
    attempts.iter().filter(|a| a.status == status).count()
}

async fn stored(pool: &PgPool, event_id: Uuid) -> HashMap<Uuid, String> {
    let rows: Vec<(Uuid, String)> =
        sqlx::query_as("SELECT seat_id, owner_id FROM holds WHERE event_id = $1")
            .bind(event_id)
            .fetch_all(pool)
            .await
            .expect("stored holds");
    rows.into_iter().collect()
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

fn assert_exactly_one_winner(attempts: &[Attempt], holds: &HashMap<Uuid, String>, seat_id: Uuid) {
    assert_eq!(count_status(attempts, StatusCode::CREATED), 1);
    assert_eq!(count_status(attempts, StatusCode::CONFLICT), CLIENTS - 1);
    let winner = attempts
        .iter()
        .find(|a| a.status == StatusCode::CREATED)
        .expect("a winner");
    assert_eq!(holds.len(), 1);
    assert_eq!(holds[&seat_id], winner.owner);
}

#[sqlx::test]
async fn one_seat_has_exactly_one_winner(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());

    let attempts = run_all(&service, event_id, vec![vec![seat_id]; CLIENTS]).await;

    service_pool.close().await;

    let holds = stored(&pool, event_id).await;
    assert_exactly_one_winner(&attempts, &holds, seat_id);
}

#[sqlx::test]
async fn an_expired_hold_has_exactly_one_new_owner(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    expired_hold(&pool, event_id, seat_id).await;

    let attempts = run_all(&service, event_id, vec![vec![seat_id]; CLIENTS]).await;

    service_pool.close().await;

    let holds = stored(&pool, event_id).await;
    assert_exactly_one_winner(&attempts, &holds, seat_id);
}

/// Requests overlap and half of them list the seats in reverse order. A deadlock would
/// surface as a 500, a broken all-or-nothing as a winner missing a seat.
#[sqlx::test]
async fn overlapping_requests_never_split_or_deadlock(pool: PgPool) {
    let (service, service_pool) = service_for(&pool).await;
    let event_id = Uuid::now_v7();
    let seats: Vec<Uuid> = (0..20).map(|_| Uuid::now_v7()).collect();
    let requests = (0..CLIENTS)
        .map(|index| {
            let start = index % (seats.len() - 2);
            let mut window = seats[start..start + 3].to_vec();
            if index % 2 == 0 {
                window.reverse();
            }
            window
        })
        .collect();

    let attempts = run_all(&service, event_id, requests).await;

    service_pool.close().await;

    let created = count_status(&attempts, StatusCode::CREATED);
    assert!(created > 0);
    assert_eq!(
        created + count_status(&attempts, StatusCode::CONFLICT),
        CLIENTS
    );

    let holds = stored(&pool, event_id).await;
    let winners = attempts.iter().filter(|a| a.status == StatusCode::CREATED);
    let mut won_seats = 0;
    for winner in winners {
        assert_eq!(winner.body["seat_ids"].as_array().unwrap().len(), 3);
        for seat_id in &winner.seat_ids {
            assert_eq!(holds[seat_id], winner.owner);
            won_seats += 1;
        }
    }
    assert_eq!(holds.len(), won_seats);
}

/// Two real connections, in a fixed order. The hold expires while the confirm is still
/// open, so the new hold has to wait for it and must then see a confirmed seat.
#[sqlx::test]
async fn a_takeover_waits_for_a_confirm_in_progress(pool: PgPool) {
    let (event_id, seat_id, hold_id) = (Uuid::now_v7(), Uuid::now_v7(), Uuid::now_v7());
    sqlx::query(
        "INSERT INTO holds (event_id, seat_id, hold_id, owner_id, expires_at)
         VALUES ($1, $2, $3, 'owner', now() + interval '200 milliseconds')",
    )
    .bind(event_id)
    .bind(seat_id)
    .bind(hold_id)
    .execute(&pool)
    .await
    .unwrap();

    let mut confirm = pool.begin().await.unwrap();
    let confirmed = HoldRepository::confirm(&mut confirm, hold_id, "owner")
        .await
        .unwrap();
    assert_eq!(confirmed, 1);
    tokio::time::sleep(Duration::from_millis(300)).await;

    let taker_pool = pool.clone();
    let taker = tokio::spawn(async move {
        let mut conn = taker_pool.acquire().await.unwrap();
        let hold = NewHold {
            event_id,
            seat_ids: vec![seat_id],
            owner_id: "thief".to_owned(),
        };
        HoldRepository::take_seats(&mut conn, Uuid::now_v7(), &hold, Duration::from_mins(5))
            .await
            .unwrap()
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(
        !taker.is_finished(),
        "the takeover must wait for the row lock"
    );
    confirm.commit().await.unwrap();

    assert!(taker.await.unwrap().is_empty());
    assert_eq!(stored(&pool, event_id).await[&seat_id], "owner");
}

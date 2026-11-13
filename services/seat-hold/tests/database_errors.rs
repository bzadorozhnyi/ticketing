mod common;

use axum::http::StatusCode;
use axum::response::IntoResponse;
use common::db::TestPool;
use seat_hold::core::db::DbPool;
use seat_hold::core::errors::ServiceError;

/// The status a request gets for a real Postgres error with this SQLSTATE.
async fn status_for(sqlstate: &str) -> StatusCode {
    let pool = TestPool::new().await;
    let mut conn = pool.get_conn().await.expect("a test connection");
    let raise = format!("DO $$ BEGIN RAISE EXCEPTION 'test' USING ERRCODE = '{sqlstate}'; END $$");
    let error = sqlx::query(sqlx::AssertSqlSafe(raise))
        .execute(&mut *conn)
        .await
        .expect_err("the raised error");
    ServiceError::from(error).into_response().status()
}

#[tokio::test]
async fn errors_worth_a_retry_answer_503() {
    for sqlstate in [
        "40P01", "40001", "57P01", "57P02", "57P03", "53300", "08006",
    ] {
        assert_eq!(
            status_for(sqlstate).await,
            StatusCode::SERVICE_UNAVAILABLE,
            "{sqlstate}"
        );
    }
}

#[tokio::test]
async fn other_errors_stay_500() {
    assert_eq!(status_for("23505").await, StatusCode::INTERNAL_SERVER_ERROR);
}

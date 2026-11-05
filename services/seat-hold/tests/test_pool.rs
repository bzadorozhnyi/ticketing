mod common;

use common::db::{TestPool, test_options};
use seat_hold::db::DbPool;
use sqlx::{Connection, PgConnection};

async fn table_is_visible_outside() -> bool {
    let mut conn = PgConnection::connect_with(&test_options())
        .await
        .expect("the test database");
    sqlx::query_scalar::<_, bool>("SELECT to_regclass('public.committed_probe') IS NOT NULL")
        .fetch_one(&mut conn)
        .await
        .expect("a boolean")
}

async fn count(conn: &mut PgConnection) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM inner_probe")
        .fetch_one(conn)
        .await
        .expect("a count")
}

#[tokio::test]
async fn a_commit_inside_a_test_is_not_visible_to_other_connections() {
    let pool = TestPool::new().await;
    let mut conn = pool.get_conn().await.unwrap();

    let mut transaction = conn.begin().await.unwrap();
    sqlx::query("CREATE TABLE committed_probe (id INT)")
        .execute(&mut *transaction)
        .await
        .unwrap();
    transaction.commit().await.unwrap();

    assert!(!table_is_visible_outside().await);
}

#[tokio::test]
async fn a_rollback_inside_a_test_undoes_only_the_inner_transaction() {
    let pool = TestPool::new().await;
    let mut conn = pool.get_conn().await.unwrap();
    sqlx::query("CREATE TABLE inner_probe (id INT)")
        .execute(&mut *conn)
        .await
        .unwrap();
    sqlx::query("INSERT INTO inner_probe VALUES (1)")
        .execute(&mut *conn)
        .await
        .unwrap();

    let mut transaction = conn.begin().await.unwrap();
    sqlx::query("INSERT INTO inner_probe VALUES (2)")
        .execute(&mut *transaction)
        .await
        .unwrap();
    transaction.rollback().await.unwrap();

    assert_eq!(count(&mut conn).await, 1);
}

#[tokio::test]
async fn asking_for_a_second_connection_fails_instead_of_hanging() {
    let pool = TestPool::new().await;
    let _held = pool.get_conn().await.unwrap();

    let second = pool.get_conn().await;

    assert!(matches!(second, Err(sqlx::Error::PoolTimedOut)));
}

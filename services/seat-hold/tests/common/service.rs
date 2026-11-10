use reqwest::Client;
use seat_hold::app::build_app;
use seat_hold::core::db::DbPool;
use seat_hold::core::settings::HoldSettings;
use tokio::net::TcpListener;

use super::telemetry;

pub async fn spawn_service<D: DbPool>(db: D) -> String {
    spawn_service_with(db, HoldSettings::default()).await
}

pub async fn spawn_service_with<D: DbPool>(db: D, holds: HoldSettings) -> String {
    telemetry::spans();
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let addr = listener.local_addr().expect("a bound address");
    tokio::spawn(async move { axum::serve(listener, build_app(db, holds)).await });
    format!("http://{addr}")
}

pub fn client() -> Client {
    Client::builder()
        .no_proxy()
        .build()
        .expect("a valid test client")
}

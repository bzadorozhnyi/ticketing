use reqwest::Client;
use seat_hold::app::build_app;
use seat_hold::db::DbPool;
use tokio::net::TcpListener;

pub async fn spawn_service<D: DbPool>(db: D) -> String {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let addr = listener.local_addr().expect("a bound address");
    tokio::spawn(async move { axum::serve(listener, build_app(db)).await });
    format!("http://{addr}")
}

pub fn client() -> Client {
    Client::builder()
        .no_proxy()
        .build()
        .expect("a valid test client")
}

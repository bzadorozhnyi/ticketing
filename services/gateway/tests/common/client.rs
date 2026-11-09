use std::time::Duration;

use axum::http::header::{AUTHORIZATION, HeaderValue};
use axum::http::{HeaderMap, StatusCode};
use gateway::auth::issue_token;
use reqwest::redirect::Policy;
use reqwest::{Client, ClientBuilder};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

use super::app::JWT_SECRET;

pub fn authorization() -> String {
    let token = issue_token(JWT_SECRET, "user-1", Duration::from_secs(60));
    format!("Bearer {}", token.expect("a signed token"))
}

fn client_builder() -> ClientBuilder {
    Client::builder().no_proxy().redirect(Policy::none())
}

pub fn anonymous_client() -> Client {
    client_builder().build().expect("a valid test client")
}

pub fn client() -> Client {
    let mut value = HeaderValue::try_from(authorization()).expect("an ASCII token");
    value.set_sensitive(true);
    client_builder()
        .default_headers(HeaderMap::from_iter([(AUTHORIZATION, value)]))
        .build()
        .expect("a valid test client")
}

/// Sends bytes as they are: an HTTP client would normalize the path or set its own framing.
pub async fn send_raw(gateway_url: &str, request: &str) -> StatusCode {
    let addr = gateway_url.trim_start_matches("http://");
    let mut stream = TcpStream::connect(addr).await.expect("a running gateway");
    stream
        .write_all(request.as_bytes())
        .await
        .expect("a writable socket");

    let mut head = [0; 12];
    stream
        .read_exact(&mut head)
        .await
        .expect("a response status line");
    let status = std::str::from_utf8(&head[9..12]).expect("an ASCII status code");
    status.parse().expect("a valid status code")
}

/// The status of an authorized `GET /api/v1/events`.
pub async fn get(gateway: &str) -> StatusCode {
    client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .expect("a response from the gateway")
        .status()
}

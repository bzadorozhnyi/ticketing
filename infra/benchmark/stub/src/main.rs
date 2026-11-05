//! A fast upstream for the gateway benchmark: its cost must stay out of the numbers.

use std::env;
use std::error::Error;
use std::time::Duration;

use axum::Router;
use axum::http::HeaderMap;
use axum::http::header::CONTENT_TYPE;
use axum::response::IntoResponse;
use axum::routing::{any, get};
use tokio::net::TcpListener;

const BODY_BYTES: usize = 795;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let delay_ms: u64 = env::var("DELAY_MS").map_or(Ok(0), |value| value.parse())?;
    let delay = Duration::from_millis(delay_ms);
    let body = format!("{{\"items\":\"{}\"}}", "x".repeat(BODY_BYTES - 12));

    let events = any(move || {
        let body = body.clone();
        async move {
            if !delay.is_zero() {
                tokio::time::sleep(delay).await;
            }
            ([(CONTENT_TYPE, "application/json")], body)
        }
    });
    let app = Router::new()
        .route("/health", get(async || "ok"))
        .route("/api/v1/events/echo", any(echo))
        .route("/api/v1/events", events);

    let listener = TcpListener::bind(("0.0.0.0", 8000)).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn echo(headers: HeaderMap) -> impl IntoResponse {
    let mut lines = Vec::new();
    for (name, value) in &headers {
        lines.push(format!("{name}: {}", value.to_str().unwrap_or("?")));
    }
    lines.sort();
    lines.join("\n")
}

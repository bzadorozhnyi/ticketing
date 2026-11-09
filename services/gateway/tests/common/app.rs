use std::net::SocketAddr;
use std::time::Duration;

use axum::Router;
use gateway::app::build_app;
use gateway::server::serve::serve as serve_gateway;
use gateway::settings::{
    AuthSettings, BreakerSettings, BulkheadSettings, ClientSettings, LimitSettings, LogFormat,
    LoggingSettings, RateLimitSettings, RetrySettings, Settings, UpstreamSettings,
};
use tokio::net::TcpListener;
use tracing::Level;

use super::upstream::{Upstream, spawn_upstream};

pub const MAX_BODY_BYTES: usize = 64;
pub const JWT_SECRET: &str = "test-secret-with-at-least-32-bytes";

pub async fn serve(app: Router) -> SocketAddr {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let addr = listener.local_addr().expect("a bound address");
    tokio::spawn(async move { axum::serve(listener, app).await });
    addr
}

pub fn test_settings(catalog_url: &str) -> Settings {
    Settings {
        port: 0,
        limits: LimitSettings {
            max_body_bytes: MAX_BODY_BYTES,
            max_in_flight: 1000,
            max_connections: 1000,
            header_timeout: Duration::from_secs(30),
        },
        auth: AuthSettings {
            jwt_secret: JWT_SECRET.to_owned(),
        },
        upstreams: UpstreamSettings {
            catalog_url: catalog_url.parse().expect("a valid upstream url"),
            seat_hold_url: catalog_url.parse().expect("a valid upstream url"),
            booking_url: catalog_url.parse().expect("a valid upstream url"),
            catalog_timeout: None,
            seat_hold_timeout: None,
            booking_timeout: None,
            catalog_max_concurrent: None,
            seat_hold_max_concurrent: None,
            booking_max_concurrent: None,
        },
        retry: RetrySettings {
            max_attempts: 3,
            base_delay: Duration::from_millis(10),
            budget_ratio: 0.2,
            budget_capacity: 10.0,
        },
        // Off unless a test turns it on: more calls are needed than the window holds.
        breaker: BreakerSettings {
            window: 20,
            min_calls: usize::MAX,
            failure_percent: 50,
            open_duration: Duration::from_secs(5),
        },
        bulkhead: BulkheadSettings {
            max_concurrent: 1000,
        },
        rate_limit: RateLimitSettings::default(),
        client: ClientSettings {
            connect_timeout: Duration::from_secs(1),
            request_timeout: Duration::from_secs(2),
            pool_idle_timeout: Duration::from_secs(3),
        },
        logging: LoggingSettings {
            format: LogFormat::Json,
            level: Level::INFO,
        },
    }
}

pub async fn spawn_gateway(settings: &Settings) -> String {
    let app = build_app(settings).expect("a valid gateway client");
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let addr = listener.local_addr().expect("a bound address");
    let limits = settings.limits.clone();
    tokio::spawn(async move {
        serve_gateway(listener, app, &limits, std::future::pending()).await;
    });
    format!("http://{addr}")
}

/// A gateway in front of `routes`, with settings changed by `adjust`.
pub async fn gateway_for(routes: Router, adjust: impl FnOnce(&mut Settings)) -> (String, Upstream) {
    let upstream = spawn_upstream(routes).await;
    let mut settings = test_settings(&upstream.url);
    adjust(&mut settings);
    (spawn_gateway(&settings).await, upstream)
}

use std::net::SocketAddr;
use std::time::Duration;

use axum::Router;
use gateway::app::build_app;
use gateway::settings::{
    AuthSettings, ClientSettings, LimitSettings, LogFormat, LoggingSettings, Settings,
    UpstreamSettings,
};
use tokio::net::TcpListener;
use tracing::Level;

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
        },
        auth: AuthSettings {
            jwt_secret: JWT_SECRET.to_owned(),
        },
        upstreams: UpstreamSettings {
            catalog_url: catalog_url.parse().expect("a valid upstream url"),
            seat_hold_url: catalog_url.parse().expect("a valid upstream url"),
        },
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
    format!("http://{}", serve(app).await)
}

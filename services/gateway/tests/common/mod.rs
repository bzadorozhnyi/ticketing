// Every test binary compiles this module and uses only a part of it.
#![allow(dead_code)]

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Request, State};
use axum::http::header::{AUTHORIZATION, HeaderValue};
use axum::http::{HeaderMap, Method, StatusCode};
use axum::middleware::{Next, from_fn_with_state};
use axum::response::Response;
use gateway::app::build_app;
use gateway::auth::issue_token;
use gateway::settings::{
    AuthSettings, ClientSettings, LimitSettings, LogFormat, LoggingSettings, Settings,
    UpstreamSettings,
};
use reqwest::redirect::Policy;
use reqwest::{Client, ClientBuilder};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tracing::Level;

pub const MAX_BODY_BYTES: usize = 64;
pub const JWT_SECRET: &str = "test-secret-with-at-least-32-bytes";

#[derive(Clone, Debug)]
pub struct Received {
    pub method: Method,
    pub uri: String,
    pub headers: HeaderMap,
    pub body: Bytes,
}

type Log = Arc<Mutex<Vec<Received>>>;

pub struct Upstream {
    pub url: String,
    log: Log,
}

impl Upstream {
    pub fn received(&self) -> Vec<Received> {
        self.log.lock().expect("log lock is poisoned").clone()
    }
}

async fn record(State(log): State<Log>, request: Request, next: Next) -> Response {
    let (parts, body) = request.into_parts();
    let body = axum::body::to_bytes(body, usize::MAX)
        .await
        .expect("the gateway sent a readable body");
    log.lock().expect("log lock is poisoned").push(Received {
        method: parts.method.clone(),
        uri: parts.uri.to_string(),
        headers: parts.headers.clone(),
        body: body.clone(),
    });
    next.run(Request::from_parts(parts, body.into())).await
}

pub async fn serve(app: Router) -> SocketAddr {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let addr = listener.local_addr().expect("a bound address");
    tokio::spawn(async move { axum::serve(listener, app).await });
    addr
}

/// A real HTTP server on a real socket; only its routes are written for the test.
pub async fn spawn_upstream(routes: Router) -> Upstream {
    let log = Log::default();
    let app = routes.layer(from_fn_with_state(log.clone(), record));
    let addr = serve(app).await;
    Upstream {
        url: format!("http://{addr}"),
        log,
    }
}

/// An address nothing listens on: the port is taken from the OS and released.
pub async fn dead_upstream_url() -> String {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let addr = listener.local_addr().expect("a bound address");
    drop(listener);
    format!("http://{addr}")
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

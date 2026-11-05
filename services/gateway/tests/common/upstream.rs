use std::sync::{Arc, Mutex};

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Request, State};
use axum::http::{HeaderMap, Method};
use axum::middleware::{Next, from_fn_with_state};
use axum::response::Response;
use tokio::net::TcpListener;

use super::app::serve;

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

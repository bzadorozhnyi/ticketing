use std::future::Future;
use std::io;
use std::pin::pin;
use std::time::Duration;

use axum::Router;
use hyper::server::conn::http1;
use hyper_util::rt::{TokioIo, TokioTimer};
use hyper_util::server::graceful::GracefulShutdown;
use hyper_util::service::TowerToHyperService;
use tokio::net::TcpListener;

use crate::limits::concurrency::ConcurrencyLimit;
use crate::settings::LimitSettings;
use crate::telemetry::metrics::metrics;

/// Serves `app` until `shutdown` completes, then waits for the requests in flight.
///
/// `axum::serve` has neither of the two limits here. A connection over
/// `max_connections` is closed at once, so a flood of connections cannot use up the
/// process's open files and stop it from accepting anyone. A client gets
/// `header_timeout` to send the headers of each request, counted from the moment the
/// gateway waits for them: a client that sends nothing, or a byte at a time, is
/// closed, and so is a keep-alive connection idle for that long.
pub async fn serve(
    listener: TcpListener,
    app: Router,
    limits: &LimitSettings,
    shutdown: impl Future<Output = ()>,
) {
    let connections = ConcurrencyLimit::new(limits.max_connections);
    metrics().observe_in_use(
        "gateway.connections.open",
        vec![(connections.clone(), vec![])],
    );
    let mut http = http1::Builder::new();
    http.timer(TokioTimer::new())
        .header_read_timeout(limits.header_timeout);
    let graceful = GracefulShutdown::new();
    let mut shutdown = pin!(shutdown);
    loop {
        let stream = tokio::select! {
            accepted = listener.accept() => match accepted {
                Ok((stream, _)) => stream,
                Err(error) => {
                    accept_failed(&error).await;
                    continue;
                }
            },
            () = &mut shutdown => break,
        };
        let Some(place) = connections.try_enter() else {
            metrics().refused_connections.add(1, &[]);
            continue;
        };
        let connection = graceful.watch(
            http.serve_connection(TokioIo::new(stream), TowerToHyperService::new(app.clone())),
        );
        tokio::spawn(async move {
            let _place = place;
            if let Err(error) = connection.await {
                tracing::debug!(error = %error, "Connection ended with an error");
            }
        });
    }
    drop(listener);
    graceful.shutdown().await;
}

/// The same rule as `axum::serve`: a client that went away is nothing, anything else,
/// such as running out of open files, is logged and waited out for a second.
async fn accept_failed(error: &io::Error) {
    if matches!(
        error.kind(),
        io::ErrorKind::ConnectionRefused
            | io::ErrorKind::ConnectionAborted
            | io::ErrorKind::ConnectionReset
    ) {
        return;
    }
    tracing::error!(error = %error, "Failed to accept a connection");
    tokio::time::sleep(Duration::from_secs(1)).await;
}

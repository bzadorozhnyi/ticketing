use tokio::signal::unix::Signal;

pub async fn shutdown_signal(mut terminate: Signal) {
    tokio::select! {
        _ = terminate.recv() => tracing::info!("SIGTERM received, draining connections"),
        _ = tokio::signal::ctrl_c() => tracing::info!("SIGINT received, draining connections"),
    }
}

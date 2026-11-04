use std::error::Error;

use gateway::app::build_app;
use gateway::logging;
use gateway::settings::Settings;
use gateway::shutdown::shutdown_signal;
use tokio::net::TcpListener;
use tokio::signal::unix::{SignalKind, signal};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let settings = Settings::from_env()?;
    logging::init(&settings.logging)?;

    let app = build_app(&settings)?;
    let listener = TcpListener::bind(("0.0.0.0", settings.port)).await?;
    let terminate = signal(SignalKind::terminate())?;

    tracing::info!(port = settings.port, "Gateway started");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal(terminate))
        .await?;
    tracing::info!("Gateway stopped");
    Ok(())
}

use std::error::Error;

use seat_hold::app::build_app;
use seat_hold::db::build_pool;
use seat_hold::logging;
use seat_hold::settings::Settings;
use seat_hold::shutdown::shutdown_signal;
use tokio::net::TcpListener;
use tokio::signal::unix::{SignalKind, signal};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let settings = Settings::from_env()?;
    logging::init(&settings.logging)?;

    let app = build_app(build_pool(&settings));
    let listener = TcpListener::bind(("0.0.0.0", settings.port)).await?;
    let terminate = signal(SignalKind::terminate())?;

    tracing::info!(port = settings.port, "Seat-hold started");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal(terminate))
        .await?;
    tracing::info!("Seat-hold stopped");
    Ok(())
}

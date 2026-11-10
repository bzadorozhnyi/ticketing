use std::error::Error;

use seat_hold::app::build_app;
use seat_hold::core::db::build_pool;
use seat_hold::core::logging;
use seat_hold::core::settings::Settings;
use seat_hold::core::shutdown::shutdown_signal;
use seat_hold::core::telemetry::init as telemetry;
use seat_hold::core::telemetry::metrics::metrics;
use seat_hold::holds::cleanup_job;
use seat_hold::holds::use_cases::cleanup_expired_holds::CleanupExpiredHoldsUseCase;
use tokio::net::TcpListener;
use tokio::signal::unix::{SignalKind, signal};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let telemetry = telemetry::init()?;
    let settings = Settings::from_env()?;
    logging::init(&settings.logging)?;

    let db = build_pool(&settings);
    metrics().observe_pool(db.clone());
    let cleanup = CleanupExpiredHoldsUseCase::new(
        db.clone(),
        settings.holds.cleanup_batch_size,
        settings.holds.idempotency_retention,
    );
    let cleanup_job = tokio::spawn(cleanup_job::run(cleanup, settings.holds.cleanup_interval));

    let app = build_app(db, settings.holds);
    let listener = TcpListener::bind(("0.0.0.0", settings.port)).await?;
    let terminate = signal(SignalKind::terminate())?;

    tracing::info!(port = settings.port, "Seat-hold started");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal(terminate))
        .await?;
    cleanup_job.abort();
    telemetry.shutdown();
    tracing::info!("Seat-hold stopped");
    Ok(())
}

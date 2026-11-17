use std::error::Error;

use seat_hold::core::db::build_pool;
use seat_hold::core::logging;
use seat_hold::core::settings::{RelaySettings, Settings};
use seat_hold::core::shutdown::shutdown_signal;
use seat_hold::outbox::relay::Relay;
use tokio::signal::unix::{SignalKind, signal};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let settings = Settings::from_env()?;
    let relay = RelaySettings::from_env()?;
    logging::init(&settings.logging)?;
    let terminate = signal(SignalKind::terminate())?;

    tracing::info!("Relay started");
    Relay::new(build_pool(&settings), relay.rabbitmq, relay.outbox)
        .run(shutdown_signal(terminate))
        .await;
    tracing::info!("Relay stopped");
    Ok(())
}

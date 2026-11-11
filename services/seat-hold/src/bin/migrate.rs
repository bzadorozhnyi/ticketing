use std::error::Error;

use seat_hold::core::db::build_migration_pool;
use seat_hold::core::logging;
use seat_hold::core::migrations::run_with_lock_retries;
use seat_hold::core::settings::Settings;
use sqlx::migrate::Migrator;

static MIGRATOR: Migrator = sqlx::migrate!();

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let settings = Settings::from_env()?;
    logging::init(&settings.logging)?;
    let pool = build_migration_pool(&settings);
    run_with_lock_retries(&settings.migrations, || MIGRATOR.run(&pool)).await?;
    Ok(())
}

use std::error::Error;

use seat_hold::db::build_pool;
use seat_hold::settings::Settings;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let settings = Settings::from_env()?;
    sqlx::migrate!().run(&build_pool(&settings)).await?;
    Ok(())
}

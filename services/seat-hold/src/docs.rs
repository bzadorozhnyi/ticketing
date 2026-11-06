use std::path::PathBuf;

use utoipa::OpenApi;

use crate::core::health;
use crate::holds::endpoints::internal::routes::{confirm_hold, extend_hold};
use crate::holds::endpoints::v1::routes::{create_hold, release_hold};

#[derive(OpenApi)]
#[openapi(
    info(title = "seat-hold", version = "0.1.0"),
    paths(
        health::health,
        health::ready,
        create_hold::create_hold,
        release_hold::release_hold,
        extend_hold::extend_hold,
        confirm_hold::confirm_hold,
    )
)]
struct ApiDoc;

#[must_use]
pub fn contract_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../contracts/seat-hold.openapi.json")
}

pub fn render_contract() -> Result<String, serde_json::Error> {
    Ok(ApiDoc::openapi().to_pretty_json()? + "\n")
}

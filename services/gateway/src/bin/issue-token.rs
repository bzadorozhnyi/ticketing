use std::env;
use std::error::Error;

use gateway::auth::{TOKEN_LIFETIME, issue_token};
use gateway::settings::Settings;

fn main() -> Result<(), Box<dyn Error + Send + Sync>> {
    let user_id = env::args()
        .nth(1)
        .filter(|user_id| !user_id.is_empty())
        .ok_or("usage: issue-token <user-id>")?;
    let settings = Settings::from_env()?;
    let token = issue_token(&settings.auth.jwt_secret, &user_id, TOKEN_LIFETIME)?;
    println!("{token}");
    Ok(())
}

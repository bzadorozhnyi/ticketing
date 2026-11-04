use std::fmt::Display;
use std::str::FromStr;
use std::time::Duration;

use config::{Config, ConfigError, Environment};
use reqwest::Url;
use serde::de::Error;
use serde::{Deserialize, Deserializer};
use tracing::Level;

/// RFC 7518 requires a key at least as long as the HS256 hash. A shorter one
/// can be brute-forced offline from any single token.
const MIN_JWT_SECRET_BYTES: usize = 32;

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum LogFormat {
    Json,
    Pretty,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct LoggingSettings {
    pub format: LogFormat,
    #[serde(deserialize_with = "from_str")]
    pub level: Level,
}

impl Default for LoggingSettings {
    fn default() -> Self {
        Self {
            format: LogFormat::Json,
            level: Level::INFO,
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct UpstreamSettings {
    #[serde(deserialize_with = "from_str")]
    pub catalog_url: Url,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct ClientSettings {
    #[serde(rename = "connect_timeout_seconds", deserialize_with = "seconds")]
    pub connect_timeout: Duration,
    #[serde(rename = "request_timeout_seconds", deserialize_with = "seconds")]
    pub request_timeout: Duration,
    #[serde(rename = "pool_idle_timeout_seconds", deserialize_with = "seconds")]
    pub pool_idle_timeout: Duration,
}

impl Default for ClientSettings {
    fn default() -> Self {
        Self {
            connect_timeout: Duration::from_secs(2),
            request_timeout: Duration::from_secs(10),
            pool_idle_timeout: Duration::from_secs(3),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct LimitSettings {
    pub max_body_bytes: usize,
}

impl Default for LimitSettings {
    fn default() -> Self {
        Self {
            max_body_bytes: 1024 * 1024,
        }
    }
}

/// No `Debug` here or on `Settings`: the secret must not be printable by accident.
#[derive(Clone, Deserialize)]
pub struct AuthSettings {
    #[serde(deserialize_with = "jwt_secret")]
    pub jwt_secret: String,
}

#[derive(Clone, Deserialize)]
pub struct Settings {
    #[serde(default = "default_port")]
    pub port: u16,
    pub auth: AuthSettings,
    pub upstreams: UpstreamSettings,
    #[serde(default)]
    pub client: ClientSettings,
    #[serde(default)]
    pub limits: LimitSettings,
    #[serde(default)]
    pub logging: LoggingSettings,
}

impl Settings {
    pub fn from_env() -> Result<Self, ConfigError> {
        Config::builder()
            .add_source(Environment::default().separator("__"))
            .build()?
            .try_deserialize()
    }
}

fn default_port() -> u16 {
    8000
}

fn from_str<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: FromStr,
    T::Err: Display,
{
    String::deserialize(deserializer)?
        .parse()
        .map_err(D::Error::custom)
}

fn jwt_secret<'de, D: Deserializer<'de>>(deserializer: D) -> Result<String, D::Error> {
    let secret = String::deserialize(deserializer)?;
    if secret.len() < MIN_JWT_SECRET_BYTES {
        return Err(D::Error::custom(format!(
            "must be at least {MIN_JWT_SECRET_BYTES} bytes"
        )));
    }
    Ok(secret)
}

fn seconds<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Duration, D::Error> {
    Duration::try_from_secs_f64(f64::deserialize(deserializer)?).map_err(D::Error::custom)
}

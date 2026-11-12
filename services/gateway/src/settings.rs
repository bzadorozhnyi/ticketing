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
    #[serde(deserialize_with = "from_str")]
    pub seat_hold_url: Url,
    #[serde(deserialize_with = "from_str")]
    pub booking_url: Url,
    #[serde(
        default,
        rename = "catalog_timeout_seconds",
        deserialize_with = "optional_seconds"
    )]
    pub catalog_timeout: Option<Duration>,
    #[serde(
        default,
        rename = "seat_hold_timeout_seconds",
        deserialize_with = "optional_seconds"
    )]
    pub seat_hold_timeout: Option<Duration>,
    #[serde(
        default,
        rename = "booking_timeout_seconds",
        deserialize_with = "optional_seconds"
    )]
    pub booking_timeout: Option<Duration>,
    #[serde(default)]
    pub catalog_max_concurrent: Option<usize>,
    #[serde(default)]
    pub seat_hold_max_concurrent: Option<usize>,
    #[serde(default)]
    pub booking_max_concurrent: Option<usize>,
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
    #[serde(rename = "pool_max_age_seconds", deserialize_with = "seconds")]
    pub pool_max_age: Duration,
}

impl Default for ClientSettings {
    fn default() -> Self {
        Self {
            connect_timeout: Duration::from_secs(2),
            request_timeout: Duration::from_secs(10),
            pool_idle_timeout: Duration::from_secs(3),
            pool_max_age: Duration::from_secs(10),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct RetrySettings {
    pub max_attempts: u32,
    #[serde(rename = "base_delay_seconds", deserialize_with = "seconds")]
    pub base_delay: Duration,
    pub budget_ratio: f64,
    pub budget_capacity: f64,
}

impl Default for RetrySettings {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            base_delay: Duration::from_millis(50),
            budget_ratio: 0.2,
            budget_capacity: 10.0,
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct BreakerSettings {
    pub window: usize,
    pub min_calls: usize,
    pub failure_percent: usize,
    #[serde(rename = "open_seconds", deserialize_with = "seconds")]
    pub open_duration: Duration,
}

impl Default for BreakerSettings {
    fn default() -> Self {
        Self {
            window: 20,
            min_calls: 10,
            failure_percent: 50,
            open_duration: Duration::from_secs(5),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct BulkheadSettings {
    pub max_concurrent: usize,
}

impl Default for BulkheadSettings {
    fn default() -> Self {
        Self {
            max_concurrent: 200,
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct RateLimitSettings {
    pub valkey_url: Option<String>,
    pub per_second: f64,
    pub burst: u32,
    #[serde(rename = "timeout_seconds", deserialize_with = "seconds")]
    pub timeout: Duration,
}

impl Default for RateLimitSettings {
    fn default() -> Self {
        Self {
            valkey_url: None,
            per_second: 10.0,
            burst: 20,
            timeout: Duration::from_millis(50),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct LimitSettings {
    pub max_body_bytes: usize,
    pub max_in_flight: usize,
    pub max_connections: usize,
    #[serde(rename = "header_timeout_seconds", deserialize_with = "seconds")]
    pub header_timeout: Duration,
}

impl Default for LimitSettings {
    fn default() -> Self {
        Self {
            max_body_bytes: 1024 * 1024,
            max_in_flight: 1000,
            max_connections: 10_000,
            header_timeout: Duration::from_secs(30),
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
    pub retry: RetrySettings,
    #[serde(default)]
    pub breaker: BreakerSettings,
    #[serde(default)]
    pub bulkhead: BulkheadSettings,
    #[serde(default)]
    pub rate_limit: RateLimitSettings,
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

fn optional_seconds<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<Duration>, D::Error> {
    Option::<f64>::deserialize(deserializer)?
        .map(Duration::try_from_secs_f64)
        .transpose()
        .map_err(D::Error::custom)
}

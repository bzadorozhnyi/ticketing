use std::fmt::Display;
use std::str::FromStr;
use std::time::Duration;

use config::{Config, ConfigError, Environment};
use serde::de::Error;
use serde::{Deserialize, Deserializer};
use tracing::Level;

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

/// No `Debug` here or on `Settings`: the password must not be printable by accident.
#[derive(Clone, Deserialize)]
pub struct DbSettings {
    pub host: String,
    #[serde(default = "default_db_port")]
    pub port: u16,
    pub name: String,
    pub user: String,
    pub pass: String,
}

/// `lock_timeout` and `statement_timeout` bound the service's own sessions, so a lock held
/// by someone else fails a request instead of keeping its pooled connection. 0 turns one off.
#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct PoolSettings {
    pub max_connections: u32,
    #[serde(rename = "acquire_timeout_seconds", deserialize_with = "seconds")]
    pub acquire_timeout: Duration,
    #[serde(rename = "lock_timeout_seconds", deserialize_with = "seconds")]
    pub lock_timeout: Duration,
    #[serde(rename = "statement_timeout_seconds", deserialize_with = "seconds")]
    pub statement_timeout: Duration,
}

impl Default for PoolSettings {
    fn default() -> Self {
        Self {
            max_connections: 10,
            acquire_timeout: Duration::from_secs(5),
            lock_timeout: Duration::from_secs(1),
            statement_timeout: Duration::from_secs(5),
        }
    }
}

/// The migration's session gives up a lock after `lock_timeout`, and the run is tried again,
/// at most `attempts` times; there is no statement timeout (see `core/migrations.rs`).
#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub struct MigrationSettings {
    #[serde(rename = "lock_timeout_seconds", deserialize_with = "seconds")]
    pub lock_timeout: Duration,
    pub attempts: u32,
    #[serde(rename = "retry_base_delay_seconds", deserialize_with = "seconds")]
    pub retry_base_delay: Duration,
    #[serde(rename = "max_retry_delay_seconds", deserialize_with = "seconds")]
    pub max_retry_delay: Duration,
}

impl Default for MigrationSettings {
    fn default() -> Self {
        Self {
            lock_timeout: Duration::from_secs(2),
            attempts: 10,
            retry_base_delay: Duration::from_secs(1),
            max_retry_delay: Duration::from_secs(15),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(default)]
pub struct HoldSettings {
    #[serde(rename = "ttl_seconds", deserialize_with = "seconds")]
    pub ttl: Duration,
    #[serde(rename = "payment_ttl_seconds", deserialize_with = "seconds")]
    pub payment_ttl: Duration,
    pub max_seats: usize,
    pub max_seats_per_user: i64,
    #[serde(rename = "cleanup_interval_seconds", deserialize_with = "seconds")]
    pub cleanup_interval: Duration,
    pub cleanup_batch_size: i64,
    #[serde(rename = "idempotency_retention_seconds", deserialize_with = "seconds")]
    pub idempotency_retention: Duration,
}

impl Default for HoldSettings {
    fn default() -> Self {
        Self {
            ttl: Duration::from_mins(5),
            payment_ttl: Duration::from_mins(15),
            max_seats: 10,
            max_seats_per_user: 10,
            cleanup_interval: Duration::from_mins(1),
            cleanup_batch_size: 1000,
            idempotency_retention: Duration::from_hours(24),
        }
    }
}

#[derive(Clone, Deserialize)]
pub struct Settings {
    #[serde(default = "default_port")]
    pub port: u16,
    pub db: DbSettings,
    #[serde(default)]
    pub pool: PoolSettings,
    #[serde(default)]
    pub migrations: MigrationSettings,
    #[serde(default)]
    pub holds: HoldSettings,
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

fn default_db_port() -> u16 {
    5432
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

fn seconds<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Duration, D::Error> {
    Duration::try_from_secs_f64(f64::deserialize(deserializer)?).map_err(D::Error::custom)
}

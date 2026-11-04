use std::fmt::Display;
use std::str::FromStr;
use std::time::Duration;

use config::{Config, ConfigError, Environment};
use reqwest::Url;
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

#[derive(Clone, Debug, Deserialize)]
pub struct Settings {
    #[serde(default = "default_port")]
    pub port: u16,
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

fn seconds<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Duration, D::Error> {
    Duration::try_from_secs_f64(f64::deserialize(deserializer)?).map_err(D::Error::custom)
}

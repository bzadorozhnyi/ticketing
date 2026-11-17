//! The only module that names the date-time library. The rest of the code uses what is
//! here, so replacing the library changes this file and the database driver's feature.

use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

/// A moment in time. The database returns `timestamptz` as this type.
pub type Timestamp = OffsetDateTime;

/// For `#[serde(with = "rfc3339")]` (and `rfc3339::option`): RFC 3339 text, the format of
/// the API and the events.
pub use time::serde::rfc3339;

#[must_use]
pub fn now() -> Timestamp {
    OffsetDateTime::now_utc()
}

/// RFC 3339 text, such as `2026-11-17T09:28:14.067256Z`.
#[must_use]
pub fn parse(text: &str) -> Option<Timestamp> {
    OffsetDateTime::parse(text, &Rfc3339).ok()
}

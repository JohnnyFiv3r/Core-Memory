use std::fmt;

use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

/// 0000-01-01T00:00:00Z in Unix milliseconds.
const MIN_MILLIS: i64 = -62_167_219_200_000;
/// 9999-12-31T23:59:59.999Z in Unix milliseconds.
const MAX_MILLIS: i64 = 253_402_300_799_999;

/// An instant with its UTC offset, serialized as RFC 3339.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Timestamp(pub OffsetDateTime);

impl Timestamp {
    /// The instant `millis` milliseconds after the Unix epoch, clamped to
    /// the range RFC 3339 can express (years 0000 to 9999).
    pub fn from_unix_millis(millis: i64) -> Self {
        let millis = millis.clamp(MIN_MILLIS, MAX_MILLIS);
        let nanos = i128::from(millis) * 1_000_000;
        Self(OffsetDateTime::from_unix_timestamp_nanos(nanos).unwrap_or(OffsetDateTime::UNIX_EPOCH))
    }

    /// The latest expressible instant, 9999-12-31T23:59:59.999Z.
    pub fn max() -> Self {
        Self::from_unix_millis(MAX_MILLIS)
    }

    /// Milliseconds since the Unix epoch, saturating.
    pub fn unix_millis(&self) -> i64 {
        i64::try_from(self.0.unix_timestamp_nanos() / 1_000_000).unwrap_or(i64::MAX)
    }

    /// This instant plus `millis` milliseconds, saturating at the end of
    /// the representable range.
    pub fn plus_millis(self, millis: u64) -> Self {
        let delta = time::Duration::milliseconds(i64::try_from(millis).unwrap_or(i64::MAX));
        Self(self.0.checked_add(delta).unwrap_or(self.0))
    }

    /// The UTC calendar date, `YYYY-MM-DD`.
    pub fn utc_date(&self) -> String {
        let d = self.0.to_offset(time::UtcOffset::UTC).date();
        format!("{:04}-{:02}-{:02}", d.year(), u8::from(d.month()), d.day())
    }

    /// Midnight UTC at the start of the next day.
    pub fn next_utc_midnight(&self) -> Self {
        let date = self.0.to_offset(time::UtcOffset::UTC).date();
        match date.next_day() {
            Some(next) => Self(next.midnight().assume_utc()),
            None => *self,
        }
    }

    pub fn parse(s: &str) -> Result<Self, time::error::Parse> {
        OffsetDateTime::parse(s, &Rfc3339).map(Self)
    }

    pub fn to_rfc3339(&self) -> String {
        // Rfc3339 formatting only fails for years outside 0..=9999, which
        // `parse` and the clocks in this workspace never produce.
        self.0
            .format(&Rfc3339)
            .unwrap_or_else(|_| self.0.unix_timestamp().to_string())
    }
}

impl fmt::Display for Timestamp {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_rfc3339())
    }
}

impl fmt::Debug for Timestamp {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Timestamp({})", self.to_rfc3339())
    }
}

impl Serialize for Timestamp {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let s = self.0.format(&Rfc3339).map_err(serde::ser::Error::custom)?;
        serializer.serialize_str(&s)
    }
}

impl<'de> Deserialize<'de> for Timestamp {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let s = String::deserialize(deserializer)?;
        Timestamp::parse(&s).map_err(serde::de::Error::custom)
    }
}

impl JsonSchema for Timestamp {
    fn schema_name() -> std::borrow::Cow<'static, str> {
        "Timestamp".into()
    }

    fn json_schema(_: &mut schemars::SchemaGenerator) -> schemars::Schema {
        schemars::json_schema!({
            "type": "string",
            "format": "date-time",
            "description": "RFC 3339 timestamp with offset"
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equal_instants_format_identically() {
        let a = Timestamp::parse("2026-10-26T14:00:00Z").unwrap();
        let b = Timestamp::parse("2026-10-26T14:00:00+00:00").unwrap();
        assert_eq!(a, b);
        assert_eq!(a.to_rfc3339(), b.to_rfc3339());
    }

    #[test]
    fn day_arithmetic_is_in_utc() {
        let t = Timestamp::parse("2026-10-26T23:30:00-05:00").unwrap();
        assert_eq!(t.utc_date(), "2026-10-27");
        assert_eq!(t.next_utc_midnight().to_rfc3339(), "2026-10-28T00:00:00Z");
        assert_eq!(
            Timestamp::from_unix_millis(0)
                .plus_millis(1_500)
                .to_rfc3339(),
            "1970-01-01T00:00:01.5Z"
        );
        assert_eq!(Timestamp::from_unix_millis(1_234).unix_millis(), 1_234);
        assert_eq!(Timestamp::from_unix_millis(i64::MAX), Timestamp::max());
        assert_eq!(Timestamp::max().to_rfc3339(), "9999-12-31T23:59:59.999Z");
        assert!(Timestamp::from_unix_millis(i64::MIN) < Timestamp::from_unix_millis(0));
    }

    #[test]
    fn from_unix_millis() {
        assert_eq!(
            Timestamp::from_unix_millis(1_000).to_rfc3339(),
            "1970-01-01T00:00:01Z"
        );
    }
}

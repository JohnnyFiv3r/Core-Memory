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
///
/// Every timestamp read from text is an instant whose UTC year is 0000 to
/// 9999, so it can always be written back as RFC 3339 and converted to UTC.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Timestamp(pub OffsetDateTime);

/// Why a string is not a timestamp.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// Not RFC 3339.
    Syntax(time::error::Parse),
    /// RFC 3339, but its instant in UTC falls outside years 0000 to 9999.
    /// Only a time within a day of either end, written with an offset, can
    /// do that.
    OutOfRange,
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::Syntax(e) => write!(f, "not an RFC 3339 timestamp: {e}"),
            ParseError::OutOfRange => {
                f.write_str("the instant in UTC is outside years 0000 to 9999")
            }
        }
    }
}

impl std::error::Error for ParseError {}

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
        match self.0.checked_add(delta) {
            Some(later) if later <= Self::max().0 => Self(later),
            _ => Self::max(),
        }
    }

    /// This instant in UTC. A value built outside [`Timestamp::parse`]
    /// whose UTC year is not 0000 to 9999 is clamped to that range.
    fn utc(self) -> OffsetDateTime {
        match self.0.checked_to_offset(time::UtcOffset::UTC) {
            Some(utc) if (0..=9999).contains(&utc.year()) => utc,
            _ => Self::from_unix_millis(self.unix_millis()).0,
        }
    }

    /// This instant in UTC, truncated to whole microseconds: the precision
    /// the store keeps. Values are truncated before they are stored, so what
    /// is read back equals what was written.
    pub fn truncated_to_micros(self) -> Self {
        let utc = self.utc();
        let nanos = utc.nanosecond() / 1_000 * 1_000;
        Self(utc.replace_nanosecond(nanos).unwrap_or(utc))
    }

    /// The UTC calendar date, `YYYY-MM-DD`.
    pub fn utc_date(&self) -> String {
        let d = self.utc().date();
        format!("{:04}-{:02}-{:02}", d.year(), u8::from(d.month()), d.day())
    }

    /// Midnight UTC at the start of the next day.
    pub fn next_utc_midnight(&self) -> Self {
        let date = self.utc().date();
        match date.next_day() {
            Some(next) if next.year() <= 9999 => Self(next.midnight().assume_utc()),
            _ => *self,
        }
    }

    /// Reads an RFC 3339 timestamp whose instant in UTC is in years 0000
    /// to 9999.
    pub fn parse(s: &str) -> Result<Self, ParseError> {
        let parsed = OffsetDateTime::parse(s, &Rfc3339).map_err(ParseError::Syntax)?;
        match parsed.checked_to_offset(time::UtcOffset::UTC) {
            Some(utc) if (0..=9999).contains(&utc.year()) => Ok(Self(parsed)),
            _ => Err(ParseError::OutOfRange),
        }
    }

    pub fn to_rfc3339(&self) -> String {
        // Rfc3339 formatting only fails for years outside 0..=9999, which
        // `parse` and the clocks in this workspace never produce. Such a
        // value is written as the nearest instant that can be.
        self.0.format(&Rfc3339).unwrap_or_else(|_| {
            self.utc()
                .format(&Rfc3339)
                .unwrap_or_else(|_| self.0.unix_timestamp().to_string())
        })
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
        serializer.serialize_str(&self.to_rfc3339())
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
    fn truncating_keeps_the_instant_to_the_microsecond() {
        let t = Timestamp::parse("2026-11-17T09:30:00.123456789+02:00").unwrap();
        let stored = t.truncated_to_micros();
        assert_eq!(stored.to_rfc3339(), "2026-11-17T07:30:00.123456Z");
        assert_eq!(stored.truncated_to_micros(), stored);
    }

    #[test]
    fn adding_saturates_at_the_latest_instant() {
        let now = Timestamp::from_unix_millis(1_793_000_000_000);
        assert_eq!(now.plus_millis(u64::MAX), Timestamp::max());
        assert_eq!(
            now.plus_millis(u64::from(u32::MAX) * 86_400_000),
            Timestamp::max(),
            "a far-off deadline saturates; it never falls back to now"
        );
        assert_eq!(now.plus_millis(1).unix_millis(), 1_793_000_000_001);
    }

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
    fn an_instant_outside_the_expressible_years_is_refused() {
        // Valid RFC 3339, but a day past either end once in UTC.
        for s in ["9999-12-31T23:59:59-01:00", "0000-01-01T00:00:00+01:00"] {
            assert_eq!(
                Timestamp::parse(s).unwrap_err(),
                ParseError::OutOfRange,
                "{s}"
            );
            assert!(serde_json::from_str::<Timestamp>(&format!("\"{s}\"")).is_err());
        }
        // The ends themselves are fine, with any offset that keeps them in range.
        for (s, utc) in [
            ("9999-12-31T23:59:59Z", "9999-12-31T23:59:59Z"),
            ("9999-12-31T23:59:59+14:00", "9999-12-31T09:59:59Z"),
            ("0000-01-01T00:00:00Z", "0000-01-01T00:00:00Z"),
            ("0000-01-01T00:00:00-12:00", "0000-01-01T12:00:00Z"),
        ] {
            let t = Timestamp::parse(s).unwrap();
            assert_eq!(t.truncated_to_micros().to_rfc3339(), utc, "{s}");
            assert_eq!(serde_json::to_string(&t).unwrap(), format!("\"{s}\""));
        }
        assert!(matches!(
            Timestamp::parse("yesterday").unwrap_err(),
            ParseError::Syntax(_)
        ));
    }

    #[test]
    fn a_value_built_out_of_range_never_panics() {
        use time::macros::{datetime, offset};
        // Built directly, not parsed: one hour before year 0000 in UTC,
        // and one hour after year 9999.
        let early = Timestamp(datetime!(0000-01-01 00:00 UTC).to_offset(offset!(+1)));
        let late = Timestamp(datetime!(9999-12-31 23:30 UTC).to_offset(offset!(-1)));
        assert!(Timestamp(early.0.replace_offset(offset!(+2))).truncated_to_micros() >= early);
        for t in [early, late] {
            let _ = t.truncated_to_micros();
            let _ = t.utc_date();
            let _ = t.next_utc_midnight();
            assert!(!t.to_rfc3339().is_empty());
            assert!(serde_json::to_string(&t).is_ok());
        }
    }

    #[test]
    fn from_unix_millis() {
        assert_eq!(
            Timestamp::from_unix_millis(1_000).to_rfc3339(),
            "1970-01-01T00:00:01Z"
        );
    }
}

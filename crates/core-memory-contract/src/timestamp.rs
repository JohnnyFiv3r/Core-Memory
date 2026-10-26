use std::fmt;

use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

/// An instant with its UTC offset, serialized as RFC 3339.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Timestamp(pub OffsetDateTime);

impl Timestamp {
    pub fn from_unix_millis(millis: i64) -> Self {
        let nanos = i128::from(millis) * 1_000_000;
        Self(OffsetDateTime::from_unix_timestamp_nanos(nanos).unwrap_or(OffsetDateTime::UNIX_EPOCH))
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
    fn from_unix_millis() {
        assert_eq!(
            Timestamp::from_unix_millis(1_000).to_rfc3339(),
            "1970-01-01T00:00:01Z"
        );
    }
}

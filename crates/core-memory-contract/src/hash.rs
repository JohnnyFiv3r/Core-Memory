//! SHA-256 digests and the length-prefixed hashing used for identities.

use std::fmt;
use std::str::FromStr;

use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};

/// A SHA-256 digest, serialized as 64 lowercase hex characters.
///
/// This matches memoriesQL's `content_hash` format (`^[a-f0-9]{64}$`).
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Sha256Digest(pub [u8; 32]);

impl Sha256Digest {
    /// Hashes `bytes` with plain SHA-256.
    pub fn of(bytes: &[u8]) -> Self {
        Self(Sha256::digest(bytes).into())
    }

    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }

    pub fn to_hex(&self) -> String {
        to_hex(&self.0)
    }
}

impl fmt::Display for Sha256Digest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_hex())
    }
}

impl fmt::Debug for Sha256Digest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Sha256Digest({})", self.to_hex())
    }
}

/// Error returned when a string is not 64 lowercase hex characters.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("expected 64 lowercase hex characters")]
pub struct ParseDigestError;

impl FromStr for Sha256Digest {
    type Err = ParseDigestError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let bytes = s.as_bytes();
        if bytes.len() != 64 {
            return Err(ParseDigestError);
        }
        let mut out = [0u8; 32];
        for (i, pair) in bytes.chunks_exact(2).enumerate() {
            out[i] = (hex_value(pair[0])? << 4) | hex_value(pair[1])?;
        }
        Ok(Self(out))
    }
}

fn hex_value(c: u8) -> Result<u8, ParseDigestError> {
    match c {
        b'0'..=b'9' => Ok(c - b'0'),
        b'a'..=b'f' => Ok(c - b'a' + 10),
        _ => Err(ParseDigestError),
    }
}

/// Lowercase hex encoding.
pub fn to_hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(DIGITS[(b >> 4) as usize] as char);
        s.push(DIGITS[(b & 0x0f) as usize] as char);
    }
    s
}

impl Serialize for Sha256Digest {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_hex())
    }
}

impl<'de> Deserialize<'de> for Sha256Digest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let s = String::deserialize(deserializer)?;
        s.parse().map_err(serde::de::Error::custom)
    }
}

impl JsonSchema for Sha256Digest {
    fn schema_name() -> std::borrow::Cow<'static, str> {
        "Sha256Digest".into()
    }

    fn json_schema(_: &mut schemars::SchemaGenerator) -> schemars::Schema {
        schemars::json_schema!({
            "type": "string",
            "pattern": "^[a-f0-9]{64}$",
            "description": "SHA-256 digest as 64 lowercase hex characters"
        })
    }
}

/// SHA-256 over a domain tag followed by length-prefixed fields.
///
/// Every field is written as its byte length (u64, little endian) and then
/// its bytes, so no two different field sequences hash the same input. The
/// domain tag keeps hashes for different purposes apart. Changing a domain
/// tag or the field order changes every derived identity, so treat both as
/// part of the contract.
#[derive(Debug, Clone)]
pub struct FieldHasher {
    inner: Sha256,
}

impl FieldHasher {
    pub fn new(domain: &str) -> Self {
        let mut hasher = Self {
            inner: Sha256::new(),
        };
        hasher.bytes(domain.as_bytes());
        hasher
    }

    pub fn bytes(&mut self, bytes: &[u8]) -> &mut Self {
        self.inner.update((bytes.len() as u64).to_le_bytes());
        self.inner.update(bytes);
        self
    }

    pub fn str(&mut self, s: &str) -> &mut Self {
        self.bytes(s.as_bytes())
    }

    pub fn opt_str(&mut self, s: Option<&str>) -> &mut Self {
        match s {
            None => self.bytes(&[0]),
            Some(s) => self.bytes(&[1]).str(s),
        }
    }

    pub fn u64(&mut self, v: u64) -> &mut Self {
        self.bytes(&v.to_le_bytes())
    }

    pub fn finish(self) -> [u8; 32] {
        self.inner.finalize().into()
    }
}

/// Hashes `value` in RFC 8785 (JCS) canonical JSON form under a domain tag.
///
/// Used where a hash must cover structured data: the result does not depend
/// on map ordering or on whitespace.
pub fn canonical_json_digest<T: Serialize>(
    domain: &str,
    value: &T,
) -> Result<Sha256Digest, serde_json::Error> {
    let canonical = serde_json_canonicalizer::to_vec(value)?;
    let mut hasher = FieldHasher::new(domain);
    hasher.bytes(&canonical);
    Ok(Sha256Digest(hasher.finish()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_round_trip() {
        let d = Sha256Digest::of(b"core memory");
        let parsed: Sha256Digest = d.to_hex().parse().unwrap();
        assert_eq!(d, parsed);
        assert_eq!(d.to_hex().len(), 64);
    }

    #[test]
    fn known_vector() {
        assert_eq!(
            Sha256Digest::of(b"abc").to_hex(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn rejects_uppercase_and_short_hex() {
        assert!("AB".repeat(32).parse::<Sha256Digest>().is_err());
        assert!("ab".parse::<Sha256Digest>().is_err());
    }

    #[test]
    fn length_prefix_separates_fields() {
        let mut a = FieldHasher::new("t");
        a.str("ab").str("c");
        let mut b = FieldHasher::new("t");
        b.str("a").str("bc");
        assert_ne!(a.finish(), b.finish());
    }

    #[test]
    fn canonical_digest_ignores_key_order() {
        let x: serde_json::Value = serde_json::from_str(r#"{"a":1,"b":[true,null]}"#).unwrap();
        let y: serde_json::Value =
            serde_json::from_str(r#"{ "b": [true, null], "a": 1 }"#).unwrap();
        assert_eq!(
            canonical_json_digest("t", &x).unwrap(),
            canonical_json_digest("t", &y).unwrap()
        );
    }
}

//! Derived identifiers.
//!
//! Every ID is a UUID (version 8, RFC 9562) built from the first 16 bytes of
//! a domain-separated SHA-256 over the ID's inputs. They are deterministic,
//! never positional, and directly usable where memoriesQL expects UUIDs
//! (`event_id`, `source_unit_id`).

use std::fmt;
use std::str::FromStr;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::event::EventKey;
use crate::hash::FieldHasher;

const EVENT_ID_DOMAIN: &str = "core-memory/event-id/v1";
const CHUNK_ID_DOMAIN: &str = "core-memory/chunk-id/v1";
const DECISION_ID_DOMAIN: &str = "core-memory/decision-id/v1";

fn uuid_from_digest(digest: [u8; 32]) -> Uuid {
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    Uuid::new_v8(bytes)
}

macro_rules! derived_id {
    ($(#[$doc:meta])* $name:ident) => {
        $(#[$doc])*
        #[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema)]
        #[serde(transparent)]
        pub struct $name(pub Uuid);

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                fmt::Display::fmt(&self.0.hyphenated(), f)
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}({})", stringify!($name), self.0.hyphenated())
            }
        }

        impl FromStr for $name {
            type Err = uuid::Error;

            fn from_str(s: &str) -> Result<Self, Self::Err> {
                Uuid::parse_str(s).map(Self)
            }
        }
    };
}

derived_id!(
    /// Identity of an admitted SourceEvent, derived from its [`EventKey`].
    EventId
);
derived_id!(
    /// Identity of a Chunk (CHK-04).
    ChunkId
);
derived_id!(
    /// Identity of a type decision or relationship decision (MOD-03).
    DecisionId
);

impl EventId {
    pub fn derive(key: &EventKey) -> Self {
        let mut h = FieldHasher::new(EVENT_ID_DOMAIN);
        key.hash_into(&mut h);
        Self(uuid_from_digest(h.finish()))
    }
}

impl ChunkId {
    /// `hash(event_key, chunker id + revision, byte range)`.
    ///
    /// Never contains a global index. Re-chunking under a new chunker
    /// revision yields new IDs even for identical ranges (CHK-05).
    pub fn derive(
        key: &EventKey,
        chunker_id: &str,
        chunker_rev: u32,
        byte_start: u64,
        byte_end: u64,
    ) -> Self {
        let mut h = FieldHasher::new(CHUNK_ID_DOMAIN);
        key.hash_into(&mut h);
        h.str(chunker_id)
            .u64(u64::from(chunker_rev))
            .u64(byte_start)
            .u64(byte_end);
        Self(uuid_from_digest(h.finish()))
    }
}

impl DecisionId {
    /// Derives a decision ID from the decided subject and a description of
    /// the decision. `parts` must be written in a fixed order by the caller.
    pub fn derive(subject: &ChunkId, parts: &[&str]) -> Self {
        let mut h = FieldHasher::new(DECISION_ID_DOMAIN);
        h.bytes(subject.0.as_bytes());
        for part in parts {
            h.str(part);
        }
        Self(uuid_from_digest(h.finish()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(rev: Option<&str>) -> EventKey {
        EventKey {
            scope: "acme".into(),
            source_ns: "slack/T01".into(),
            external_id: "C1/1".into(),
            revision: rev.map(Into::into),
        }
    }

    #[test]
    fn ids_are_version_8_uuids() {
        let id = EventId::derive(&key(None));
        assert_eq!(id.0.get_version_num(), 8);
        assert_eq!(id.0.get_variant(), uuid::Variant::RFC4122);
    }

    #[test]
    fn event_ids_are_stable_and_revision_sensitive() {
        assert_eq!(EventId::derive(&key(None)), EventId::derive(&key(None)));
        assert_ne!(
            EventId::derive(&key(None)),
            EventId::derive(&key(Some("2")))
        );
        // An absent revision differs from an empty one.
        assert_ne!(EventId::derive(&key(None)), EventId::derive(&key(Some(""))));
    }

    #[test]
    fn chunk_ids_depend_on_chunker_revision_and_range() {
        let k = key(None);
        let a = ChunkId::derive(&k, "text.structural", 1, 0, 10);
        assert_eq!(a, ChunkId::derive(&k, "text.structural", 1, 0, 10));
        assert_ne!(a, ChunkId::derive(&k, "text.structural", 2, 0, 10));
        assert_ne!(a, ChunkId::derive(&k, "text.structural", 1, 0, 11));
    }

    /// Pins the derivations. If these change, every stored ID changes, so a
    /// failure here means the change needs a new domain tag and a migration.
    #[test]
    fn derivations_are_pinned() {
        assert_eq!(
            EventId::derive(&key(None)).to_string(),
            "1eda07c7-2ea1-8a19-9102-2e4e00b3b572"
        );
        assert_eq!(
            ChunkId::derive(&key(None), "text.structural", 1, 0, 10).to_string(),
            "56ebe04c-3896-8aa2-8e61-9289d1a9a39b"
        );
    }

    #[test]
    fn display_round_trips() {
        let id = ChunkId::derive(&key(None), "c", 1, 0, 1);
        assert_eq!(id, id.to_string().parse().unwrap());
    }
}

//! Derived identifiers.
//!
//! Every ID is a UUID (version 8, RFC 9562). Event and chunk IDs are built
//! from the first 16 bytes of an HMAC-SHA-256, under the workspace's
//! [`IdKey`], of a domain-separated SHA-256 over the ID's inputs. They are
//! deterministic, so a replay finds the same row, and never positional.
//! They are keyed, so an ID that outlives an erased event does not let a
//! reader confirm a guessed event key.

use std::fmt;
use std::str::FromStr;

use hmac::{Hmac, KeyInit, Mac};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use uuid::Uuid;

use crate::event::EventKey;
use crate::hash::FieldHasher;

const EVENT_ID_DOMAIN: &str = "core-memory/event-id/v2";
const CHUNK_ID_DOMAIN: &str = "core-memory/chunk-id/v2";

/// The secret that keys a workspace's derived IDs. It is created with the
/// workspace, never changes and never leaves the store and the engine.
#[derive(Clone, PartialEq, Eq)]
pub struct IdKey([u8; 32]);

impl IdKey {
    pub const LEN: usize = 32;

    pub fn from_bytes(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    /// `None` unless `bytes` is exactly [`IdKey::LEN`] bytes.
    pub fn from_slice(bytes: &[u8]) -> Option<Self> {
        <[u8; 32]>::try_from(bytes).ok().map(Self)
    }

    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

impl fmt::Debug for IdKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("IdKey(<redacted>)")
    }
}

fn uuid_from_digest(digest: [u8; 32]) -> Uuid {
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    Uuid::new_v8(bytes)
}

fn keyed_uuid(id_key: &IdKey, digest: [u8; 32]) -> Uuid {
    // HMAC accepts keys of any length; a 32-byte key cannot fail.
    let mut mac = match <Hmac<Sha256> as KeyInit>::new_from_slice(id_key.as_bytes()) {
        Ok(mac) => mac,
        Err(_) => unreachable!("HMAC takes keys of any length"),
    };
    mac.update(&digest);
    uuid_from_digest(mac.finalize().into_bytes().into())
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
    /// Identity of a bead: an unchanging pointer other records link to. A
    /// message chunk's bead has the chunk's own ID.
    BeadId
);
derived_id!(
    /// Identity of one model call. The store assigns it when it writes the
    /// call's receipt, before anything is sent.
    CallId
);

impl EventId {
    pub fn derive(id_key: &IdKey, key: &EventKey) -> Self {
        let mut h = FieldHasher::new(EVENT_ID_DOMAIN);
        key.hash_into(&mut h);
        Self(keyed_uuid(id_key, h.finish()))
    }
}

impl ChunkId {
    /// `hmac(id_key, hash(event_key, chunker id + revision, byte range))`.
    ///
    /// Never contains a global index. Re-chunking under a new chunker
    /// revision yields new IDs even for identical ranges (CHK-05).
    pub fn derive(
        id_key: &IdKey,
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
        Self(keyed_uuid(id_key, h.finish()))
    }
}

impl From<ChunkId> for BeadId {
    fn from(chunk_id: ChunkId) -> Self {
        Self(chunk_id.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id_key(fill: u8) -> IdKey {
        IdKey::from_bytes([fill; 32])
    }

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
        let id = EventId::derive(&id_key(1), &key(None));
        assert_eq!(id.0.get_version_num(), 8);
        assert_eq!(id.0.get_variant(), uuid::Variant::RFC4122);
    }

    #[test]
    fn event_ids_are_stable_and_revision_sensitive() {
        let k = id_key(1);
        assert_eq!(
            EventId::derive(&k, &key(None)),
            EventId::derive(&k, &key(None))
        );
        assert_ne!(
            EventId::derive(&k, &key(None)),
            EventId::derive(&k, &key(Some("2")))
        );
        // An absent revision differs from an empty one.
        assert_ne!(
            EventId::derive(&k, &key(None)),
            EventId::derive(&k, &key(Some("")))
        );
    }

    #[test]
    fn ids_depend_on_the_workspace_key() {
        assert_ne!(
            EventId::derive(&id_key(1), &key(None)),
            EventId::derive(&id_key(2), &key(None))
        );
        assert_ne!(
            ChunkId::derive(&id_key(1), &key(None), "c", 1, 0, 1),
            ChunkId::derive(&id_key(2), &key(None), "c", 1, 0, 1)
        );
    }

    #[test]
    fn chunk_ids_depend_on_chunker_revision_and_range() {
        let (ik, k) = (id_key(1), key(None));
        let a = ChunkId::derive(&ik, &k, "text.structural", 1, 0, 10);
        assert_eq!(a, ChunkId::derive(&ik, &k, "text.structural", 1, 0, 10));
        assert_ne!(a, ChunkId::derive(&ik, &k, "text.structural", 2, 0, 10));
        assert_ne!(a, ChunkId::derive(&ik, &k, "text.structural", 1, 0, 11));
    }

    /// Pins the derivations. If these change, every stored ID changes, so a
    /// failure here means the change needs a new domain tag and a migration.
    #[test]
    fn derivations_are_pinned() {
        let ik = id_key(7);
        assert_eq!(
            EventId::derive(&ik, &key(None)).to_string(),
            "891b8624-f54f-8aba-963f-48248ed75a46"
        );
        assert_eq!(
            ChunkId::derive(&ik, &key(None), "text.structural", 1, 0, 10).to_string(),
            "825f295e-14fe-8c86-bf2b-5e10f1489ece"
        );
    }

    #[test]
    fn display_round_trips() {
        let id = ChunkId::derive(&id_key(1), &key(None), "c", 1, 0, 1);
        assert_eq!(id, id.to_string().parse().unwrap());
        assert_eq!(BeadId::from(id).0, id.0);
    }

    #[test]
    fn the_key_never_prints() {
        assert_eq!(format!("{:?}", id_key(9)), "IdKey(<redacted>)");
    }
}

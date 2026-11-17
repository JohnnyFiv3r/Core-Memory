//! Typed outcomes for admission (ING-03) and classification (CLS-03).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::event::FieldError;
use crate::hash::Sha256Digest;
use crate::ids::{ChunkId, EventId};

/// The result of offering one SourceEvent to the engine.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum AdmissionOutcome {
    /// Newly admitted. The event, its chunks and its beads are stored.
    Accepted {
        event_id: EventId,
        chunk_ids: Vec<ChunkId>,
        /// Set when the engine assigned a new revision to an edited event
        /// from a source whose profile declares it editable (ING-04).
        #[serde(default, skip_serializing_if = "Option::is_none")]
        assigned_revision: Option<String>,
        /// The reader's stored position, if the event carried a checkpoint.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        checkpoint: Option<CheckpointReceipt>,
    },
    /// Already admitted with identical content. No event was written.
    Replay {
        event_id: EventId,
        chunk_ids: Vec<ChunkId>,
        /// The reader's stored position, if the event carried a checkpoint.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        checkpoint: Option<CheckpointReceipt>,
    },
    /// Already admitted with different content, and the source is not
    /// declared editable.
    IdentityConflict {
        event_id: EventId,
        stored_hash: Sha256Digest,
        offered_hash: Sha256Digest,
    },
    /// The event's checkpoint does not follow what is stored. Nothing was
    /// written. The reader should resume from the stored position.
    CheckpointConflict {
        key: String,
        /// 0 when no checkpoint is stored under the key.
        stored_sequence: u64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stored_hash: Option<Sha256Digest>,
    },
    /// Permanently refused. Retrying the same event will not help.
    Refused {
        reason: RefusalReason,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        problems: Vec<FieldError>,
    },
    /// Transiently refused. Retry after the given delay. Hosts must not
    /// count this against a terminal retry budget (OPS-05).
    Busy {
        retry_after_ms: u64,
        cause: BusyCause,
    },
}

impl AdmissionOutcome {
    pub fn refused(reason: RefusalReason) -> Self {
        AdmissionOutcome::Refused {
            reason,
            problems: Vec::new(),
        }
    }

    pub fn refused_with(reason: RefusalReason, field: &str, problem: impl Into<String>) -> Self {
        AdmissionOutcome::Refused {
            reason,
            problems: vec![FieldError::new(field, problem)],
        }
    }
}

/// A reader's position as the store now holds it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CheckpointReceipt {
    pub key: String,
    pub sequence: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RefusalReason {
    /// The event was erased. Its key is not admitted again.
    Tombstoned,
    /// The caller holds no grant for the event's scope, or the scope is not
    /// enrolled.
    ScopeDenied,
    /// The scope is paused or revoked. Nothing is captured for it.
    ScopeNotActive,
    /// The event or one of its records exceeds a size limit.
    Oversize,
    /// The named profile does not exist or cannot admit this event.
    InvalidProfile,
    /// The event fails validation: bad fields, duplicate JSON keys,
    /// content that does not match the profile.
    Malformed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BusyCause {
    /// The engine or its store cannot take the event right now.
    Capacity,
}

/// A pinned reference to a type in the configured vocabulary.
#[derive(
    Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct TypeRef {
    pub key: String,
    pub rev: u32,
}

/// Why a model gave a chunk no type. An abstention is a completed
/// judgment, not a failure: it is stored, and it is never retried as if the
/// model had not answered. Values match memoriesQL's classification
/// abstentions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Abstention {
    /// None of the types describes the chunk.
    NoFit,
    /// The chunk is too short or unclear to tell.
    InsufficientEvidence,
    /// Two or more types fit equally well.
    Ambiguous,
}

impl Abstention {
    pub const ALL: [Abstention; 3] = [
        Abstention::NoFit,
        Abstention::InsufficientEvidence,
        Abstention::Ambiguous,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Abstention::NoFit => "no_fit",
            Abstention::InsufficientEvidence => "insufficient_evidence",
            Abstention::Ambiguous => "ambiguous",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|a| a.as_str() == name)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn outcomes_serialize_with_tags() {
        let busy = AdmissionOutcome::Busy {
            retry_after_ms: 500,
            cause: BusyCause::Capacity,
        };
        assert_eq!(
            serde_json::to_value(&busy).unwrap(),
            serde_json::json!({"outcome": "busy", "retry_after_ms": 500, "cause": "capacity"})
        );
        let conflict = AdmissionOutcome::CheckpointConflict {
            key: "session-1".into(),
            stored_sequence: 0,
            stored_hash: None,
        };
        assert_eq!(
            serde_json::to_value(&conflict).unwrap(),
            serde_json::json!({"outcome": "checkpoint_conflict", "key": "session-1", "stored_sequence": 0})
        );
    }

    #[test]
    fn abstentions_have_one_name_each() {
        for abstention in Abstention::ALL {
            assert_eq!(Abstention::from_name(abstention.as_str()), Some(abstention));
            assert_eq!(
                serde_json::to_value(abstention).unwrap(),
                serde_json::json!(abstention.as_str())
            );
        }
        assert_eq!(Abstention::from_name("unsure"), None);
    }
}

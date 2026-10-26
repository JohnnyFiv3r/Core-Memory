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
    /// Newly admitted. Its records are in the outbox.
    Accepted {
        event_id: EventId,
        chunk_ids: Vec<ChunkId>,
        /// Set when the engine assigned a new revision to an edited event
        /// from a source whose profile declares it editable (ING-04).
        #[serde(default, skip_serializing_if = "Option::is_none")]
        assigned_revision: Option<String>,
    },
    /// Already admitted with identical content. Nothing new was written.
    Replay {
        event_id: EventId,
        chunk_ids: Vec<ChunkId>,
    },
    /// Already admitted with different content, and the source is not
    /// declared editable.
    IdentityConflict {
        event_id: EventId,
        stored_hash: Sha256Digest,
        offered_hash: Sha256Digest,
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RefusalReason {
    /// The event key was erased. Re-admission needs an approver.
    Tombstoned,
    /// The caller holds no grant for the event's scope.
    ScopeDenied,
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
    Capacity,
    SinkLagging,
}

/// A pinned reference to a type in the configured vocabulary.
#[derive(
    Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct TypeRef {
    pub key: String,
    pub rev: u32,
}

/// The type outcome of one chunk.
///
/// Only `assigned` and `unassigned` are completed decisions. A `pending`
/// outcome becomes a completed one exactly once (CLS-04).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum TypeOutcome {
    Assigned { type_ref: TypeRef },
    Unassigned { reason: UnassignedReason },
    Pending { reason: PendingReason },
}

impl TypeOutcome {
    pub fn is_final(&self) -> bool {
        !matches!(self, TypeOutcome::Pending { .. })
    }
}

/// Why a completed decision assigned no type. Values match memoriesQL's
/// classification abstentions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum UnassignedReason {
    NoFit,
    InsufficientEvidence,
    Ambiguous,
}

/// Why no completed decision exists yet. Pending outcomes are retryable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PendingReason {
    Timeout,
    ProviderError,
    Capacity,
    UnsupportedInput,
    /// No rule matched and no classifier binding is configured. The chunk
    /// is classified when a binding is added.
    NoClassifier,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn outcomes_serialize_with_tags() {
        let pending = TypeOutcome::Pending {
            reason: PendingReason::NoClassifier,
        };
        assert_eq!(
            serde_json::to_value(&pending).unwrap(),
            serde_json::json!({"status": "pending", "reason": "no_classifier"})
        );
        let busy = AdmissionOutcome::Busy {
            retry_after_ms: 500,
            cause: BusyCause::SinkLagging,
        };
        assert_eq!(
            serde_json::to_value(&busy).unwrap(),
            serde_json::json!({"outcome": "busy", "retry_after_ms": 500, "cause": "sink_lagging"})
        );
    }

    #[test]
    fn only_pending_is_not_final() {
        assert!(
            TypeOutcome::Unassigned {
                reason: UnassignedReason::NoFit
            }
            .is_final()
        );
        assert!(
            !TypeOutcome::Pending {
                reason: PendingReason::Timeout
            }
            .is_final()
        );
    }
}

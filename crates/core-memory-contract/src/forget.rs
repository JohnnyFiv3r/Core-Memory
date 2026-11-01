//! Forgetting chunks a system of record erased (PRD §13, safeguard 8).
//!
//! With a system of record such as memoriesQL attached, deletion is governed
//! there, not in the engine. When it erases chunks it tells the engine to
//! forget them, and the engine drops what it still holds of each: text
//! waiting for a model type, records not yet delivered to every sink, and
//! delivery failure details that could quote them. Once every chunk of an
//! event is forgotten its key is tombstoned: the plain key is cleared, the
//! keyed hash is kept, and offering the event again is refused. There are no
//! plans, limits or approvals here; the decision was made upstream.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::event::FieldError;
use crate::ids::ChunkId;

/// The most chunks one request may name.
pub const MAX_FORGET_CHUNKS: usize = 10_000;

/// Body of a request to forget chunks.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ForgetRequest {
    /// Exact chunk IDs. There are no filters.
    pub chunk_ids: Vec<ChunkId>,
}

/// Why a forget request was refused. Nothing was forgotten.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ForgetRefusal {
    /// No chunks, or a chunk named twice.
    Malformed,
    /// More than [`MAX_FORGET_CHUNKS`] chunks. Split the request.
    TooManyChunks,
    /// The caller holds no grant for a chunk's scope.
    ScopeDenied,
    /// The caller may not ask the engine to forget anything.
    NotPermitted,
}

/// The answer to a forget request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum ForgetOutcome {
    Forgotten {
        /// Chunks this request forgot.
        forgotten: u32,
        /// Chunks an earlier request forgot already.
        already_forgotten: u32,
        /// Chunks the engine never held. There is nothing to forget for them.
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        unknown: Vec<ChunkId>,
        /// Chunk texts that were still waiting for a model type.
        queued_dropped: u32,
        /// Records that had not reached every sink yet.
        records_dropped: u32,
        /// Events whose chunks are now all forgotten. Their keys are
        /// tombstoned.
        events_tombstoned: u32,
    },
    Refused {
        reason: ForgetRefusal,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        problems: Vec<FieldError>,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requests_take_exact_chunk_ids_only() {
        let ok: ForgetRequest =
            serde_json::from_str(r#"{"chunk_ids":["00000000-0000-8000-8000-000000000000"]}"#)
                .unwrap();
        assert_eq!(ok.chunk_ids.len(), 1);
        assert!(
            serde_json::from_str::<ForgetRequest>(r#"{"chunk_ids":[],"scope":"all"}"#).is_err()
        );
    }
}

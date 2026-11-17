//! Wire contracts for the Core Memory engine.
//!
//! This crate defines what flows in (SourceEvent), what the engine answers
//! (typed admission and type outcomes) and the public form of what it
//! stores (the versioned Record envelope). It depends on no other crate in
//! the workspace.

pub mod event;
pub mod hash;
pub mod ids;
pub mod json;
pub mod outcome;
pub mod record;
pub mod schema;
pub mod timestamp;

pub use event::{
    Actor, Checkpoint, Content, ContentKind, CorrelationKey, DocumentPosition, EventKey,
    FieldError, ObjectRef, OccurredAt, RecordObject, SourceEvent, Structure, TimePrecision,
};
pub use hash::{FieldHasher, Sha256Digest};
pub use ids::{BeadId, ChunkId, DecisionId, EventId, IdKey};
pub use outcome::{
    AdmissionOutcome, BusyCause, CheckpointReceipt, PendingReason, RefusalReason, TypeOutcome,
    TypeRef, UnassignedReason,
};
pub use record::{
    CatalogRef, ChunkFacets, ChunkRecord, ChunkerRef, EventDetails, EventRecord, IdentifierMatch,
    Lengths, ProfileRef, RECORD_CONTRACT, Record, RecordBody, RecordKind, SourceType, TextSpan,
    TimeBasis,
};
pub use timestamp::Timestamp;

//! Wire contracts for the Core Memory engine.
//!
//! This crate defines what flows in (SourceEvent), what the engine answers
//! (typed admission and type outcomes) and what flows out (the versioned
//! Record envelope). It depends on no other crate in the workspace.

pub mod event;
pub mod hash;
pub mod ids;
pub mod outcome;
pub mod record;
pub mod schema;
pub mod timestamp;

pub use event::{
    Actor, Content, ContentKind, CorrelationKey, DocumentPosition, EventKey, FieldError, ObjectRef,
    OccurredAt, RecordObject, SourceEvent, Structure, TimePrecision,
};
pub use hash::{FieldHasher, Sha256Digest};
pub use ids::{ChunkId, DecisionId, EventId};
pub use outcome::{
    AdmissionOutcome, BusyCause, PendingReason, RefusalReason, TypeOutcome, TypeRef,
    UnassignedReason,
};
pub use record::{
    CatalogRef, Catalogs, ChunkBody, ChunkFacets, ChunkRecord, ChunkerRef, Decision,
    DecisionMethod, EventInfo, IdentifierMatch, Lengths, Memory, ProfileRef, RECORD_CONTRACT_V1,
    Record, RecordBody, RecordKind, RelationshipStage, SourceType, Stage, TextSpan,
    TypeDecisionRecord,
};
pub use timestamp::Timestamp;

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
pub mod recall;
pub mod record;
pub mod relation;
pub mod schema;
pub mod timestamp;

pub use event::{
    Actor, Checkpoint, Content, ContentKind, CorrelationKey, DocumentPosition, EventKey,
    FieldError, ObjectRef, OccurredAt, RecordObject, SourceEvent, Structure, TimePrecision,
    ToolCall,
};
pub use hash::{FieldHasher, Sha256Digest};
pub use ids::{BeadId, CallId, ChunkId, EventId, IdKey};
pub use outcome::{
    Abstention, AdmissionOutcome, BusyCause, CheckpointReceipt, RefusalReason, TypeRef,
};
pub use recall::{
    MatchedBy, RECALL_DEFAULT_FOLLOW, RECALL_DEFAULT_LIMIT, RECALL_MAX_FOLLOW, RECALL_MAX_LIMIT,
    RECALL_MAX_LINKED, RECALL_MAX_QUERY_BYTES, RECALL_MAX_SCOPES, RECALL_MAX_TEXT_BYTES,
    RecallAnswer, RecallCompared, RecallFound, RecallMatch, RecallMeaning, RecallRequest,
    RecallSearch, RecallVia, RecallWords, RecalledArrow, RecalledBead, RecalledChunk,
    RecalledEvent,
};
pub use record::{
    CatalogRef, ChunkFacets, ChunkReadRecord, ChunkRecord, ChunkerRef, CompressorRef, EntityKind,
    ErasureRecord, EventDetails, EventRecord, IdentifierMatch, Lengths, MAX_ACTION_CHARS,
    MAX_OUTCOME_CHARS, ProfileRef, RECORD_CONTRACT, ReadDecision, ReadSpan, ReaderRef, Record,
    RecordBody, RecordKind, SourceType, SpanKind, TextSpan, TimeBasis, ToolCallDetails,
    ToolOutcomeRecord, ToolStatus,
};
pub use relation::{
    Arrow, ArrowOrigin, BeadKind, BeadRecord, COLUMNS_REV, Direction, EdgeRecord, EdgeType,
    JudgeRef, MadeBead, NameLinkRecord, PairColumns, Verdict, VerdictNote, VerdictRecord,
};
pub use timestamp::Timestamp;

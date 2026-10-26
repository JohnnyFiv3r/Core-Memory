//! The versioned Record envelope delivered to sinks (REC-01..03).
//!
//! Chunk records are emitted once. Later type decisions arrive as separate
//! append records keyed by chunk. A full view of a memory is something each
//! consumer builds for itself.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::event::{Actor, ContentKind, EventKey, OccurredAt, Structure};
use crate::hash::Sha256Digest;
use crate::ids::{ChunkId, DecisionId, EventId};
use crate::outcome::TypeOutcome;
use crate::timestamp::Timestamp;

/// Contract identifier carried by every v1 record.
pub const RECORD_CONTRACT_V1: &str = "core_memory.record.v1";

/// One record as delivered to a sink.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Record {
    /// Always [`RECORD_CONTRACT_V1`] for this version.
    pub contract: String,
    /// Idempotency key. A sink that has accepted a key must treat a second
    /// delivery of it as already accepted.
    pub record_key: String,
    pub scope: String,
    #[serde(flatten)]
    pub body: RecordBody,
}

impl Record {
    pub fn kind(&self) -> RecordKind {
        match &self.body {
            RecordBody::Chunk(_) => RecordKind::Chunk,
            RecordBody::TypeDecision(_) => RecordKind::TypeDecision,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "record_kind", rename_all = "snake_case")]
pub enum RecordBody {
    Chunk(Box<ChunkRecord>),
    TypeDecision(Box<TypeDecisionRecord>),
}

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum RecordKind {
    Chunk,
    TypeDecision,
}

/// A chunk: the memory unit, with its event, facets and type outcome.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ChunkRecord {
    pub event: EventInfo,
    pub memory: Memory,
    pub facets: ChunkFacets,
    pub source_profile: ProfileRef,
    pub chunker: ChunkerRef,
    pub catalogs: Catalogs,
    /// Provenance of the type outcome. Absent while the outcome is pending.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decision: Option<Decision>,
    pub relationships: RelationshipStage,
}

/// The source event a chunk came from, as the source declared it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EventInfo {
    pub event_id: EventId,
    pub key: EventKey,
    /// True when the engine assigned `key.revision` to an edited event (ING-04).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub engine_assigned_revision: bool,
    /// Hash used for the identity check: content plus declared metadata.
    pub event_hash: Sha256Digest,
    /// SHA-256 of the exact content bytes (canonical JSON for JSON content).
    pub content_hash: Sha256Digest,
    pub content_kind: ContentKind,
    pub source_type: SourceType,
    pub source_system: String,
    pub media_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occurred_at: Option<OccurredAt>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub actor: Option<Actor>,
    #[serde(default, skip_serializing_if = "Structure::is_empty")]
    pub structure: Structure,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub attributes: BTreeMap<String, String>,
    /// Unknown SourceEvent fields, unchanged.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub extra: BTreeMap<String, serde_json::Value>,
    pub parser_contract_version: String,
    pub captured_at: Timestamp,
    /// How many chunks the event produced. A sink that needs a whole event
    /// at once (memoriesQL) can wait for this many.
    pub chunk_count: u32,
}

/// Kind of source. Values match memoriesQL `SourceType`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum SourceType {
    Transcript,
    Document,
    Media,
    Relational,
    Operational,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Memory {
    pub chunk: ChunkBody,
    #[serde(rename = "type")]
    pub type_outcome: TypeOutcome,
}

/// The chunk itself. `text` is the exact source slice.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ChunkBody {
    pub chunk_id: ChunkId,
    /// Position within the event under this chunker, from 0.
    pub ordinal: u32,
    /// Byte range in the event content.
    pub byte_start: u64,
    pub byte_end: u64,
    /// Unicode scalar (code point) range in the event content.
    pub char_start: u64,
    pub char_end: u64,
    /// SHA-256 of `text` as UTF-8. Matches memoriesQL `SourceUnitInput.content_hash`.
    pub content_hash: Sha256Digest,
    pub text: String,
}

/// Deterministic observations about a chunk (FAC-01, FAC-02). Never model
/// output, never generated text. Offsets are bytes within the chunk text.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ChunkFacets {
    pub lengths: Lengths,
    /// ISO 639-3 code, present only when detection is reliable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub urls: Vec<TextSpan>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub identifiers: Vec<IdentifierMatch>,
    /// True when a facet list hit its per-chunk limit.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub truncated: bool,
    /// Extractor name to extractor revision.
    pub extractors: BTreeMap<String, u32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Lengths {
    pub bytes: u64,
    pub code_points: u64,
    pub lines: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct TextSpan {
    pub value: String,
    pub byte_start: u64,
    pub byte_end: u64,
}

/// An identifier matched by a pattern the source profile declares.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct IdentifierMatch {
    pub kind: String,
    pub value: String,
    pub byte_start: u64,
    pub byte_end: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
pub struct ProfileRef {
    pub id: String,
    pub rev: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ChunkerRef {
    pub id: String,
    pub rev: u32,
    /// Hash of the chunker settings, for audit. Changing settings requires a
    /// new `rev`; the engine refuses to start otherwise.
    pub config_hash: Sha256Digest,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CatalogRef {
    pub id: String,
    pub hash: Sha256Digest,
}

/// The vocabularies decisions were made against.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Catalogs {
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    pub type_catalog: Option<CatalogRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub predicate: Option<CatalogRef>,
}

/// Decision provenance (MOD-03). A reference, never a rationale or a
/// confidence score.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Decision {
    pub decision_id: DecisionId,
    #[serde(flatten)]
    pub method: DecisionMethod,
    pub decided_at: Timestamp,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "method", rename_all = "snake_case")]
pub enum DecisionMethod {
    /// A source-profile rule decided.
    Rule {
        profile: ProfileRef,
        rule_id: String,
    },
    /// A model binding decided.
    Model {
        binding_ref: String,
        binding_rev: Sha256Digest,
    },
}

/// Whether relationship judging applies to this record (REL-01). An empty
/// relationship list is never proof that nothing is related.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RelationshipStage {
    pub stage: Stage,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binding_ref: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Off,
    On,
    Skipped,
}

/// A completed type decision for a chunk whose chunk record said `pending`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct TypeDecisionRecord {
    pub chunk_id: ChunkId,
    pub event_id: EventId,
    pub outcome: TypeOutcome,
    pub decision: Decision,
    pub catalogs: Catalogs,
}

/// Idempotency key of a chunk record.
pub fn chunk_record_key(chunk_id: &ChunkId) -> String {
    format!("chunk:{chunk_id}")
}

/// Idempotency key of a type decision record. One per chunk: a pending
/// type completes exactly once.
pub fn type_decision_record_key(chunk_id: &ChunkId) -> String {
    format!("type:{chunk_id}")
}

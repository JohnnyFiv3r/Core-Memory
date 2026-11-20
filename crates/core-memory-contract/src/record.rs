//! The versioned Record envelope: the public JSON form of what the engine
//! stores (REC-01..03).
//!
//! Each record is one primitive. An event record carries what the source
//! declared; a chunk record carries one exact slice of its content. Records
//! are written once and never change. A full view of a memory is something
//! each consumer builds for itself.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::event::{Actor, ContentKind, EventKey, OccurredAt, Structure};
use crate::hash::Sha256Digest;
use crate::ids::{ChunkId, EventId};
use crate::timestamp::Timestamp;

/// Contract identifier carried by every v2 record.
pub const RECORD_CONTRACT: &str = "core_memory.record.v2";

/// One record.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Record {
    /// Always [`RECORD_CONTRACT`] for this version.
    pub contract: String,
    /// Unique per record. A consumer that has taken a key can drop a second
    /// record with the same key.
    pub record_key: String,
    pub scope: String,
    #[serde(flatten)]
    pub body: RecordBody,
}

impl Record {
    pub fn event(event: EventRecord) -> Self {
        Self {
            contract: RECORD_CONTRACT.to_string(),
            record_key: event_record_key(&event.event_id),
            scope: event.key.scope.clone(),
            body: RecordBody::Event(Box::new(event)),
        }
    }

    pub fn chunk(scope: impl Into<String>, chunk: ChunkRecord) -> Self {
        Self {
            contract: RECORD_CONTRACT.to_string(),
            record_key: chunk_record_key(&chunk.chunk_id),
            scope: scope.into(),
            body: RecordBody::Chunk(Box::new(chunk)),
        }
    }

    pub fn kind(&self) -> RecordKind {
        match &self.body {
            RecordBody::Event(_) => RecordKind::Event,
            RecordBody::Chunk(_) => RecordKind::Chunk,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "record_kind", rename_all = "snake_case")]
pub enum RecordBody {
    Event(Box<EventRecord>),
    Chunk(Box<ChunkRecord>),
}

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum RecordKind {
    Event,
    Chunk,
}

/// An admitted source event, as the source declared it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EventRecord {
    pub event_id: EventId,
    pub workspace: String,
    /// The workspace's change counter when the event was admitted. Within
    /// a workspace, rows commit in `seq` order, so a reader that pages by
    /// `seq` greater than the last one it saw misses nothing.
    pub seq: u64,
    pub key: EventKey,
    /// True when the engine assigned `key.revision` to an edited event (ING-04).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub engine_assigned_revision: bool,
    /// Hash used for the identity check: content plus declared metadata.
    pub event_hash: Sha256Digest,
    /// SHA-256 of the exact content bytes (canonical JSON for JSON content).
    pub content_hash: Sha256Digest,
    #[serde(flatten)]
    pub details: EventDetails,
    pub source_profile: ProfileRef,
    pub chunker: ChunkerRef,
    /// When the engine admitted the event.
    pub captured_at: Timestamp,
    /// The time used to order the event, and where it came from.
    pub time_at: Timestamp,
    pub time_basis: TimeBasis,
    /// How many chunks the event produced.
    pub chunk_count: u32,
}

/// What the source declared about an event, apart from its key and content.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EventDetails {
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
}

/// Where an event's ordering time came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TimeBasis {
    /// The source gave an instant, to the second or better.
    Source,
    /// The source gave a coarser time; `time_at` is the start of that period.
    SourceBucket,
    /// The source gave no usable time; `time_at` is when the engine admitted
    /// the event.
    Captured,
}

impl TimeBasis {
    pub fn as_str(self) -> &'static str {
        match self {
            TimeBasis::Source => "source",
            TimeBasis::SourceBucket => "source_bucket",
            TimeBasis::Captured => "captured",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        [
            TimeBasis::Source,
            TimeBasis::SourceBucket,
            TimeBasis::Captured,
        ]
        .into_iter()
        .find(|b| b.as_str() == name)
    }
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

/// A chunk: the memory unit. `text` is the exact source slice.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ChunkRecord {
    pub chunk_id: ChunkId,
    pub event_id: EventId,
    /// Position within the event under its chunker, from 0.
    pub ordinal: u32,
    /// Byte range in the event content.
    pub byte_start: u64,
    pub byte_end: u64,
    /// Unicode scalar (code point) range in the event content.
    pub char_start: u64,
    pub char_end: u64,
    /// SHA-256 of `text` as UTF-8.
    pub content_hash: Sha256Digest,
    pub text: String,
    pub facets: ChunkFacets,
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

/// A vocabulary a decision was made against.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CatalogRef {
    pub id: String,
    pub hash: Sha256Digest,
}

/// Key of an event record.
pub fn event_record_key(event_id: &EventId) -> String {
    format!("event:{event_id}")
}

/// Key of a chunk record.
pub fn chunk_record_key(chunk_id: &ChunkId) -> String {
    format!("chunk:{chunk_id}")
}

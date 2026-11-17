//! The versioned Record envelope: the public JSON form of what the engine
//! stores (REC-01..03).
//!
//! Each record is one primitive. An event record carries what the source
//! declared; a chunk record carries one exact slice of its content; a
//! chunk-read record carries what a model read in a chunk; a tool-outcome
//! record carries what a model read in a tool call; an erasure record says
//! an event is gone. Records are written once and never change.
//! A full view of a memory is something each consumer builds for itself.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::event::{Actor, ContentKind, EventKey, OccurredAt, Structure};
use crate::hash::Sha256Digest;
use crate::ids::{BeadId, CallId, ChunkId, EventId};
use crate::outcome::{Abstention, TypeRef};
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

    pub fn chunk_read(scope: impl Into<String>, read: ChunkReadRecord) -> Self {
        Self {
            contract: RECORD_CONTRACT.to_string(),
            record_key: format!("chunk_read:{}:{}", read.chunk_id, read.seq),
            scope: scope.into(),
            body: RecordBody::ChunkRead(Box::new(read)),
        }
    }

    pub fn tool_outcome(scope: impl Into<String>, outcome: ToolOutcomeRecord) -> Self {
        Self {
            contract: RECORD_CONTRACT.to_string(),
            record_key: format!("tool_outcome:{}:{}", outcome.event_id, outcome.seq),
            scope: scope.into(),
            body: RecordBody::ToolOutcome(Box::new(outcome)),
        }
    }

    pub fn erasure(scope: impl Into<String>, erasure: ErasureRecord) -> Self {
        Self {
            contract: RECORD_CONTRACT.to_string(),
            record_key: format!("erasure:{}", erasure.event_id),
            scope: scope.into(),
            body: RecordBody::Erasure(Box::new(erasure)),
        }
    }

    pub fn kind(&self) -> RecordKind {
        match &self.body {
            RecordBody::Event(_) => RecordKind::Event,
            RecordBody::Chunk(_) => RecordKind::Chunk,
            RecordBody::ChunkRead(_) => RecordKind::ChunkRead,
            RecordBody::ToolOutcome(_) => RecordKind::ToolOutcome,
            RecordBody::Erasure(_) => RecordKind::Erasure,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "record_kind", rename_all = "snake_case")]
pub enum RecordBody {
    Event(Box<EventRecord>),
    Chunk(Box<ChunkRecord>),
    ChunkRead(Box<ChunkReadRecord>),
    ToolOutcome(Box<ToolOutcomeRecord>),
    Erasure(Box<ErasureRecord>),
}

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum RecordKind {
    Event,
    Chunk,
    ChunkRead,
    ToolOutcome,
    Erasure,
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
    /// For a tool call: the tool, and where its arguments end in the text.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call: Option<ToolCallDetails>,
    /// Unknown SourceEvent fields, unchanged.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub extra: BTreeMap<String, serde_json::Value>,
    pub parser_contract_version: String,
}

/// What a tool-call event declared, apart from its text.
///
/// The text of a tool call is its arguments as canonical JSON, a line
/// feed, then its output. The first `input_bytes` bytes are the arguments,
/// and the output starts one byte after them.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ToolCallDetails {
    pub tool: String,
    pub input_bytes: u64,
    /// Whether the tool reported that the call failed, if it said.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
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

/// What a model read in one chunk: its type, or why the model gave none,
/// and the entities, actions and outcomes it found there.
///
/// A chunk may be read more than once, by another model or prompt. Each
/// read is its own record, and the one with the highest `seq` is current.
/// No text of the model's is stored: a type is a key of the configured
/// vocabulary, and a span is a byte range of the chunk's own text.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ChunkReadRecord {
    pub chunk_id: ChunkId,
    pub workspace: String,
    /// The workspace's change counter when the read was stored.
    pub seq: u64,
    #[serde(flatten)]
    pub decision: ReadDecision,
    /// The vocabulary the type was chosen from.
    pub vocabulary: CatalogRef,
    pub reader: ReaderRef,
    /// The call that produced the read. Its receipt names where the
    /// chunk's text was sent.
    pub call_id: CallId,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub spans: Vec<ReadSpan>,
    /// Spans the model offered that were not found in the chunk, and so
    /// were not stored.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub spans_rejected: u32,
    pub read_at: Timestamp,
}

fn is_zero(n: &u32) -> bool {
    *n == 0
}

/// The type a model gave a chunk, or its reason for giving none. Both are
/// completed judgments.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum ReadDecision {
    Typed {
        #[serde(rename = "type")]
        type_ref: TypeRef,
    },
    Abstained {
        reason: Abstention,
    },
}

/// The model and prompt that made a read.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ReaderRef {
    pub binding_id: String,
    /// Hash of everything that affects what the reader says: provider,
    /// endpoint, model, sampling settings, prompt, reply schema, validator.
    pub binding_rev: Sha256Digest,
    pub model: String,
    pub prompt_rev: u32,
}

/// Something a read found in a chunk: a byte range of the chunk's text,
/// counted from the start of the chunk.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
pub struct ReadSpan {
    #[serde(flatten)]
    pub kind: SpanKind,
    pub byte_start: u64,
    pub byte_end: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SpanKind {
    /// A named person, organization, place, product, project, artifact or
    /// event the chunk mentions.
    Entity { entity_kind: EntityKind },
    /// Something someone did, is doing or commits to doing.
    Action,
    /// A result that happened or a state that was reached.
    Outcome,
}

impl SpanKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            SpanKind::Entity { .. } => "entity",
            SpanKind::Action => "action",
            SpanKind::Outcome => "outcome",
        }
    }

    pub fn entity_kind(&self) -> Option<EntityKind> {
        match self {
            SpanKind::Entity { entity_kind } => Some(*entity_kind),
            SpanKind::Action | SpanKind::Outcome => None,
        }
    }

    /// The kind a stored `kind` and `entity_kind` name, if they agree.
    pub fn from_names(kind: &str, entity_kind: Option<&str>) -> Option<Self> {
        match (kind, entity_kind) {
            ("entity", Some(name)) => {
                EntityKind::from_name(name).map(|entity_kind| SpanKind::Entity { entity_kind })
            }
            ("action", None) => Some(SpanKind::Action),
            ("outcome", None) => Some(SpanKind::Outcome),
            _ => None,
        }
    }
}

/// A coarse, closed set of entity kinds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EntityKind {
    Person,
    Organization,
    Place,
    Product,
    Project,
    /// A document, file, ticket, message, code object or other made thing.
    Artifact,
    /// A meeting, release, incident or other thing that happens.
    Event,
    Other,
}

impl EntityKind {
    pub const ALL: [EntityKind; 8] = [
        EntityKind::Person,
        EntityKind::Organization,
        EntityKind::Place,
        EntityKind::Product,
        EntityKind::Project,
        EntityKind::Artifact,
        EntityKind::Event,
        EntityKind::Other,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            EntityKind::Person => "person",
            EntityKind::Organization => "organization",
            EntityKind::Place => "place",
            EntityKind::Product => "product",
            EntityKind::Project => "project",
            EntityKind::Artifact => "artifact",
            EntityKind::Event => "event",
            EntityKind::Other => "other",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|k| k.as_str() == name)
    }
}

/// Most characters of a tool outcome's `action`.
pub const MAX_ACTION_CHARS: usize = 120;
/// Most characters of a tool outcome's `outcome`.
pub const MAX_OUTCOME_CHARS: usize = 200;

/// What a model read in one tool call: what the call did, what came of it
/// and how it ended.
///
/// The model was shown the call's text after code shortened it by the
/// rules `compressor` names. `action` and `outcome` are the model's own
/// words, the one place the engine keeps text a model wrote. Readers that
/// must not see a tool's own text are given this record in its place.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ToolOutcomeRecord {
    /// The tool-call event.
    pub event_id: EventId,
    /// The tool call's bead. It has the event's ID.
    pub bead_id: BeadId,
    pub workspace: String,
    /// The workspace's change counter when the outcome was stored.
    pub seq: u64,
    pub status: ToolStatus,
    /// What the call did. At most [`MAX_ACTION_CHARS`] characters.
    pub action: String,
    /// What came of it. At most [`MAX_OUTCOME_CHARS`] characters.
    pub outcome: String,
    pub compressor: CompressorRef,
    /// Bytes of the shortened text the model was shown.
    pub compressed_bytes: u64,
    pub reader: ReaderRef,
    /// The call that produced the outcome. Its receipt names where the
    /// tool call's text was sent.
    pub call_id: CallId,
    pub read_at: Timestamp,
}

/// How a tool call ended, as a model read it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ToolStatus {
    /// The call did what it was asked to.
    Succeeded,
    /// The call did not do what it was asked to.
    Failed,
    /// The call did part of what it was asked to.
    Partial,
    /// The text does not say how the call ended. Not a failure.
    Unknown,
}

impl ToolStatus {
    pub const ALL: [ToolStatus; 4] = [
        ToolStatus::Succeeded,
        ToolStatus::Failed,
        ToolStatus::Partial,
        ToolStatus::Unknown,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            ToolStatus::Succeeded => "succeeded",
            ToolStatus::Failed => "failed",
            ToolStatus::Partial => "partial",
            ToolStatus::Unknown => "unknown",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|s| s.as_str() == name)
    }
}

/// The rules that shortened a tool call's text before a model read it.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
pub struct CompressorRef {
    pub id: String,
    pub rev: u32,
}

/// An event that was erased. A consumer that keeps anything built from the
/// event, its chunks or their reads removes it on seeing this.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ErasureRecord {
    pub event_id: EventId,
    pub workspace: String,
    /// The workspace's change counter when the event was erased.
    pub seq: u64,
    pub erased_at: Timestamp,
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

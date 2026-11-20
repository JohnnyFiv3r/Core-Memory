//! The store (OPS-01): the durable system of record.
//!
//! It holds scopes and their state, events, chunks and their text, beads,
//! reader checkpoints and profile pins. What admission commits here is the
//! record: there is no outbox and no delivery step. The store exposes no
//! retrieval API; consumers read its tables or its export.
//!
//! The engine's guarantees are transactional, so there is one real
//! implementation, on PostgreSQL (`core-memory-store-postgres`), and no
//! in-memory stand-in.

use core_memory_contract::{
    Checkpoint, CheckpointReceipt, ChunkFacets, ChunkId, ChunkRecord, ChunkerRef, EventDetails,
    EventId, EventKey, EventRecord, IdKey, ProfileRef, Sha256Digest, TimeBasis, Timestamp,
};

use crate::error::StoreError;

/// Whether a scope takes new events.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ScopeState {
    Active,
    /// Capture is stopped for now. Nothing is admitted until it is resumed.
    Paused,
    /// The connection was withdrawn. Nothing is admitted.
    Revoked,
}

impl ScopeState {
    pub fn as_str(self) -> &'static str {
        match self {
            ScopeState::Active => "active",
            ScopeState::Paused => "paused",
            ScopeState::Revoked => "revoked",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        [ScopeState::Active, ScopeState::Paused, ScopeState::Revoked]
            .into_iter()
            .find(|s| s.as_str() == name)
    }
}

/// An enrolled scope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopeInfo {
    pub workspace: String,
    pub state: ScopeState,
    /// The workspace's ID key. It never changes.
    pub id_key: IdKey,
}

/// What the store holds for an event ID.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StoredEvent {
    Live {
        event_hash: Sha256Digest,
        chunk_ids: Vec<ChunkId>,
    },
    /// Erased. The ID is all that is left, and it blocks re-admission.
    Erased,
}

/// Everything one admission writes, committed atomically.
#[derive(Debug, Clone)]
pub struct AdmissionWrite<'a> {
    pub event_id: EventId,
    pub workspace: &'a str,
    pub key: &'a EventKey,
    pub engine_assigned_revision: bool,
    pub event_hash: Sha256Digest,
    pub content_hash: Sha256Digest,
    pub details: &'a EventDetails,
    pub profile: &'a ProfileRef,
    pub chunker: &'a ChunkerRef,
    pub chunks: &'a [ChunkWrite],
    pub time_at: Timestamp,
    pub time_basis: TimeBasis,
    pub captured_at: Timestamp,
    pub checkpoint: Option<&'a Checkpoint>,
    /// The first version of the record this event is an edit of. The edit
    /// is stored only if that event is still live when it commits.
    pub revises: Option<EventId>,
}

/// One chunk of an admission, with its text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkWrite {
    pub chunk_id: ChunkId,
    pub ordinal: u32,
    pub byte_start: u64,
    pub byte_end: u64,
    pub char_start: u64,
    pub char_end: u64,
    pub content_hash: Sha256Digest,
    pub text: String,
    pub facets: ChunkFacets,
}

/// How [`Store::commit_admission`] ended. Only `Committed` wrote anything.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AdmissionCommit {
    Committed {
        checkpoint: Option<CheckpointReceipt>,
    },
    /// The event ID is already stored: another admission of the same key
    /// committed first.
    AlreadyPresent(StoredEvent),
    /// The record this event edits was erased.
    RevisedEventErased,
    /// The scope is not active. `None` means it is not enrolled.
    ScopeNotActive(Option<ScopeState>),
    /// The offered checkpoint does not follow the stored one.
    CheckpointConflict(CheckpointState),
}

/// A reader's stored position. Sequence 0 with no hash means nothing is
/// stored under the key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckpointState {
    pub sequence: u64,
    pub hash: Option<Sha256Digest>,
}

/// How [`Store::advance_checkpoint`] ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CheckpointAdvance {
    /// The stored position now equals the offered one, either because this
    /// call moved it or because an earlier identical call did.
    Advanced(CheckpointReceipt),
    Conflict(CheckpointState),
    ScopeNotActive(Option<ScopeState>),
}

/// A live event with its chunks, as exported.
#[derive(Debug, Clone, PartialEq)]
pub struct ExportedEvent {
    pub event: EventRecord,
    pub chunks: Vec<ChunkRecord>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PinKind {
    SourceProfile,
    Chunker,
}

impl PinKind {
    pub fn as_str(self) -> &'static str {
        match self {
            PinKind::SourceProfile => "source_profile",
            PinKind::Chunker => "chunker",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PinResult {
    /// First time this (kind, id, rev) was seen. Now pinned.
    Pinned,
    Matches,
    /// The stored revision has different contents.
    Mismatch {
        stored: Sha256Digest,
    },
}

/// The durable store. Every method is one transaction.
pub trait Store {
    /// The scope's workspace, state and ID key, or `None` if not enrolled.
    fn scope(&mut self, scope: &str) -> Result<Option<ScopeInfo>, StoreError>;

    fn event(&mut self, event_id: &EventId) -> Result<Option<StoredEvent>, StoreError>;

    /// Writes the event, its chunks, their text and its beads, and advances
    /// the checkpoint if one is offered. The scope must be active, and the
    /// event it revises live, when the transaction commits. Writes nothing
    /// unless it returns `Committed`.
    fn commit_admission(
        &mut self,
        write: &AdmissionWrite<'_>,
    ) -> Result<AdmissionCommit, StoreError>;

    /// Advances a checkpoint without admitting anything: used when the
    /// event that carries it is a replay.
    fn advance_checkpoint(
        &mut self,
        scope: &str,
        checkpoint: &Checkpoint,
        at: Timestamp,
    ) -> Result<CheckpointAdvance, StoreError>;

    /// The stored position for `(scope, key)`, if any.
    fn checkpoint(&mut self, scope: &str, key: &str)
    -> Result<Option<CheckpointState>, StoreError>;

    /// Records that `(kind, id, rev)` has contents `hash`, or reports that
    /// it was pinned with different contents.
    fn pin(
        &mut self,
        kind: PinKind,
        id: &str,
        rev: u32,
        hash: &Sha256Digest,
    ) -> Result<PinResult, StoreError>;

    /// Up to `limit` live events of `workspace` whose `seq` is greater than
    /// `after`, in `seq` order, each with its chunks. `scope` narrows to one
    /// scope. Events commit in `seq` order within a workspace, so paging
    /// from the last `seq` returned misses nothing.
    fn export_events(
        &mut self,
        workspace: &str,
        after: u64,
        limit: u32,
        scope: Option<&str>,
    ) -> Result<Vec<ExportedEvent>, StoreError>;
}

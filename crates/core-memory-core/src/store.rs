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
    CallId, CatalogRef, Checkpoint, CheckpointReceipt, ChunkFacets, ChunkId, ChunkReadRecord,
    ChunkRecord, ChunkerRef, CompressorRef, ErasureRecord, EventDetails, EventId, EventKey,
    EventRecord, IdKey, ProfileRef, ReadDecision, ReadSpan, ReaderRef, Sha256Digest, TimeBasis,
    Timestamp, ToolCallDetails, ToolOutcomeRecord, ToolStatus,
};

use crate::error::StoreError;
use crate::model::{BudgetConfig, Usage};

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
    /// How the event is represented among beads.
    pub beads: BeadsOf,
    /// What a model is still to read in the event, if a reader for it is
    /// configured: each chunk, or the tool call as a whole.
    pub queue: Option<Purpose>,
}

/// How an event is represented among beads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BeadsOf {
    /// Each chunk is a bead, with the chunk's ID. A message, a document or
    /// a record.
    Chunks,
    /// The event is one bead, with the event's ID. A tool call.
    Event,
}

impl BeadsOf {
    /// How many beads an event of `chunks` chunks has.
    pub fn count(self, chunks: usize) -> usize {
        match self {
            BeadsOf::Chunks => chunks,
            BeadsOf::Event => 1,
        }
    }
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

/// One change to a workspace, as exported. Changes come in `seq` order.
#[derive(Debug, Clone, PartialEq)]
pub enum Change {
    /// An event was admitted. It is live when exported.
    Event(Box<ExportedEvent>),
    /// A model read a chunk. Reads of erased events are not exported.
    Read {
        scope: String,
        read: Box<ChunkReadRecord>,
    },
    /// A model read a tool call. Outcomes of erased events are not exported.
    ToolOutcome {
        scope: String,
        outcome: Box<ToolOutcomeRecord>,
    },
    /// An event was erased.
    Erasure {
        scope: String,
        erasure: ErasureRecord,
    },
}

impl Change {
    pub fn seq(&self) -> u64 {
        match self {
            Change::Event(e) => e.event.seq,
            Change::Read { read, .. } => read.seq,
            Change::ToolOutcome { outcome, .. } => outcome.seq,
            Change::Erasure { erasure, .. } => erasure.seq,
        }
    }
}

// ---- model calls ----

/// Chunks of one scope and one source profile, reserved for one worker
/// until the lease runs out.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadBatch {
    pub scope: String,
    pub workspace: String,
    pub profile: ProfileRef,
    pub chunks: Vec<LeasedChunk>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LeasedChunk {
    pub chunk_id: ChunkId,
    /// Times the chunk has been handed to a worker, this one included.
    pub attempts: u32,
}

/// A tool call reserved for one worker until the lease runs out.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeasedToolCall {
    pub scope: String,
    pub workspace: String,
    pub event_id: EventId,
    /// The event's chunks, in order. Together their text is the call's.
    pub chunk_ids: Vec<ChunkId>,
    /// The tool, and where its arguments end in the text.
    pub tool_call: ToolCallDetails,
    /// Times the call has been handed to a worker, this one included.
    pub attempts: u32,
}

/// What a model call is for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Purpose {
    /// Reading chunks: their type, entities, actions and outcomes.
    ChunkRead,
    /// Reading one tool call: what it did and what came of it.
    ToolRead,
}

impl Purpose {
    pub fn as_str(self) -> &'static str {
        match self {
            Purpose::ChunkRead => "chunk_read",
            Purpose::ToolRead => "tool_read",
        }
    }
}

/// Asks to send chunks of one scope to a model.
#[derive(Clone, Copy)]
pub struct PermitRequest<'a> {
    pub scope: &'a str,
    pub chunk_ids: &'a [ChunkId],
    pub purpose: Purpose,
    /// Where the text would go: the binding's endpoint host.
    pub destination: &'a str,
    pub reader: &'a ReaderRef,
    /// 1 for a first call, 2 for the repair of a rejected reply.
    pub attempt: u8,
    /// The call a repair repairs.
    pub repairs: Option<CallId>,
    /// What a call that sends these chunks will send and may cost. The
    /// store asks once it has read the text, inside the transaction that
    /// permits the call, and writes the answer on the receipt. `None`
    /// means the text cannot be sent as it is: nothing is permitted then,
    /// and no receipt is written.
    pub sending: &'a (dyn Fn(&[PermittedChunk]) -> Option<Sending> + Sync),
    pub budget: Option<BudgetConfig>,
    pub at: Timestamp,
}

impl std::fmt::Debug for PermitRequest<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PermitRequest")
            .field("scope", &self.scope)
            .field("chunk_ids", &self.chunk_ids)
            .field("purpose", &self.purpose)
            .field("destination", &self.destination)
            .field("attempt", &self.attempt)
            .finish_non_exhaustive()
    }
}

/// What a model call will send of the chunks it is permitted, and what it
/// may cost.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sending {
    /// Bytes of the chunks' text as it will be sent: all of it, or what is
    /// left of it once code has shortened it.
    pub bytes: u64,
    /// The tokens to count against the budget: the instructions, the text
    /// as it will be sent, and the most the reply may use.
    pub reserved_tokens: u64,
}

/// An estimate of the tokens in `bytes` of text, at three bytes to a token.
pub fn tokens_in(bytes: u64) -> u64 {
    bytes / 3
}

/// The answer to a [`PermitRequest`]. Only `Granted` wrote a receipt, and
/// only `Granted` hands out text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Permit {
    /// The scope is active and has granted the destination. The receipt is
    /// committed; these are the chunks to send. A chunk erased since it
    /// was queued is not among them.
    Granted {
        call_id: CallId,
        chunks: Vec<PermittedChunk>,
    },
    /// The scope is paused or revoked, or was never enrolled.
    ScopeNotActive(Option<ScopeState>),
    /// The scope has not granted the destination, or has revoked it.
    NoConsent,
    /// The binding's budget for the day is spent in this workspace.
    OverBudget,
    /// None of the chunks has text any more.
    NothingToSend,
    /// The text is stored and cannot be made into a request: it does not
    /// hold what its event says it holds. Asking again would find the same.
    Unsendable,
}

/// One chunk a permit hands out.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PermittedChunk {
    pub chunk_id: ChunkId,
    pub text: String,
    /// The conversational role of whoever wrote it, if the source said.
    pub role: Option<String>,
}

/// How a model call ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CallResult {
    /// The reply was valid.
    Accepted,
    /// The reply was not valid.
    Rejected,
    /// The model declined, or the request or its reply did not fit.
    Refused,
    /// The provider did not process the request.
    NotSent,
    /// The request may have been processed.
    InDoubt,
    /// The provider turned the request itself down.
    Failed,
}

impl CallResult {
    pub fn as_str(self) -> &'static str {
        match self {
            CallResult::Accepted => "accepted",
            CallResult::Rejected => "rejected",
            CallResult::Refused => "refused",
            CallResult::NotSent => "not_sent",
            CallResult::InDoubt => "in_doubt",
            CallResult::Failed => "failed",
        }
    }
}

/// What a model read in one chunk, to be stored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadWrite {
    pub chunk_id: ChunkId,
    pub decision: ReadDecision,
    pub spans: Vec<ReadSpan>,
    pub spans_rejected: u32,
}

/// What a model read in one tool call, to be stored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolOutcomeWrite {
    pub event_id: EventId,
    pub status: ToolStatus,
    pub action: String,
    pub outcome: String,
    pub compressor: CompressorRef,
    /// Bytes of the shortened text the model was shown.
    pub compressed_bytes: u64,
}

/// What a piece of queued work is about.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum WorkItem {
    /// A chunk to be read.
    Chunk(ChunkId),
    /// A tool-call event to be read.
    ToolCall(EventId),
}

impl WorkItem {
    /// The kind of work the item is queued for.
    pub fn purpose(self) -> Purpose {
        match self {
            WorkItem::Chunk(_) => Purpose::ChunkRead,
            WorkItem::ToolCall(_) => Purpose::ToolRead,
        }
    }
}

/// What becomes of an item's queued work.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkUpdate {
    pub item: WorkItem,
    pub next: WorkNext,
    /// A short code for why. Never model or source text.
    pub problem: &'static str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkNext {
    /// No call was made for it, so the attempt does not count. Offer it
    /// again not before the given time.
    Defer { not_before: Timestamp },
    /// A call was made and did not read it. Try again, not before the
    /// given time. With `alone`, it is never put in a batch again.
    Retry { not_before: Timestamp, alone: bool },
    /// Set aside for an operator.
    Failed,
}

/// Records how a call ended and what it produced, in one transaction.
#[derive(Debug, Clone)]
pub struct CallFinish<'a> {
    pub call_id: CallId,
    pub scope: &'a str,
    pub result: CallResult,
    pub usage: Option<Usage>,
    /// What to store of chunks read. Empty unless the reply was valid.
    pub reads: &'a [ReadWrite],
    /// What to store of a tool call read. `None` unless the reply was valid.
    pub tool_outcome: Option<&'a ToolOutcomeWrite>,
    /// The vocabulary the reads' types were chosen from. `None` only when
    /// there are no reads to store.
    pub vocabulary: Option<&'a CatalogRef>,
    pub reader: &'a ReaderRef,
    /// What to do with the work of chunks that were not read.
    pub work: &'a [WorkUpdate],
    pub at: Timestamp,
}

/// What [`Store::finish_call`] did.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CallFinished {
    /// Reads and tool outcomes stored.
    pub stored: u32,
    /// Reads and tool outcomes not stored: the event was erased, or the
    /// scope stopped being active, while the model was reading.
    pub dropped: u32,
}

/// Queue counts for one kind of work.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct WorkCounts {
    pub ready: u64,
    pub leased: u64,
    pub failed: u64,
    /// How many items carry each problem code.
    pub problems: Vec<(String, u64)>,
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

    /// Up to `limit` changes to `workspace` whose `seq` is greater than
    /// `after`, in `seq` order: live events with their chunks, reads of
    /// live events, and erasures. `scope` narrows to one scope. Rows commit
    /// in `seq` order within a workspace, so paging from the last `seq`
    /// returned misses nothing.
    fn export(
        &mut self,
        workspace: &str,
        after: u64,
        limit: u32,
        scope: Option<&str>,
    ) -> Result<Vec<Change>, StoreError>;

    // ---- model calls ----

    /// Reserves up to `limit` chunks that are due to be read, all of one
    /// scope and one source profile, until `lease_until`. `None` if nothing
    /// is due.
    ///
    /// A chunk is handed out `max_attempts` times at most. One that was
    /// handed out that often and never settled, because a worker died with
    /// it or could not record how its call ended, is set aside as failed
    /// instead of being handed out, and sent, once more.
    fn claim_reads(
        &mut self,
        limit: u32,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<ReadBatch>, StoreError>;

    /// Reserves one tool call that is due to be read, until `lease_until`.
    /// `None` if none is due. A call is handed out `max_attempts` times at
    /// most, as a chunk is.
    fn claim_tool_read(
        &mut self,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<LeasedToolCall>, StoreError>;

    /// Decides whether chunks may be sent to a model, and if so writes the
    /// call's receipt and reads their text, in one transaction: the scope
    /// must be active and must have granted the destination, and the
    /// budget must allow the call. Nothing is sent without a receipt, and
    /// a revocation that commits first is always seen.
    fn permit(&mut self, request: &PermitRequest<'_>) -> Result<Permit, StoreError>;

    /// Records how a call ended, stores what it read and settles the work
    /// of its chunks, in one transaction. Reads of chunks erased meanwhile
    /// are dropped.
    ///
    /// The end of a call is recorded once. Offering it again changes
    /// nothing and reports what the first time stored, so a caller whose
    /// answer was lost on the way back can ask again.
    fn finish_call(&mut self, finish: &CallFinish<'_>) -> Result<CallFinished, StoreError>;

    /// Settles work that no call was made for: a permit was refused.
    fn update_work(&mut self, work: &[WorkUpdate]) -> Result<(), StoreError>;

    /// How much work of a kind is queued.
    fn work_counts(&mut self, purpose: Purpose) -> Result<WorkCounts, StoreError>;
}

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
    Arrow, BeadId, BeadRecord, CallId, CatalogRef, Checkpoint, CheckpointReceipt, ChunkFacets,
    ChunkId, ChunkReadRecord, ChunkRecord, ChunkerRef, CompressorRef, Direction, EdgeRecord,
    ErasureRecord, EventDetails, EventId, EventKey, EventRecord, IdKey, NameLinkRecord,
    PairColumns, ProfileRef, ReadDecision, ReadSpan, ReaderRef, Sha256Digest, TimeBasis, Timestamp,
    ToolCallDetails, ToolOutcomeRecord, ToolStatus, Verdict, VerdictNote, VerdictRecord,
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
    /// When a judge may first read the event's beads, if a judge is
    /// configured. A tool call's bead is queued for the judge later, by
    /// the transaction that stores what a model wrote about the call.
    pub judge: Option<Timestamp>,
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
    /// A judge made a bead: a group.
    Bead(Box<BeadRecord>),
    /// A judge wrote a relationship.
    Edge(Box<EdgeRecord>),
    /// A judge gave a verdict on a pair. Verdicts about erased beads are
    /// not exported.
    Verdict(Box<VerdictRecord>),
    /// A judge linked two names.
    NameLink(Box<NameLinkRecord>),
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
            Change::Bead(bead) => bead.seq,
            Change::Edge(edge) => edge.seq,
            Change::Verdict(verdict) => verdict.seq,
            Change::NameLink(link) => link.seq,
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
    /// Judging how new beads relate to the recent ones of their workspace.
    Judge,
    /// Reading one held arrow that ends currency a second time, blind.
    SecondRead,
}

impl Purpose {
    pub fn as_str(self) -> &'static str {
        match self {
            Purpose::ChunkRead => "chunk_read",
            Purpose::ToolRead => "tool_read",
            Purpose::Judge => "judge",
            Purpose::SecondRead => "second_read",
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
    /// A new bead to be judged.
    NewBead(BeadId),
    /// The source of an arrow that ends currency, held for its second read.
    HeldChain(BeadId),
}

impl WorkItem {
    /// The kind of work the item is queued for.
    pub fn purpose(self) -> Purpose {
        match self {
            WorkItem::Chunk(_) => Purpose::ChunkRead,
            WorkItem::ToolCall(_) => Purpose::ToolRead,
            WorkItem::NewBead(_) => Purpose::Judge,
            WorkItem::HeldChain(_) => Purpose::SecondRead,
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
    /// Nothing more is owed for it, and no call was made: a judge's new
    /// bead that had nothing to be compared with.
    Done,
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
    /// When a judge may first read the tool call's bead, if a judge is
    /// configured and `tool_outcome` is stored.
    pub judge_after: Option<Timestamp>,
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

// ---- judging ----

/// A bead as a judge may be shown it, with what code needs to compute the
/// columns of a pair. It lives in memory for one read and is never stored.
#[derive(Debug, Clone, PartialEq)]
pub struct ShownBead {
    pub bead_id: BeadId,
    pub scope: String,
    pub seq: u64,
    pub time_at: Timestamp,
    pub time_basis: TimeBasis,
    pub content: ShownContent,
    /// The type a model gave the chunk, as a key of the vocabulary.
    pub type_key: Option<String>,
    /// Whether it is current: nothing has replaced or withdrawn it.
    pub is_head: bool,
    /// The arrow by which it already replaced or withdrew one or two
    /// beads, if it did.
    pub chain: Option<Arrow>,
    /// The group bead it is a member of, if any.
    pub group: Option<BeadId>,
    /// For a chunk of a message that was cut into several: which chunk it
    /// is, counted from 1, and how many there are.
    pub part: Option<(u32, u32)>,
    /// The key and the declared details of its event.
    pub key: EventKey,
    pub details: EventDetails,
    /// The facets of its chunk. A tool call's bead has none.
    pub facets: Option<ChunkFacets>,
}

/// What of a bead a judge is shown.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ShownContent {
    /// One chunk of a message, a document or a record: its exact text.
    Text(String),
    /// A tool call, as a model recorded it. Never the tool's own text.
    ToolCall {
        tool: String,
        action: String,
        outcome: String,
        status: ToolStatus,
    },
}

impl ShownContent {
    /// Bytes of text a judge is shown for the bead.
    pub fn bytes(&self) -> u64 {
        match self {
            ShownContent::Text(text) => text.len() as u64,
            ShownContent::ToolCall {
                tool,
                action,
                outcome,
                ..
            } => (tool.len() + action.len() + outcome.len()) as u64,
        }
    }
}

/// Everything one judge call shows: the window, in the order of the beads'
/// times, then the new beads in the same order.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Shown {
    pub window: Vec<ShownBead>,
    pub new: Vec<ShownBead>,
}

impl Shown {
    /// The beads in the order a judge is shown them.
    pub fn listed(&self) -> impl Iterator<Item = &ShownBead> {
        self.window.iter().chain(&self.new)
    }
}

/// A bead reserved for a judge.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LeasedBead {
    pub bead_id: BeadId,
    /// Times the bead has been handed to a judge, this one included.
    pub attempts: u32,
}

/// Work of one workspace, reserved for one judge until the lease runs out.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JudgeBatch {
    pub workspace: String,
    /// The workspace's ID key, for the IDs of the beads a judge makes.
    pub id_key: IdKey,
    pub work: JudgeWork,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JudgeWork {
    /// New beads to read against the window.
    Window(Vec<LeasedBead>),
    /// An arrow that ends currency, which a first read gave, to be read
    /// again without it.
    SecondRead {
        source: LeasedBead,
        arrow: Arrow,
        /// The beads the first read would end with it: one or two.
        heads: Vec<BeadId>,
    },
}

/// How a judge's claim chooses its work.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct JudgeClaim {
    pub now: Timestamp,
    pub lease_until: Timestamp,
    /// Most new beads in one read.
    pub max_new: u32,
    /// Times a bead is handed out at most. One that was handed out that
    /// often and never settled is set aside instead.
    pub max_attempts: u32,
    /// The new beads of one read lie within this long of each other, so
    /// that each lies in the window of the others.
    pub within_ms: u64,
    /// How long a chunk's bead waits for a model to read the chunk before
    /// it is judged without that. `None` when no reader is configured, and
    /// there is nothing to wait for.
    pub wait_for_reads_ms: Option<u64>,
}

/// Which beads a judge is shown beside the new ones.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowOf<'a> {
    /// The beads of the workspace near the new ones, chosen by time alone:
    /// those whose time lies within `within_ms` of the new beads, before
    /// or after, nearest first, up to `max_beads` of them and `max_bytes`
    /// of content. A bead that is itself waiting to be judged is left out:
    /// it meets the new ones when its own turn comes, so no pair is read
    /// twice. So is a bead this judge has already read with every new
    /// bead. Up to `conflict_beads` beads of open disputes come first,
    /// whatever their time: two beads that contradict each other, or of
    /// which one says the other is wrong, while both are current.
    Near {
        within_ms: u64,
        max_beads: u32,
        max_bytes: u64,
        conflict_beads: u32,
    },
    /// Exactly these: the same beads again for a repair, or the beads a
    /// held arrow would end, for its second read. Every one of them is shown,
    /// or nothing is: if one may not be shown, the answer is
    /// [`JudgePermit::NothingToJudge`] and names it.
    These(&'a [BeadId]),
}

/// Asks to show beads of one workspace to a judge.
#[derive(Clone, Copy)]
pub struct JudgePermitRequest<'a> {
    pub workspace: &'a str,
    /// [`Purpose::Judge`] or [`Purpose::SecondRead`].
    pub purpose: Purpose,
    pub new: &'a [BeadId],
    pub window: WindowOf<'a>,
    pub destination: &'a str,
    pub reader: &'a ReaderRef,
    pub attempt: u8,
    pub repairs: Option<CallId>,
    /// What a call that shows these beads will send and may cost. `None`
    /// means nothing can be sent, and nothing is permitted.
    pub sending: &'a (dyn Fn(&Shown) -> Option<Sending> + Sync),
    pub budget: Option<BudgetConfig>,
    pub at: Timestamp,
}

impl std::fmt::Debug for JudgePermitRequest<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("JudgePermitRequest")
            .field("workspace", &self.workspace)
            .field("purpose", &self.purpose)
            .field("new", &self.new)
            .field("destination", &self.destination)
            .field("attempt", &self.attempt)
            .finish_non_exhaustive()
    }
}

/// Why a bead was not shown to a judge.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Withheld {
    /// Its scope is paused or revoked.
    ScopeNotActive,
    /// Its scope has not granted the judge's destination.
    NoConsent,
    /// It was erased, or is a tool call no model has read yet.
    NothingToShow,
}

/// The answer to a [`JudgePermitRequest`]. Only `Granted` wrote a receipt,
/// and only `Granted` hands out text.
#[derive(Debug, Clone, PartialEq)]
pub enum JudgePermit {
    /// The receipt is committed. `shown` is what to send: every bead of it
    /// belongs to an active scope that has granted the destination.
    /// `withheld` are the new beads that were asked for and not shown.
    Granted {
        call_id: CallId,
        shown: Shown,
        withheld: Vec<(BeadId, Withheld)>,
    },
    /// None of the new beads may be shown, or a bead named by
    /// [`WindowOf::These`] may not. The list says which, and why.
    NothingToJudge(Vec<(BeadId, Withheld)>),
    /// One new bead may be shown and there is nothing beside it, so there
    /// is no pair to read. No receipt is written and nothing is sent. The
    /// list holds the new beads that were withheld, if any.
    NothingToCompare(Vec<(BeadId, Withheld)>),
    /// The binding's budget for the day is spent in this workspace.
    OverBudget,
    /// What would be shown cannot be made into a request.
    Unsendable,
}

/// A verdict to store for one pair.
#[derive(Debug, Clone, PartialEq)]
pub struct VerdictWrite {
    pub earlier: BeadId,
    pub later: BeadId,
    /// The new bead the pair was read for. The verdict is stored only if
    /// that bead was still owed a judgment.
    pub read_for: BeadId,
    pub verdict: Verdict,
    /// The arrow given, with any verdict but `none` and `unsure`.
    pub arrow: Option<Arrow>,
    pub direction: Direction,
    /// Set by the judge's side only for a second read that differs. What
    /// became of every other arrow that ends currency, the store decides
    /// and notes.
    pub note: Option<VerdictNote>,
    pub columns: PairColumns,
}

/// A relationship between two beads of source content to store: an arrow
/// that does not end currency. Its type is the arrow's.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EdgeWrite {
    pub arrow: Arrow,
    /// The bead the arrow's forward name is said of. For a symmetric
    /// arrow, the smaller ID.
    pub from: BeadId,
    pub to: BeadId,
    /// The new bead whose pair said so.
    pub read_for: BeadId,
}

/// An arrow that ends currency, to store or to hold for its second read:
/// a later statement that replaces or withdraws one or two beads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChainWrite {
    pub arrow: Arrow,
    /// The bead of the later statement, which the arrow runs from.
    pub source: BeadId,
    /// The beads it ends: one or two, each current when the judge was
    /// shown it.
    pub heads: Vec<BeadId>,
    /// The new beads whose pairs said so. The arrow is stored only if each
    /// of them was still owed a judgment.
    pub read_for: Vec<BeadId>,
    /// Whether the arrow waits for a second read that agrees with it.
    pub hold: bool,
}

/// Beads a judge marked as one memory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupWrite {
    /// The group some of the members already belonged to when the judge
    /// was shown them. The others join it.
    pub existing: Option<BeadId>,
    /// The ID a new group bead will have, if there is no group to join.
    pub bead_id: BeadId,
    /// Every member, those of an existing group included.
    pub members: Vec<BeadId>,
}

/// Two names a judge linked, as byte ranges of their chunks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NameLinkWrite {
    /// The new bead whose pair this is.
    pub read_for: BeadId,
    pub a_bead: BeadId,
    pub a_start: u64,
    pub a_end: u64,
    pub b_bead: BeadId,
    pub b_start: u64,
    pub b_end: u64,
}

/// Everything a valid reply of a judge says, to be stored in one
/// transaction.
#[derive(Debug, Clone, PartialEq)]
pub struct JudgmentWrite {
    /// 1, or 2 for the second read of a held arrow.
    pub read_no: u8,
    pub rubric_rev: u32,
    pub new_beads: Vec<BeadId>,
    pub window_beads: Vec<BeadId>,
    pub verdicts: Vec<VerdictWrite>,
    pub edges: Vec<EdgeWrite>,
    pub chains: Vec<ChainWrite>,
    pub groups: Vec<GroupWrite>,
    pub names: Vec<NameLinkWrite>,
}

/// Judge work to settle without finishing a call.
#[derive(Debug, Clone, Copy)]
pub struct JudgeSettle<'a> {
    /// The lease the claim set on the work.
    pub lease: Timestamp,
    pub work: &'a [WorkUpdate],
}

/// Records how a judge's call ended and what it said, in one transaction.
#[derive(Debug, Clone)]
pub struct JudgmentFinish<'a> {
    pub call_id: CallId,
    pub workspace: &'a str,
    /// The lease the claim set on the work this call was made for. Only
    /// work still held under it is settled or stored for: a worker whose
    /// lease ran out, and whose work was handed to another, changes
    /// nothing.
    pub lease: Timestamp,
    pub result: CallResult,
    pub usage: Option<Usage>,
    /// What to store. `None` unless the reply was valid.
    pub judgment: Option<&'a JudgmentWrite>,
    /// For a second read with a valid reply: the source whose held arrow
    /// this read settles. The arrow is written if `judgment` holds it, and
    /// dropped otherwise.
    pub settles: Option<BeadId>,
    pub reader: &'a ReaderRef,
    /// What to do with the work of beads that were not judged.
    pub work: &'a [WorkUpdate],
    pub at: Timestamp,
}

/// What [`Store::finish_judgment`] stored.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct JudgmentFinished {
    /// New beads whose judgment was stored.
    pub judged: u32,
    pub verdicts: u32,
    /// Relationships written, apart from those that end currency and
    /// those of groups.
    pub edges: u32,
    /// Arrows written that end currency.
    pub chains: u32,
    /// Arrows that end currency held for a second read.
    pub held: u32,
    /// Arrows that end currency not written: one of their beads was no
    /// longer current, or the source already ended beads with an arrow
    /// this one could not join, or waited with another held one.
    pub chains_dropped: u32,
    /// Group beads made or added to.
    pub groups: u32,
    pub names: u32,
    /// Verdicts not stored: one of their beads was erased, its scope
    /// stopped being active, or the new bead was no longer owed a
    /// judgment.
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

    /// Settles a judge's work that no call was made for, or that a call
    /// made before did not judge, when no call is finished with it. Only
    /// work still held under `settle.lease` is changed.
    fn settle_judgment(&mut self, settle: &JudgeSettle<'_>) -> Result<(), StoreError>;

    // ---- judging ----

    /// Reserves work for a judge in one workspace, until the lease runs
    /// out: a held arrow that waits for its second read, or new beads
    /// that are due. `None` if nothing is due, or a judge already reads in
    /// every workspace that has work. One judge reads in a workspace at a
    /// time.
    fn claim_judgment(&mut self, claim: &JudgeClaim) -> Result<Option<JudgeBatch>, StoreError>;

    /// Decides which beads may be shown to a judge, and if any new bead
    /// may, writes the call's receipt and reads what is shown, in one
    /// transaction. A bead is shown only while its scope is active and has
    /// granted the destination.
    fn permit_judgment(
        &mut self,
        request: &JudgePermitRequest<'_>,
    ) -> Result<JudgePermit, StoreError>;

    /// Records how a judge's call ended, stores what it said and settles
    /// the work of its new beads, in one transaction. Nothing is stored
    /// for a bead erased meanwhile, or one whose scope stopped being
    /// active.
    fn finish_judgment(
        &mut self,
        finish: &JudgmentFinish<'_>,
    ) -> Result<JudgmentFinished, StoreError>;

    /// How much work of a kind is queued.
    fn work_counts(&mut self, purpose: Purpose) -> Result<WorkCounts, StoreError>;
}

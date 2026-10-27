//! The operational store (OPS-01).
//!
//! It holds the event-key ledger and tombstones, profile pins, the outbox
//! of records waiting for delivery, chunks waiting for a model type
//! decision, and the attempt journal of model calls. It is never queried by
//! consumers and exposes no retrieval API.

mod memory;

pub use memory::MemoryStore;

use core_memory_contract::{ChunkId, EventId, EventKey, PendingReason, Sha256Digest, Timestamp};

use crate::error::StoreError;
use crate::model::Usage;

/// What the ledger knows about an event key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LedgerEntry {
    Live {
        event_id: EventId,
        event_hash: Sha256Digest,
        chunk_ids: Vec<ChunkId>,
    },
    /// Erased. The key is matched by a keyed hash, never stored in plain
    /// form after erasure (ING-05).
    Tombstoned,
}

/// Everything one admission writes, committed atomically: the ledger entry,
/// the records for the outbox, and the chunks held for a model decision.
#[derive(Debug, Clone)]
pub struct AdmissionWrite<'a> {
    pub key: &'a EventKey,
    pub event_id: EventId,
    pub event_hash: Sha256Digest,
    pub chunk_ids: &'a [ChunkId],
    pub records: Vec<OutboxRecord>,
    pub held: Vec<HeldChunk>,
    pub admitted_at: Timestamp,
}

/// A chunk whose record is held until a model decides its type, or until
/// `deadline_at`, whichever comes first (CLS-05).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HeldChunk {
    pub chunk_id: ChunkId,
    pub event_id: EventId,
    pub scope: String,
    pub profile_id: String,
    pub text: String,
    pub record: OutboxRecord,
    pub deadline_at: Timestamp,
}

/// A chunk in the classification queue. `held_record` is present until the
/// chunk record has been released to the outbox.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueuedChunk {
    pub chunk_id: ChunkId,
    pub event_id: EventId,
    pub scope: String,
    pub profile_id: String,
    pub text: String,
    pub held_record: Option<OutboxRecord>,
    pub deadline_at: Timestamp,
    pub attempts: u32,
    pub last_reason: Option<PendingReason>,
    /// The largest batch this chunk may be claimed in. Absent until a batch
    /// it was in is split or it is refused on its own; it only ever shrinks.
    pub batch_cap: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueueUpdate {
    pub chunk_id: ChunkId,
    pub action: QueueAction,
}

/// What to do with a queued chunk. Applied atomically per batch; an update
/// for a chunk no longer queued is ignored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum QueueAction {
    /// The type is final. Emit `if_held` if the chunk record is still held,
    /// otherwise `if_released`, and leave the queue. Pending becomes final
    /// exactly once (CLS-04).
    Complete {
        if_held: Option<OutboxRecord>,
        if_released: OutboxRecord,
    },
    /// Not decided yet. Keep the chunk queued with `reason`; move its next
    /// attempt if given; release the held record as `release` if still held;
    /// lower its batch cap to `batch_cap` if given (a cap never rises).
    Retry {
        reason: PendingReason,
        next_attempt_at: Option<Timestamp>,
        count_attempt: bool,
        release: Option<OutboxRecord>,
        batch_cap: Option<u32>,
    },
    /// Stop trying. Release the held record if still held and leave the
    /// queue; the chunk stays pending until a backfill.
    Abandon { release: Option<OutboxRecord> },
}

/// A model call about to be sent, charged against the budget first (MOD-04).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttemptReservation {
    pub attempt_id: String,
    pub binding_id: String,
    pub binding_rev: Sha256Digest,
    pub scope: String,
    /// UTC date the budget is counted in, `YYYY-MM-DD`.
    pub day: String,
    /// Reserved maxima. Reservations are never refunded.
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub chunk_count: u32,
    pub started_at: Timestamp,
    pub max_requests: u64,
    pub max_tokens: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReserveOutcome {
    Reserved,
    /// The call would exceed the day's budget. Nothing was recorded.
    Exhausted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttemptState {
    /// A reply came back and passed validation.
    Succeeded,
    /// A reply came back and failed validation.
    Rejected,
    TransientError,
    PermanentError,
    /// The model declined the content, or the request or reply did not fit.
    Refused,
    /// The outcome is unknown; the provider may have processed the call.
    InDoubt,
}

impl AttemptState {
    pub fn as_str(self) -> &'static str {
        match self {
            AttemptState::Succeeded => "succeeded",
            AttemptState::Rejected => "rejected",
            AttemptState::TransientError => "transient_error",
            AttemptState::PermanentError => "permanent_error",
            AttemptState::Refused => "refused",
            AttemptState::InDoubt => "in_doubt",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttemptFinish {
    pub attempt_id: String,
    pub state: AttemptState,
    pub usage: Option<Usage>,
    pub detail: Option<String>,
    pub finished_at: Timestamp,
}

/// A serialized record queued for delivery.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutboxRecord {
    pub record_key: String,
    pub record_json: String,
}

/// A queued record with its position in the outbox.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutboxEntry {
    /// Strictly increasing, starting at 1.
    pub seq: u64,
    pub record_key: String,
    pub record_json: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitResult {
    Committed,
    /// Another admission of the same key committed first.
    AlreadyPresent(LedgerEntry),
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

/// A record a sink refused for good, kept for operators to inspect.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryFailure {
    pub seq: u64,
    pub record_key: String,
    pub detail: String,
}

/// Durable operational state. Implementations must make
/// [`OperationalStore::commit_admission`] atomic.
pub trait OperationalStore {
    fn ledger_get(&self, key: &EventKey) -> Result<Option<LedgerEntry>, StoreError>;

    /// Writes the ledger entry and queues the records, or does nothing and
    /// returns the existing entry if the key is already present.
    fn commit_admission(&mut self, write: AdmissionWrite<'_>) -> Result<CommitResult, StoreError>;

    /// Records that `(kind, id, rev)` has contents `hash`, or reports that
    /// it was pinned with different contents.
    fn pin(
        &mut self,
        kind: PinKind,
        id: &str,
        rev: u32,
        hash: &Sha256Digest,
    ) -> Result<PinResult, StoreError>;

    /// Up to `limit` outbox entries with `seq > after`, in order.
    fn outbox_after(&self, after: u64, limit: usize) -> Result<Vec<OutboxEntry>, StoreError>;

    /// The highest outbox sequence number ever assigned (0 if none).
    fn outbox_head(&self) -> Result<u64, StoreError>;

    /// The last sequence number delivered to `sink_id` (0 if none).
    fn sink_cursor(&self, sink_id: &str) -> Result<u64, StoreError>;

    /// Moves `sink_id`'s cursor forward to `seq` and keeps `failures`.
    /// A cursor never moves backwards.
    fn advance_sink_cursor(
        &mut self,
        sink_id: &str,
        seq: u64,
        failures: &[DeliveryFailure],
    ) -> Result<(), StoreError>;

    /// Deletes outbox entries every sink in `required` has passed. Optional
    /// sinks never hold entries back. Returns how many were deleted.
    fn prune_outbox(&mut self, required: &[String]) -> Result<u64, StoreError>;

    /// Claims up to `limit` due chunks (next attempt reached, no live lease)
    /// sharing one (scope, profile), leasing them until `lease_until`.
    /// Batches never mix scopes (SEC-04).
    ///
    /// The first due chunk, by next attempt and then queue order, picks the
    /// group and the batch size: `limit`, or its batch cap if smaller. Only
    /// chunks with no cap or a cap of at least that size join it, so a batch
    /// is never larger than the smallest cap among its chunks.
    fn claim_classification(
        &mut self,
        now: Timestamp,
        lease_until: Timestamp,
        limit: usize,
    ) -> Result<Vec<QueuedChunk>, StoreError>;

    /// Up to `limit` queued chunks still held past their deadline.
    fn expired_holds(&self, now: Timestamp, limit: usize) -> Result<Vec<QueuedChunk>, StoreError>;

    /// Applies queue updates and the records they emit, atomically.
    fn apply_classification(&mut self, updates: Vec<QueueUpdate>) -> Result<(), StoreError>;

    /// Number of chunks waiting for a model decision.
    fn classification_queue_len(&self) -> Result<u64, StoreError>;

    /// Records a model call as started and charges its reserved tokens to
    /// the (binding, scope, day) budget, or refuses if that would exceed it.
    fn reserve_attempt(
        &mut self,
        reservation: &AttemptReservation,
    ) -> Result<ReserveOutcome, StoreError>;

    /// Records how a started call ended.
    fn finish_attempt(&mut self, finish: &AttemptFinish) -> Result<(), StoreError>;

    /// Records queued but not yet delivered to the slowest required sink.
    fn outbox_backlog(&self, required: &[String]) -> Result<u64, StoreError> {
        let head = self.outbox_head()?;
        let mut slowest = head;
        for sink in required {
            slowest = slowest.min(self.sink_cursor(sink)?);
        }
        Ok(head - slowest)
    }
}

//! The operational store (OPS-01).
//!
//! It holds the event-key ledger and tombstones, profile pins, and the
//! outbox of records waiting for delivery. It is never queried by consumers
//! and exposes no retrieval API.

mod memory;

pub use memory::MemoryStore;

use core_memory_contract::{ChunkId, EventId, EventKey, Sha256Digest, Timestamp};

use crate::error::StoreError;

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

/// Everything one admission writes, committed atomically: the ledger entry
/// and the records for the outbox.
#[derive(Debug, Clone)]
pub struct AdmissionWrite<'a> {
    pub key: &'a EventKey,
    pub event_id: EventId,
    pub event_hash: Sha256Digest,
    pub chunk_ids: &'a [ChunkId],
    pub records: Vec<OutboxRecord>,
    pub admitted_at: Timestamp,
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

use std::collections::{BTreeMap, VecDeque};

use core_memory_contract::{ChunkId, EventKey, Sha256Digest, Timestamp};

use super::{
    AdmissionWrite, AttemptFinish, AttemptReservation, AttemptState, CommitResult, DeliveryFailure,
    LedgerEntry, OperationalStore, OutboxEntry, OutboxRecord, PinKind, PinResult, QueueAction,
    QueueUpdate, QueuedChunk, ReserveOutcome,
};
use crate::error::StoreError;

/// An in-memory [`OperationalStore`] for tests and embedding. Nothing
/// survives the process.
#[derive(Debug, Default)]
pub struct MemoryStore {
    ledger: BTreeMap<EventKey, LedgerEntry>,
    pins: BTreeMap<(&'static str, String, u32), Sha256Digest>,
    outbox: VecDeque<OutboxEntry>,
    head: u64,
    cursors: BTreeMap<String, u64>,
    failures: Vec<(String, DeliveryFailure)>,
    queue: BTreeMap<ChunkId, QueueRow>,
    queue_seq: u64,
    attempts: Vec<AttemptRow>,
}

#[derive(Debug, Clone)]
struct QueueRow {
    /// Insertion order, so chunks are classified first in, first out.
    seq: u64,
    chunk: QueuedChunk,
    next_attempt_at: Timestamp,
    lease_until: Option<Timestamp>,
}

/// One row of the attempt journal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AttemptRow {
    pub reservation: AttemptReservation,
    pub state: Option<AttemptState>,
    pub finish: Option<AttemptFinish>,
}

/// A batch cap as a batch length; a cap is at least one chunk.
fn cap_len(cap: u32) -> usize {
    usize::try_from(cap.max(1)).unwrap_or(usize::MAX)
}

impl MemoryStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Marks a key as erased. Erasure governance (plan, approval, limits)
    /// is not implemented yet; this exists for tests of tombstone handling.
    pub fn tombstone(&mut self, key: &EventKey) {
        self.ledger.insert(key.clone(), LedgerEntry::Tombstoned);
    }

    pub fn delivery_failures(&self) -> &[(String, DeliveryFailure)] {
        &self.failures
    }

    /// The attempt journal, oldest first.
    pub fn attempts(&self) -> &[AttemptRow] {
        &self.attempts
    }

    fn push_outbox(&mut self, record: OutboxRecord) {
        self.head += 1;
        self.outbox.push_back(OutboxEntry {
            seq: self.head,
            record_key: record.record_key,
            record_json: record.record_json,
        });
    }
}

impl OperationalStore for MemoryStore {
    fn ledger_get(&self, key: &EventKey) -> Result<Option<LedgerEntry>, StoreError> {
        Ok(self.ledger.get(key).cloned())
    }

    fn commit_admission(&mut self, write: AdmissionWrite<'_>) -> Result<CommitResult, StoreError> {
        if let Some(existing) = self.ledger.get(write.key) {
            return Ok(CommitResult::AlreadyPresent(existing.clone()));
        }
        self.ledger.insert(
            write.key.clone(),
            LedgerEntry::Live {
                event_id: write.event_id,
                event_hash: write.event_hash,
                chunk_ids: write.chunk_ids.to_vec(),
            },
        );
        for record in write.records {
            self.push_outbox(record);
        }
        for held in write.held {
            self.queue_seq += 1;
            self.queue.insert(
                held.chunk_id,
                QueueRow {
                    seq: self.queue_seq,
                    next_attempt_at: write.admitted_at,
                    lease_until: None,
                    chunk: QueuedChunk {
                        chunk_id: held.chunk_id,
                        event_id: held.event_id,
                        scope: held.scope,
                        profile_id: held.profile_id,
                        text: held.text,
                        held_record: Some(held.record),
                        deadline_at: held.deadline_at,
                        attempts: 0,
                        last_reason: None,
                        batch_cap: None,
                    },
                },
            );
        }
        Ok(CommitResult::Committed)
    }

    fn pin(
        &mut self,
        kind: PinKind,
        id: &str,
        rev: u32,
        hash: &Sha256Digest,
    ) -> Result<PinResult, StoreError> {
        match self.pins.get(&(kind.as_str(), id.to_string(), rev)) {
            Some(stored) if stored == hash => Ok(PinResult::Matches),
            Some(stored) => Ok(PinResult::Mismatch { stored: *stored }),
            None => {
                self.pins
                    .insert((kind.as_str(), id.to_string(), rev), *hash);
                Ok(PinResult::Pinned)
            }
        }
    }

    fn outbox_after(&self, after: u64, limit: usize) -> Result<Vec<OutboxEntry>, StoreError> {
        Ok(self
            .outbox
            .iter()
            .filter(|e| e.seq > after)
            .take(limit)
            .cloned()
            .collect())
    }

    fn outbox_head(&self) -> Result<u64, StoreError> {
        Ok(self.head)
    }

    fn sink_cursor(&self, sink_id: &str) -> Result<u64, StoreError> {
        Ok(self.cursors.get(sink_id).copied().unwrap_or(0))
    }

    fn advance_sink_cursor(
        &mut self,
        sink_id: &str,
        seq: u64,
        failures: &[DeliveryFailure],
    ) -> Result<(), StoreError> {
        let cursor = self.cursors.entry(sink_id.to_string()).or_insert(0);
        *cursor = (*cursor).max(seq);
        self.failures
            .extend(failures.iter().map(|f| (sink_id.to_string(), f.clone())));
        Ok(())
    }

    fn prune_outbox(&mut self, required: &[String]) -> Result<u64, StoreError> {
        let mut floor = self.head;
        for sink in required {
            floor = floor.min(self.sink_cursor(sink)?);
        }
        let before = self.outbox.len();
        while self.outbox.front().is_some_and(|e| e.seq <= floor) {
            self.outbox.pop_front();
        }
        Ok((before - self.outbox.len()) as u64)
    }

    fn claim_classification(
        &mut self,
        now: Timestamp,
        lease_until: Timestamp,
        limit: usize,
    ) -> Result<Vec<QueuedChunk>, StoreError> {
        let mut due: Vec<&QueueRow> = self
            .queue
            .values()
            .filter(|r| r.next_attempt_at <= now && r.lease_until.is_none_or(|l| l <= now))
            .collect();
        due.sort_by_key(|r| (r.next_attempt_at, r.seq));
        let Some(first) = due.first() else {
            return Ok(Vec::new());
        };
        let group = (first.chunk.scope.clone(), first.chunk.profile_id.clone());
        let limit = first
            .chunk
            .batch_cap
            .map_or(limit, |cap| limit.min(cap_len(cap)));
        let ids: Vec<ChunkId> = due
            .iter()
            .filter(|r| (&r.chunk.scope, &r.chunk.profile_id) == (&group.0, &group.1))
            .filter(|r| r.chunk.batch_cap.is_none_or(|cap| cap_len(cap) >= limit))
            .take(limit)
            .map(|r| r.chunk.chunk_id)
            .collect();
        let mut claimed = Vec::with_capacity(ids.len());
        for id in ids {
            if let Some(row) = self.queue.get_mut(&id) {
                row.lease_until = Some(lease_until);
                claimed.push(row.chunk.clone());
            }
        }
        Ok(claimed)
    }

    fn expired_holds(&self, now: Timestamp, limit: usize) -> Result<Vec<QueuedChunk>, StoreError> {
        let mut expired: Vec<&QueueRow> = self
            .queue
            .values()
            .filter(|r| r.chunk.held_record.is_some() && r.chunk.deadline_at <= now)
            .collect();
        expired.sort_by_key(|r| (r.chunk.deadline_at, r.seq));
        Ok(expired
            .into_iter()
            .take(limit)
            .map(|r| r.chunk.clone())
            .collect())
    }

    fn apply_classification(&mut self, updates: Vec<QueueUpdate>) -> Result<(), StoreError> {
        // Validate first so a bad batch changes nothing.
        for u in &updates {
            if let (Some(row), QueueAction::Complete { if_held: None, .. }) =
                (self.queue.get(&u.chunk_id), &u.action)
                && row.chunk.held_record.is_some()
            {
                return Err(StoreError::Corrupt(format!(
                    "chunk {} is still held but the completion has no chunk record",
                    u.chunk_id
                )));
            }
        }
        for u in updates {
            let Some(row) = self.queue.get_mut(&u.chunk_id) else {
                continue;
            };
            let held = row.chunk.held_record.is_some();
            match u.action {
                QueueAction::Complete {
                    if_held,
                    if_released,
                } => {
                    self.queue.remove(&u.chunk_id);
                    match (held, if_held) {
                        (true, Some(record)) => self.push_outbox(record),
                        _ => self.push_outbox(if_released),
                    }
                }
                QueueAction::Retry {
                    reason,
                    next_attempt_at,
                    count_attempt,
                    release,
                    batch_cap,
                } => {
                    row.chunk.last_reason = Some(reason);
                    row.lease_until = None;
                    if let Some(at) = next_attempt_at {
                        row.next_attempt_at = at;
                    }
                    if count_attempt {
                        row.chunk.attempts += 1;
                    }
                    if let Some(cap) = batch_cap {
                        row.chunk.batch_cap =
                            Some(row.chunk.batch_cap.map_or(cap, |old| old.min(cap)));
                    }
                    if let (true, Some(record)) = (held, release) {
                        row.chunk.held_record = None;
                        self.push_outbox(record);
                    }
                }
                QueueAction::Abandon { release } => {
                    self.queue.remove(&u.chunk_id);
                    if let (true, Some(record)) = (held, release) {
                        self.push_outbox(record);
                    }
                }
            }
        }
        Ok(())
    }

    fn classification_queue_len(&self) -> Result<u64, StoreError> {
        Ok(self.queue.len() as u64)
    }

    fn reserve_attempt(&mut self, r: &AttemptReservation) -> Result<ReserveOutcome, StoreError> {
        let (requests, tokens) = self
            .attempts
            .iter()
            .filter(|a| {
                a.reservation.binding_id == r.binding_id
                    && a.reservation.scope == r.scope
                    && a.reservation.day == r.day
            })
            .fold((0u64, 0u64), |(n, t), a| {
                (
                    n + 1,
                    t + a.reservation.input_tokens + a.reservation.output_tokens,
                )
            });
        if requests + 1 > r.max_requests || tokens + r.input_tokens + r.output_tokens > r.max_tokens
        {
            return Ok(ReserveOutcome::Exhausted);
        }
        self.attempts.push(AttemptRow {
            reservation: r.clone(),
            state: None,
            finish: None,
        });
        Ok(ReserveOutcome::Reserved)
    }

    fn finish_attempt(&mut self, finish: &AttemptFinish) -> Result<(), StoreError> {
        let row = self
            .attempts
            .iter_mut()
            .find(|a| a.reservation.attempt_id == finish.attempt_id)
            .ok_or_else(|| StoreError::Corrupt(format!("no attempt {}", finish.attempt_id)))?;
        row.state = Some(finish.state);
        row.finish = Some(finish.clone());
        Ok(())
    }
}

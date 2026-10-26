use std::collections::{BTreeMap, VecDeque};

use core_memory_contract::{EventKey, Sha256Digest};

use super::{
    AdmissionWrite, CommitResult, DeliveryFailure, LedgerEntry, OperationalStore, OutboxEntry,
    PinKind, PinResult,
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
            self.head += 1;
            self.outbox.push_back(OutboxEntry {
                seq: self.head,
                record_key: record.record_key,
                record_json: record.record_json,
            });
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
}

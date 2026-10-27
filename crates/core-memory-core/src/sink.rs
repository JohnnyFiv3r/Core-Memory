//! Sinks and delivery (REC-04..08).
//!
//! Each sink reads the outbox with its own cursor ("direct" fan-out).
//! Delivery is at least once: a crash between a sink write and the cursor
//! update redelivers, and sinks deduplicate by `record_key`.

use core_memory_contract::{RECORD_CONTRACT_V1, RecordKind};
use serde::Deserialize;

use crate::error::StoreError;
use crate::store::{DeliveryFailure, OperationalStore, OutboxEntry};

/// What a sink can accept (REC-04).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SinkCaps {
    pub contracts: Vec<String>,
    pub record_kinds: Vec<RecordKind>,
    pub max_record_bytes: u64,
    pub supports_type_upgrade: bool,
    pub supports_relationships: bool,
    pub supports_erasure: bool,
    /// Idempotent accept with receipts and first-writer-wins (REC-07).
    pub system_of_record: bool,
}

/// Per-record result of a sink write (REC-05).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WriteOutcome {
    Accepted,
    AlreadyAccepted,
    /// Terminal. The sink holds a different record under this key: the
    /// stored record, unless the sink withholds it from this writer.
    Conflict {
        stored: Option<String>,
    },
    /// Terminal.
    Rejected {
        reason: String,
    },
    Retryable {
        retry_after_ms: u64,
    },
}

#[derive(Debug, thiserror::Error)]
#[error("sink {sink}: {message}")]
pub struct SinkError {
    pub sink: String,
    pub message: String,
    pub retry_after_ms: u64,
}

pub trait Sink: std::fmt::Debug {
    fn id(&self) -> &str;
    fn caps(&self) -> SinkCaps;
    /// Writes `batch` in order and returns one outcome per entry. An `Err`
    /// means nothing in the batch can be assumed written.
    fn write(&mut self, batch: &[OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError>;
}

/// Result of one delivery round to one sink.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeliveryReport {
    pub accepted: u64,
    pub already_accepted: u64,
    /// Records the sink refused for good, or the engine withheld because the
    /// sink cannot take them. They are kept in the store's failure list.
    pub failed: u64,
    /// Entries pruned before this sink read them (optional sinks only).
    pub skipped_gap: u64,
    /// Set when the sink asked to retry; delivery stopped there.
    pub retry_after_ms: Option<u64>,
    /// True when the sink has caught up with the outbox.
    pub idle: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum DeliveryError {
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error("sink {sink} returned {got} outcomes for {expected} records")]
    OutcomeCount {
        sink: String,
        expected: usize,
        got: usize,
    },
}

#[derive(Deserialize)]
struct KindProbe {
    contract: String,
    record_kind: RecordKind,
}

/// Delivers up to `batch_size` outbox entries to `sink`.
pub fn deliver_once(
    store: &mut dyn OperationalStore,
    sink: &mut dyn Sink,
    batch_size: usize,
) -> Result<DeliveryReport, DeliveryError> {
    let sink_id = sink.id().to_string();
    let caps = sink.caps();
    let cursor = store.sink_cursor(&sink_id)?;
    let entries = store.outbox_after(cursor, batch_size)?;
    let mut report = DeliveryReport::default();
    let Some(first) = entries.first() else {
        // Anything between the cursor and the head was pruned before this
        // (optional) sink read it.
        let head = store.outbox_head()?;
        if head > cursor {
            report.skipped_gap = head - cursor;
            store.advance_sink_cursor(&sink_id, head, &[])?;
        }
        report.idle = true;
        return Ok(report);
    };
    report.skipped_gap = first.seq - cursor - 1;

    // Withhold what the sink cannot take, and report it (REC-06).
    let mut failures = Vec::new();
    let mut sendable = Vec::new();
    for entry in entries {
        match unsupported(&entry, &caps) {
            Some(detail) => failures.push(DeliveryFailure {
                seq: entry.seq,
                record_key: entry.record_key.clone(),
                detail,
            }),
            None => sendable.push(entry),
        }
    }

    let outcomes = if sendable.is_empty() {
        Vec::new()
    } else {
        match sink.write(&sendable) {
            Ok(outcomes) => outcomes,
            Err(e) => {
                report.retry_after_ms = Some(e.retry_after_ms);
                return Ok(report);
            }
        }
    };
    if outcomes.len() != sendable.len() {
        return Err(DeliveryError::OutcomeCount {
            sink: sink_id,
            expected: sendable.len(),
            got: outcomes.len(),
        });
    }

    // Advance through the batch in seq order, stopping at the first retry.
    let mut withheld = failures.into_iter().peekable();
    let mut kept_failures = Vec::new();
    let mut advanced_to = cursor;
    for (entry, outcome) in sendable.iter().zip(outcomes) {
        while let Some(f) = withheld.next_if(|f| f.seq < entry.seq) {
            advanced_to = f.seq;
            report.failed += 1;
            kept_failures.push(f);
        }
        match outcome {
            WriteOutcome::Accepted => report.accepted += 1,
            WriteOutcome::AlreadyAccepted => report.already_accepted += 1,
            WriteOutcome::Conflict { stored } => {
                report.failed += 1;
                kept_failures.push(DeliveryFailure {
                    seq: entry.seq,
                    record_key: entry.record_key.clone(),
                    detail: match stored {
                        Some(stored) => format!("conflict: sink holds {stored}"),
                        None => "conflict: sink holds a different record".into(),
                    },
                });
            }
            WriteOutcome::Rejected { reason } => {
                report.failed += 1;
                kept_failures.push(DeliveryFailure {
                    seq: entry.seq,
                    record_key: entry.record_key.clone(),
                    detail: format!("rejected: {reason}"),
                });
            }
            WriteOutcome::Retryable { retry_after_ms } => {
                report.retry_after_ms = Some(retry_after_ms);
                store.advance_sink_cursor(&sink_id, advanced_to, &kept_failures)?;
                return Ok(report);
            }
        }
        advanced_to = entry.seq;
    }
    for f in withheld {
        advanced_to = f.seq;
        report.failed += 1;
        kept_failures.push(f);
    }
    store.advance_sink_cursor(&sink_id, advanced_to, &kept_failures)?;
    Ok(report)
}

/// Delivers until the sink is idle or asks to retry. Returns the summed
/// report.
pub fn deliver_all(
    store: &mut dyn OperationalStore,
    sink: &mut dyn Sink,
    batch_size: usize,
) -> Result<DeliveryReport, DeliveryError> {
    let mut total = DeliveryReport::default();
    loop {
        let round = deliver_once(store, sink, batch_size)?;
        total.accepted += round.accepted;
        total.already_accepted += round.already_accepted;
        total.failed += round.failed;
        total.skipped_gap += round.skipped_gap;
        if round.idle || round.retry_after_ms.is_some() {
            total.idle = round.idle;
            total.retry_after_ms = round.retry_after_ms;
            return Ok(total);
        }
    }
}

fn unsupported(entry: &OutboxEntry, caps: &SinkCaps) -> Option<String> {
    if entry.record_json.len() as u64 > caps.max_record_bytes {
        return Some(format!(
            "withheld: {} bytes exceeds the sink's {} byte limit",
            entry.record_json.len(),
            caps.max_record_bytes
        ));
    }
    let probe: KindProbe = match serde_json::from_str(&entry.record_json) {
        Ok(p) => p,
        Err(e) => return Some(format!("withheld: unreadable record: {e}")),
    };
    if !caps.contracts.contains(&probe.contract) {
        return Some(format!(
            "withheld: sink does not support contract {}",
            probe.contract
        ));
    }
    if !caps.record_kinds.contains(&probe.record_kind) {
        return Some(format!(
            "withheld: sink does not take {:?} records",
            probe.record_kind
        ));
    }
    None
}

impl SinkCaps {
    /// Capabilities of a sink that takes every v1 record kind.
    pub fn accepts_all_v1(max_record_bytes: u64) -> Self {
        SinkCaps {
            contracts: vec![RECORD_CONTRACT_V1.to_string()],
            record_kinds: vec![RecordKind::Chunk, RecordKind::TypeDecision],
            max_record_bytes,
            supports_type_upgrade: true,
            supports_relationships: true,
            supports_erasure: false,
            system_of_record: false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::{AdmissionWrite, MemoryStore, OutboxRecord};
    use core_memory_contract::{EventId, EventKey, Sha256Digest, Timestamp};

    #[derive(Debug)]
    struct ScriptedSink {
        caps: SinkCaps,
        script: Vec<WriteOutcome>,
        received: Vec<u64>,
    }

    impl Sink for ScriptedSink {
        fn id(&self) -> &str {
            "scripted"
        }
        fn caps(&self) -> SinkCaps {
            self.caps.clone()
        }
        fn write(&mut self, batch: &[OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError> {
            self.received.extend(batch.iter().map(|e| e.seq));
            Ok(batch
                .iter()
                .map(|_| {
                    if self.script.is_empty() {
                        WriteOutcome::Accepted
                    } else {
                        self.script.remove(0)
                    }
                })
                .collect())
        }
    }

    fn record(kind: &str, key: &str) -> OutboxRecord {
        OutboxRecord {
            record_key: key.into(),
            record_json: format!(
                r#"{{"contract":"{RECORD_CONTRACT_V1}","record_kind":"{kind}","record_key":"{key}"}}"#
            ),
        }
    }

    fn store_with(records: Vec<OutboxRecord>) -> MemoryStore {
        let mut store = MemoryStore::new();
        let key = EventKey {
            scope: "s".into(),
            source_ns: "n".into(),
            external_id: "e".into(),
            revision: None,
        };
        store
            .commit_admission(AdmissionWrite {
                key: &key,
                event_id: EventId::derive(&key),
                event_hash: Sha256Digest([0; 32]),
                chunk_ids: &[],
                records,
                held: Vec::new(),
                admitted_at: Timestamp::from_unix_millis(0),
            })
            .unwrap();
        store
    }

    #[test]
    fn delivers_everything_and_goes_idle() {
        let mut store = store_with(vec![record("chunk", "a"), record("chunk", "b")]);
        let mut sink = ScriptedSink {
            caps: SinkCaps::accepts_all_v1(1 << 20),
            script: vec![],
            received: vec![],
        };
        let report = deliver_all(&mut store, &mut sink, 1).unwrap();
        assert_eq!(report.accepted, 2);
        assert!(report.idle);
        assert_eq!(store.sink_cursor("scripted").unwrap(), 2);
    }

    #[test]
    fn stops_at_retry_and_resumes_there() {
        let mut store = store_with(vec![
            record("chunk", "a"),
            record("chunk", "b"),
            record("chunk", "c"),
        ]);
        let mut sink = ScriptedSink {
            caps: SinkCaps::accepts_all_v1(1 << 20),
            script: vec![
                WriteOutcome::Accepted,
                WriteOutcome::Retryable { retry_after_ms: 50 },
            ],
            received: vec![],
        };
        let first = deliver_once(&mut store, &mut sink, 10).unwrap();
        assert_eq!(first.retry_after_ms, Some(50));
        assert_eq!(store.sink_cursor("scripted").unwrap(), 1);
        let second = deliver_all(&mut store, &mut sink, 10).unwrap();
        assert_eq!(second.accepted, 2);
        assert_eq!(sink.received, vec![1, 2, 3, 2, 3]);
    }

    #[test]
    fn terminal_failures_advance_and_are_kept() {
        let mut store = store_with(vec![record("chunk", "a"), record("chunk", "b")]);
        let mut sink = ScriptedSink {
            caps: SinkCaps::accepts_all_v1(1 << 20),
            script: vec![WriteOutcome::Rejected {
                reason: "bad".into(),
            }],
            received: vec![],
        };
        let report = deliver_all(&mut store, &mut sink, 10).unwrap();
        assert_eq!((report.failed, report.accepted), (1, 1));
        assert_eq!(store.delivery_failures().len(), 1);
    }

    #[test]
    fn unsupported_kinds_are_withheld_in_order() {
        let mut store = store_with(vec![
            record("chunk", "a"),
            record("type_decision", "b"),
            record("chunk", "c"),
        ]);
        let mut caps = SinkCaps::accepts_all_v1(1 << 20);
        caps.record_kinds = vec![RecordKind::Chunk];
        let mut sink = ScriptedSink {
            caps,
            script: vec![],
            received: vec![],
        };
        let report = deliver_all(&mut store, &mut sink, 10).unwrap();
        assert_eq!((report.accepted, report.failed), (2, 1));
        assert_eq!(sink.received, vec![1, 3]);
        assert_eq!(store.sink_cursor("scripted").unwrap(), 3);
    }

    #[test]
    fn optional_sinks_never_hold_back_pruning() {
        let mut store = store_with(vec![record("chunk", "a"), record("chunk", "b")]);
        store.advance_sink_cursor("required", 2, &[]).unwrap();
        assert_eq!(store.prune_outbox(&["required".into()]).unwrap(), 2);
        let mut late = ScriptedSink {
            caps: SinkCaps::accepts_all_v1(1 << 20),
            script: vec![],
            received: vec![],
        };
        let report = deliver_all(&mut store, &mut late, 10).unwrap();
        assert!(report.idle);
        assert_eq!((report.accepted, report.skipped_gap), (0, 2));
    }
}

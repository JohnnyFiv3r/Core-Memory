//! Embedded SQLite operational store (OPS-01).
//!
//! The ledger is keyed by an HMAC of the event key under a per-store secret,
//! so a tombstone can block re-admission without keeping the erased key in
//! plain form (ING-05). Ledger rows cannot be deleted and their identity
//! columns cannot change: triggers enforce both (deletion safeguard 4).

use std::path::Path;

use core_memory_contract::{
    ChunkId, EventId, EventKey, FieldHasher, PendingReason, Sha256Digest, Timestamp,
};
use core_memory_core::StoreError;
use core_memory_core::store::{
    AdmissionWrite, AttemptFinish, AttemptReservation, CommitResult, DeliveryFailure, LedgerEntry,
    OperationalStore, OutboxEntry, OutboxRecord, PinKind, PinResult, QueueAction, QueueUpdate,
    QueuedChunk, ReserveOutcome,
};
use hmac::{Hmac, KeyInit, Mac};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use sha2::Sha256;

const SCHEMA_VERSION: i64 = 1;
const LEDGER_KEY_DOMAIN: &str = "core-memory/ledger-key/v1";

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value ANY NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS ledger (
    key_mac BLOB PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('live', 'tombstoned')),
    -- Plain key fields. Erasure clears them and keeps key_mac.
    scope TEXT,
    source_ns TEXT,
    external_id TEXT,
    revision TEXT,
    event_id TEXT NOT NULL,
    event_hash BLOB NOT NULL,
    admitted_at TEXT NOT NULL
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS ledger_chunks (
    key_mac BLOB NOT NULL REFERENCES ledger (key_mac),
    ordinal INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    PRIMARY KEY (key_mac, ordinal)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS pins (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    rev INTEGER NOT NULL,
    hash BLOB NOT NULL,
    PRIMARY KEY (kind, id, rev)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    record_key TEXT NOT NULL,
    record_json TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS sink_cursors (
    sink_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS delivery_failures (
    id INTEGER PRIMARY KEY,
    sink_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    record_key TEXT NOT NULL,
    detail TEXT NOT NULL
) STRICT;

-- Chunks waiting for a model type decision. Times are Unix milliseconds.
CREATE TABLE IF NOT EXISTS classification_queue (
    chunk_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    text TEXT NOT NULL,
    held_key TEXT,
    held_json TEXT,
    deadline_at INTEGER NOT NULL,
    next_attempt_at INTEGER NOT NULL,
    lease_until INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_reason TEXT
) STRICT;

-- rowid gives first-in, first-out order among chunks due at the same time.
CREATE INDEX IF NOT EXISTS classification_due ON classification_queue (next_attempt_at);

-- The attempt journal (MOD-04). Rows are never deleted.
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL,
    binding_rev BLOB NOT NULL,
    scope TEXT NOT NULL,
    day TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'started',
    usage_input INTEGER,
    usage_output INTEGER,
    detail TEXT,
    finished_at TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS attempts_budget ON attempts (binding_id, scope, day);

CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempts cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger rows cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS ledger_chunks_no_delete BEFORE DELETE ON ledger_chunks
BEGIN SELECT RAISE(ABORT, 'ledger rows cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS ledger_identity_fixed
BEFORE UPDATE OF key_mac, event_id, event_hash ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger identity cannot change'); END;

CREATE TRIGGER IF NOT EXISTS ledger_chunks_fixed BEFORE UPDATE ON ledger_chunks
BEGIN SELECT RAISE(ABORT, 'ledger rows cannot change'); END;

CREATE TRIGGER IF NOT EXISTS pins_fixed BEFORE UPDATE ON pins
BEGIN SELECT RAISE(ABORT, 'pins cannot change'); END;

CREATE TRIGGER IF NOT EXISTS pins_no_delete BEFORE DELETE ON pins
BEGIN SELECT RAISE(ABORT, 'pins cannot be deleted'); END;
"#;

/// A file-backed (or in-memory) SQLite [`OperationalStore`].
///
/// One process owns a store file at a time (ARC-03).
#[derive(Debug)]
pub struct SqliteStore {
    conn: Connection,
    ledger_key: [u8; 32],
}

fn backend(e: impl std::fmt::Display) -> StoreError {
    StoreError::Backend(e.to_string())
}

impl SqliteStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, StoreError> {
        Self::init(Connection::open(path).map_err(backend)?)
    }

    pub fn open_in_memory() -> Result<Self, StoreError> {
        Self::init(Connection::open_in_memory().map_err(backend)?)
    }

    fn init(conn: Connection) -> Result<Self, StoreError> {
        conn.pragma_update(None, "journal_mode", "WAL")
            .map_err(backend)?;
        conn.pragma_update(None, "synchronous", "FULL")
            .map_err(backend)?;
        conn.pragma_update(None, "foreign_keys", "ON")
            .map_err(backend)?;
        conn.busy_timeout(std::time::Duration::from_secs(5))
            .map_err(backend)?;
        conn.execute_batch(SCHEMA).map_err(backend)?;

        let version: Option<i64> = conn
            .query_row(
                "SELECT value FROM meta WHERE key = 'schema_version'",
                [],
                |r| r.get(0),
            )
            .optional()
            .map_err(backend)?;
        match version {
            None => {
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?1)",
                    params![SCHEMA_VERSION],
                )
                .map_err(backend)?;
            }
            Some(SCHEMA_VERSION) => {}
            Some(other) => {
                return Err(StoreError::Corrupt(format!(
                    "store schema version {other} is not supported (this build reads {SCHEMA_VERSION})"
                )));
            }
        }

        let stored: Option<Vec<u8>> = conn
            .query_row("SELECT value FROM meta WHERE key = 'ledger_key'", [], |r| {
                r.get(0)
            })
            .optional()
            .map_err(backend)?;
        let ledger_key = match stored {
            Some(bytes) => <[u8; 32]>::try_from(bytes.as_slice())
                .map_err(|_| StoreError::Corrupt("ledger key has the wrong length".into()))?,
            None => {
                let mut key = [0u8; 32];
                getrandom::fill(&mut key).map_err(backend)?;
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('ledger_key', ?1)",
                    params![key.as_slice()],
                )
                .map_err(backend)?;
                key
            }
        };
        Ok(Self { conn, ledger_key })
    }

    fn key_mac(&self, key: &EventKey) -> Vec<u8> {
        let mut h = FieldHasher::new(LEDGER_KEY_DOMAIN);
        key.hash_into(&mut h);
        let digest = h.finish();
        // HMAC accepts keys of any length; a 32-byte key cannot fail.
        let mut mac = match <Hmac<Sha256> as KeyInit>::new_from_slice(&self.ledger_key) {
            Ok(mac) => mac,
            Err(_) => unreachable!("HMAC takes keys of any length"),
        };
        mac.update(&digest);
        mac.finalize().into_bytes().to_vec()
    }

    /// Delivery failures kept for `sink_id`, oldest first.
    pub fn delivery_failures(&self, sink_id: &str) -> Result<Vec<DeliveryFailure>, StoreError> {
        let mut stmt = self
            .conn
            .prepare("SELECT seq, record_key, detail FROM delivery_failures WHERE sink_id = ?1 ORDER BY id")
            .map_err(backend)?;
        let rows = stmt
            .query_map(params![sink_id], |r| {
                Ok(DeliveryFailure {
                    seq: r.get::<_, i64>(0)? as u64,
                    record_key: r.get(1)?,
                    detail: r.get(2)?,
                })
            })
            .map_err(backend)?;
        rows.collect::<Result<_, _>>().map_err(backend)
    }

    #[cfg(test)]
    fn tombstone_for_test(&mut self, key: &EventKey) -> Result<(), StoreError> {
        self.conn
            .execute(
                "INSERT INTO ledger (key_mac, state, event_id, event_hash, admitted_at)
                 VALUES (?1, 'tombstoned', 'erased', x'00', 'test')",
                params![self.key_mac(key)],
            )
            .map_err(backend)?;
        Ok(())
    }
}

fn read_entry(conn: &Connection, key_mac: &[u8]) -> Result<Option<LedgerEntry>, StoreError> {
    let row: Option<(String, String, Vec<u8>)> = conn
        .query_row(
            "SELECT state, event_id, event_hash FROM ledger WHERE key_mac = ?1",
            params![key_mac],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .optional()
        .map_err(backend)?;
    let Some((state, event_id, event_hash)) = row else {
        return Ok(None);
    };
    if state == "tombstoned" {
        return Ok(Some(LedgerEntry::Tombstoned));
    }
    let event_id: EventId = event_id
        .parse()
        .map_err(|e| StoreError::Corrupt(format!("event id: {e}")))?;
    let event_hash = Sha256Digest(
        event_hash
            .as_slice()
            .try_into()
            .map_err(|_| StoreError::Corrupt("event hash has the wrong length".into()))?,
    );
    let mut stmt = conn
        .prepare_cached("SELECT chunk_id FROM ledger_chunks WHERE key_mac = ?1 ORDER BY ordinal")
        .map_err(backend)?;
    let chunk_ids = stmt
        .query_map(params![key_mac], |r| r.get::<_, String>(0))
        .map_err(backend)?
        .map(|id| {
            id.map_err(backend)?
                .parse::<ChunkId>()
                .map_err(|e| StoreError::Corrupt(format!("chunk id: {e}")))
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Some(LedgerEntry::Live {
        event_id,
        event_hash,
        chunk_ids,
    }))
}

const QUEUE_COLUMNS: &str = "chunk_id, event_id, scope, profile_id, text, held_key, held_json, \
                             deadline_at, attempts, last_reason";

fn queued_from_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<RawQueued> {
    Ok(RawQueued {
        chunk_id: r.get(0)?,
        event_id: r.get(1)?,
        scope: r.get(2)?,
        profile_id: r.get(3)?,
        text: r.get(4)?,
        held_key: r.get(5)?,
        held_json: r.get(6)?,
        deadline_at: r.get(7)?,
        attempts: r.get(8)?,
        last_reason: r.get(9)?,
    })
}

struct RawQueued {
    chunk_id: String,
    event_id: String,
    scope: String,
    profile_id: String,
    text: String,
    held_key: Option<String>,
    held_json: Option<String>,
    deadline_at: i64,
    attempts: i64,
    last_reason: Option<String>,
}

impl RawQueued {
    fn parse(self) -> Result<QueuedChunk, StoreError> {
        let held_record = match (self.held_key, self.held_json) {
            (Some(record_key), Some(record_json)) => Some(OutboxRecord {
                record_key,
                record_json,
            }),
            _ => None,
        };
        Ok(QueuedChunk {
            chunk_id: self
                .chunk_id
                .parse()
                .map_err(|e| StoreError::Corrupt(format!("chunk id: {e}")))?,
            event_id: self
                .event_id
                .parse()
                .map_err(|e| StoreError::Corrupt(format!("event id: {e}")))?,
            scope: self.scope,
            profile_id: self.profile_id,
            text: self.text,
            held_record,
            deadline_at: Timestamp::from_unix_millis(self.deadline_at),
            attempts: u32::try_from(self.attempts).unwrap_or(u32::MAX),
            last_reason: self.last_reason.as_deref().map(parse_reason).transpose()?,
        })
    }
}

fn reason_str(reason: PendingReason) -> &'static str {
    match reason {
        PendingReason::Timeout => "timeout",
        PendingReason::ProviderError => "provider_error",
        PendingReason::Capacity => "capacity",
        PendingReason::UnsupportedInput => "unsupported_input",
        PendingReason::NoClassifier => "no_classifier",
    }
}

fn parse_reason(s: &str) -> Result<PendingReason, StoreError> {
    Ok(match s {
        "timeout" => PendingReason::Timeout,
        "provider_error" => PendingReason::ProviderError,
        "capacity" => PendingReason::Capacity,
        "unsupported_input" => PendingReason::UnsupportedInput,
        "no_classifier" => PendingReason::NoClassifier,
        other => {
            return Err(StoreError::Corrupt(format!(
                "unknown pending reason `{other}`"
            )));
        }
    })
}

fn push_outbox(tx: &rusqlite::Transaction<'_>, record: &OutboxRecord) -> Result<(), StoreError> {
    tx.execute(
        "INSERT INTO outbox (record_key, record_json) VALUES (?1, ?2)",
        params![record.record_key, record.record_json],
    )
    .map_err(backend)?;
    Ok(())
}

impl OperationalStore for SqliteStore {
    fn ledger_get(&self, key: &EventKey) -> Result<Option<LedgerEntry>, StoreError> {
        read_entry(&self.conn, &self.key_mac(key))
    }

    fn commit_admission(&mut self, write: AdmissionWrite<'_>) -> Result<CommitResult, StoreError> {
        let key_mac = self.key_mac(write.key);
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(backend)?;
        if let Some(existing) = read_entry(&tx, &key_mac)? {
            return Ok(CommitResult::AlreadyPresent(existing));
        }
        tx.execute(
            "INSERT INTO ledger (key_mac, state, scope, source_ns, external_id, revision,
                                 event_id, event_hash, admitted_at)
             VALUES (?1, 'live', ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                key_mac,
                write.key.scope,
                write.key.source_ns,
                write.key.external_id,
                write.key.revision,
                write.event_id.to_string(),
                write.event_hash.as_bytes().as_slice(),
                write.admitted_at.to_rfc3339(),
            ],
        )
        .map_err(backend)?;
        {
            let mut chunk = tx
                .prepare_cached(
                    "INSERT INTO ledger_chunks (key_mac, ordinal, chunk_id) VALUES (?1, ?2, ?3)",
                )
                .map_err(backend)?;
            for (ordinal, id) in write.chunk_ids.iter().enumerate() {
                chunk
                    .execute(params![key_mac, ordinal as i64, id.to_string()])
                    .map_err(backend)?;
            }
            let mut outbox = tx
                .prepare_cached("INSERT INTO outbox (record_key, record_json) VALUES (?1, ?2)")
                .map_err(backend)?;
            for record in &write.records {
                outbox
                    .execute(params![record.record_key, record.record_json])
                    .map_err(backend)?;
            }
            let mut queue = tx
                .prepare_cached(
                    "INSERT INTO classification_queue
                     (chunk_id, event_id, scope, profile_id, text, held_key, held_json,
                      deadline_at, next_attempt_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                )
                .map_err(backend)?;
            for h in &write.held {
                queue
                    .execute(params![
                        h.chunk_id.to_string(),
                        h.event_id.to_string(),
                        h.scope,
                        h.profile_id,
                        h.text,
                        h.record.record_key,
                        h.record.record_json,
                        h.deadline_at.unix_millis(),
                        write.admitted_at.unix_millis(),
                    ])
                    .map_err(backend)?;
            }
        }
        tx.commit().map_err(backend)?;
        Ok(CommitResult::Committed)
    }

    fn pin(
        &mut self,
        kind: PinKind,
        id: &str,
        rev: u32,
        hash: &Sha256Digest,
    ) -> Result<PinResult, StoreError> {
        let stored: Option<Vec<u8>> = self
            .conn
            .query_row(
                "SELECT hash FROM pins WHERE kind = ?1 AND id = ?2 AND rev = ?3",
                params![kind.as_str(), id, rev],
                |r| r.get(0),
            )
            .optional()
            .map_err(backend)?;
        match stored {
            Some(bytes) if bytes.as_slice() == hash.as_bytes() => Ok(PinResult::Matches),
            Some(bytes) => {
                Ok(PinResult::Mismatch {
                    stored: Sha256Digest(bytes.as_slice().try_into().map_err(|_| {
                        StoreError::Corrupt("pin hash has the wrong length".into())
                    })?),
                })
            }
            None => {
                self.conn
                    .execute(
                        "INSERT INTO pins (kind, id, rev, hash) VALUES (?1, ?2, ?3, ?4)",
                        params![kind.as_str(), id, rev, hash.as_bytes().as_slice()],
                    )
                    .map_err(backend)?;
                Ok(PinResult::Pinned)
            }
        }
    }

    fn outbox_after(&self, after: u64, limit: usize) -> Result<Vec<OutboxEntry>, StoreError> {
        let mut stmt = self
            .conn
            .prepare_cached("SELECT seq, record_key, record_json FROM outbox WHERE seq > ?1 ORDER BY seq LIMIT ?2")
            .map_err(backend)?;
        let limit = i64::try_from(limit).unwrap_or(i64::MAX);
        let after = i64::try_from(after).unwrap_or(i64::MAX);
        let rows = stmt
            .query_map(params![after, limit], |r| {
                Ok(OutboxEntry {
                    seq: r.get::<_, i64>(0)? as u64,
                    record_key: r.get(1)?,
                    record_json: r.get(2)?,
                })
            })
            .map_err(backend)?;
        rows.collect::<Result<_, _>>().map_err(backend)
    }

    fn outbox_head(&self) -> Result<u64, StoreError> {
        // sqlite_sequence keeps the highest seq ever assigned, even after
        // pruning; it has no row until the first insert.
        let head: Option<i64> = self
            .conn
            .query_row(
                "SELECT seq FROM sqlite_sequence WHERE name = 'outbox'",
                [],
                |r| r.get(0),
            )
            .optional()
            .map_err(backend)?;
        Ok(head.unwrap_or(0) as u64)
    }

    fn sink_cursor(&self, sink_id: &str) -> Result<u64, StoreError> {
        let seq: Option<i64> = self
            .conn
            .query_row(
                "SELECT seq FROM sink_cursors WHERE sink_id = ?1",
                params![sink_id],
                |r| r.get(0),
            )
            .optional()
            .map_err(backend)?;
        Ok(seq.unwrap_or(0) as u64)
    }

    fn advance_sink_cursor(
        &mut self,
        sink_id: &str,
        seq: u64,
        failures: &[DeliveryFailure],
    ) -> Result<(), StoreError> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(backend)?;
        tx.execute(
            "INSERT INTO sink_cursors (sink_id, seq) VALUES (?1, ?2)
             ON CONFLICT (sink_id) DO UPDATE SET seq = max(seq, excluded.seq)",
            params![sink_id, i64::try_from(seq).unwrap_or(i64::MAX)],
        )
        .map_err(backend)?;
        for f in failures {
            tx.execute(
                "INSERT INTO delivery_failures (sink_id, seq, record_key, detail) VALUES (?1, ?2, ?3, ?4)",
                params![sink_id, f.seq as i64, f.record_key, f.detail],
            )
            .map_err(backend)?;
        }
        tx.commit().map_err(backend)
    }

    fn prune_outbox(&mut self, required: &[String]) -> Result<u64, StoreError> {
        let mut floor = self.outbox_head()?;
        for sink in required {
            floor = floor.min(self.sink_cursor(sink)?);
        }
        let deleted = self
            .conn
            .execute("DELETE FROM outbox WHERE seq <= ?1", params![floor as i64])
            .map_err(backend)?;
        Ok(deleted as u64)
    }

    fn claim_classification(
        &mut self,
        now: Timestamp,
        lease_until: Timestamp,
        limit: usize,
    ) -> Result<Vec<QueuedChunk>, StoreError> {
        let now = now.unix_millis();
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(backend)?;
        let due = "next_attempt_at <= ?1 AND (lease_until IS NULL OR lease_until <= ?1)";
        let group: Option<(String, String)> = tx
            .query_row(
                &format!(
                    "SELECT scope, profile_id FROM classification_queue WHERE {due}
                     ORDER BY next_attempt_at, rowid LIMIT 1"
                ),
                params![now],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()
            .map_err(backend)?;
        let Some((scope, profile_id)) = group else {
            return Ok(Vec::new());
        };
        let rows: Vec<RawQueued> = {
            let mut stmt = tx
                .prepare(&format!(
                    "SELECT {QUEUE_COLUMNS} FROM classification_queue
                     WHERE {due} AND scope = ?2 AND profile_id = ?3
                     ORDER BY next_attempt_at, rowid LIMIT ?4"
                ))
                .map_err(backend)?;
            let limit = i64::try_from(limit).unwrap_or(i64::MAX);
            stmt.query_map(params![now, scope, profile_id, limit], queued_from_row)
                .map_err(backend)?
                .collect::<Result<_, _>>()
                .map_err(backend)?
        };
        for row in &rows {
            tx.execute(
                "UPDATE classification_queue SET lease_until = ?1 WHERE chunk_id = ?2",
                params![lease_until.unix_millis(), row.chunk_id],
            )
            .map_err(backend)?;
        }
        tx.commit().map_err(backend)?;
        rows.into_iter().map(RawQueued::parse).collect()
    }

    fn expired_holds(&self, now: Timestamp, limit: usize) -> Result<Vec<QueuedChunk>, StoreError> {
        let mut stmt = self
            .conn
            .prepare(&format!(
                "SELECT {QUEUE_COLUMNS} FROM classification_queue
                 WHERE held_json IS NOT NULL AND deadline_at <= ?1
                 ORDER BY deadline_at, rowid LIMIT ?2"
            ))
            .map_err(backend)?;
        let limit = i64::try_from(limit).unwrap_or(i64::MAX);
        let rows: Vec<RawQueued> = stmt
            .query_map(params![now.unix_millis(), limit], queued_from_row)
            .map_err(backend)?
            .collect::<Result<_, _>>()
            .map_err(backend)?;
        rows.into_iter().map(RawQueued::parse).collect()
    }

    fn apply_classification(&mut self, updates: Vec<QueueUpdate>) -> Result<(), StoreError> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(backend)?;
        for u in updates {
            let id = u.chunk_id.to_string();
            let held: Option<bool> = tx
                .query_row(
                    "SELECT held_json IS NOT NULL FROM classification_queue WHERE chunk_id = ?1",
                    params![id],
                    |r| r.get(0),
                )
                .optional()
                .map_err(backend)?;
            let Some(held) = held else {
                continue;
            };
            match u.action {
                QueueAction::Complete {
                    if_held,
                    if_released,
                } => {
                    match (held, if_held) {
                        (true, Some(record)) => push_outbox(&tx, &record)?,
                        (true, None) => {
                            return Err(StoreError::Corrupt(format!(
                                "chunk {id} is still held but the completion has no chunk record"
                            )));
                        }
                        (false, _) => push_outbox(&tx, &if_released)?,
                    }
                    tx.execute(
                        "DELETE FROM classification_queue WHERE chunk_id = ?1",
                        params![id],
                    )
                    .map_err(backend)?;
                }
                QueueAction::Retry {
                    reason,
                    next_attempt_at,
                    count_attempt,
                    release,
                } => {
                    tx.execute(
                        "UPDATE classification_queue
                         SET last_reason = ?1, lease_until = NULL,
                             next_attempt_at = COALESCE(?2, next_attempt_at),
                             attempts = attempts + ?3
                         WHERE chunk_id = ?4",
                        params![
                            reason_str(reason),
                            next_attempt_at.map(|t| t.unix_millis()),
                            i64::from(count_attempt),
                            id
                        ],
                    )
                    .map_err(backend)?;
                    if let (true, Some(record)) = (held, release) {
                        push_outbox(&tx, &record)?;
                        tx.execute(
                            "UPDATE classification_queue SET held_key = NULL, held_json = NULL
                             WHERE chunk_id = ?1",
                            params![id],
                        )
                        .map_err(backend)?;
                    }
                }
                QueueAction::Abandon { release } => {
                    if let (true, Some(record)) = (held, release) {
                        push_outbox(&tx, &record)?;
                    }
                    tx.execute(
                        "DELETE FROM classification_queue WHERE chunk_id = ?1",
                        params![id],
                    )
                    .map_err(backend)?;
                }
            }
        }
        tx.commit().map_err(backend)
    }

    fn classification_queue_len(&self) -> Result<u64, StoreError> {
        let n: i64 = self
            .conn
            .query_row("SELECT COUNT(*) FROM classification_queue", [], |r| {
                r.get(0)
            })
            .map_err(backend)?;
        Ok(n as u64)
    }

    fn reserve_attempt(&mut self, r: &AttemptReservation) -> Result<ReserveOutcome, StoreError> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(backend)?;
        let (requests, tokens): (i64, i64) = tx
            .query_row(
                "SELECT COUNT(*), COALESCE(SUM(input_tokens + output_tokens), 0) FROM attempts
                 WHERE binding_id = ?1 AND scope = ?2 AND day = ?3",
                params![r.binding_id, r.scope, r.day],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .map_err(backend)?;
        let requests = requests as u64;
        let tokens = tokens as u64;
        if requests + 1 > r.max_requests || tokens + r.input_tokens + r.output_tokens > r.max_tokens
        {
            return Ok(ReserveOutcome::Exhausted);
        }
        tx.execute(
            "INSERT INTO attempts (attempt_id, binding_id, binding_rev, scope, day, input_tokens,
                                   output_tokens, chunk_count, started_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                r.attempt_id,
                r.binding_id,
                r.binding_rev.as_bytes().as_slice(),
                r.scope,
                r.day,
                i64::try_from(r.input_tokens).unwrap_or(i64::MAX),
                i64::try_from(r.output_tokens).unwrap_or(i64::MAX),
                i64::from(r.chunk_count),
                r.started_at.to_rfc3339(),
            ],
        )
        .map_err(backend)?;
        tx.commit().map_err(backend)?;
        Ok(ReserveOutcome::Reserved)
    }

    fn finish_attempt(&mut self, f: &AttemptFinish) -> Result<(), StoreError> {
        let changed = self
            .conn
            .execute(
                "UPDATE attempts SET state = ?1, usage_input = ?2, usage_output = ?3, detail = ?4,
                                     finished_at = ?5
                 WHERE attempt_id = ?6 AND state = 'started'",
                params![
                    f.state.as_str(),
                    f.usage
                        .map(|u| i64::try_from(u.input_tokens).unwrap_or(i64::MAX)),
                    f.usage
                        .map(|u| i64::try_from(u.output_tokens).unwrap_or(i64::MAX)),
                    f.detail,
                    f.finished_at.to_rfc3339(),
                    f.attempt_id,
                ],
            )
            .map_err(backend)?;
        if changed == 0 {
            return Err(StoreError::Corrupt(format!(
                "attempt {} is not open",
                f.attempt_id
            )));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(id: &str) -> EventKey {
        EventKey {
            scope: "acme".into(),
            source_ns: "ns".into(),
            external_id: id.into(),
            revision: None,
        }
    }

    fn write<'a>(
        key: &'a EventKey,
        chunk_ids: &'a [ChunkId],
        records: usize,
    ) -> AdmissionWrite<'a> {
        AdmissionWrite {
            key,
            event_id: EventId::derive(key),
            event_hash: Sha256Digest::of(key.external_id.as_bytes()),
            chunk_ids,
            records: (0..records)
                .map(|i| OutboxRecord {
                    record_key: format!("r{i}"),
                    record_json: "{}".into(),
                })
                .collect(),
            held: Vec::new(),
            admitted_at: Timestamp::from_unix_millis(0),
        }
    }

    #[test]
    fn commits_and_reads_back() {
        let mut store = SqliteStore::open_in_memory().unwrap();
        let k = key("e1");
        let ids = [
            ChunkId::derive(&k, "c", 1, 0, 1),
            ChunkId::derive(&k, "c", 1, 1, 2),
        ];
        assert_eq!(
            store.commit_admission(write(&k, &ids, 2)).unwrap(),
            CommitResult::Committed
        );
        match store.ledger_get(&k).unwrap().unwrap() {
            LedgerEntry::Live { chunk_ids, .. } => assert_eq!(chunk_ids, ids.to_vec()),
            LedgerEntry::Tombstoned => panic!("expected live"),
        }
        assert!(matches!(
            store.commit_admission(write(&k, &ids, 2)).unwrap(),
            CommitResult::AlreadyPresent(_)
        ));
        assert_eq!(
            store.outbox_head().unwrap(),
            2,
            "the second commit wrote nothing"
        );
    }

    #[test]
    fn ledger_rows_cannot_be_deleted_or_rekeyed() {
        let mut store = SqliteStore::open_in_memory().unwrap();
        let k = key("e1");
        store.commit_admission(write(&k, &[], 0)).unwrap();
        let delete = store.conn.execute("DELETE FROM ledger", []);
        assert!(
            delete
                .unwrap_err()
                .to_string()
                .contains("cannot be deleted")
        );
        let rekey = store.conn.execute("UPDATE ledger SET event_id = 'x'", []);
        assert!(rekey.unwrap_err().to_string().contains("cannot change"));
    }

    #[test]
    fn the_attempt_journal_cannot_be_deleted() {
        let mut store = SqliteStore::open_in_memory().unwrap();
        let reservation = AttemptReservation {
            attempt_id: "a1".into(),
            binding_id: "b".into(),
            binding_rev: Sha256Digest::of(b"rev"),
            scope: "acme".into(),
            day: "2026-10-26".into(),
            input_tokens: 10,
            output_tokens: 10,
            chunk_count: 1,
            started_at: Timestamp::from_unix_millis(0),
            max_requests: 1,
            max_tokens: 100,
        };
        assert_eq!(
            store.reserve_attempt(&reservation).unwrap(),
            ReserveOutcome::Reserved
        );
        let again = AttemptReservation {
            attempt_id: "a2".into(),
            ..reservation
        };
        assert_eq!(
            store.reserve_attempt(&again).unwrap(),
            ReserveOutcome::Exhausted
        );
        let delete = store.conn.execute("DELETE FROM attempts", []);
        assert!(
            delete
                .unwrap_err()
                .to_string()
                .contains("cannot be deleted")
        );
    }

    #[test]
    fn tombstones_match_by_keyed_hash() {
        let mut store = SqliteStore::open_in_memory().unwrap();
        let k = key("gone");
        store.tombstone_for_test(&k).unwrap();
        assert_eq!(store.ledger_get(&k).unwrap(), Some(LedgerEntry::Tombstoned));
        let plain: Option<String> = store
            .conn
            .query_row("SELECT external_id FROM ledger", [], |r| r.get(0))
            .unwrap();
        assert_eq!(plain, None, "the plain key is not stored for a tombstone");
    }

    #[test]
    fn outbox_head_survives_pruning_and_cursors_only_move_forward() {
        let mut store = SqliteStore::open_in_memory().unwrap();
        let k = key("e1");
        store.commit_admission(write(&k, &[], 3)).unwrap();
        store.advance_sink_cursor("a", 3, &[]).unwrap();
        store.advance_sink_cursor("a", 1, &[]).unwrap();
        assert_eq!(store.sink_cursor("a").unwrap(), 3);
        assert_eq!(store.prune_outbox(&["a".into()]).unwrap(), 3);
        assert_eq!(store.outbox_head().unwrap(), 3);
        let k2 = key("e2");
        store.commit_admission(write(&k2, &[], 1)).unwrap();
        assert_eq!(store.outbox_after(3, 10).unwrap()[0].seq, 4);
    }

    #[test]
    fn pins_detect_changed_contents() {
        let mut store = SqliteStore::open_in_memory().unwrap();
        let a = Sha256Digest::of(b"a");
        let b = Sha256Digest::of(b"b");
        assert_eq!(
            store.pin(PinKind::SourceProfile, "p", 1, &a).unwrap(),
            PinResult::Pinned
        );
        assert_eq!(
            store.pin(PinKind::SourceProfile, "p", 1, &a).unwrap(),
            PinResult::Matches
        );
        assert_eq!(
            store.pin(PinKind::SourceProfile, "p", 1, &b).unwrap(),
            PinResult::Mismatch { stored: a }
        );
    }

    #[test]
    fn reopening_keeps_the_ledger_key() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("store.sqlite");
        let k = key("e1");
        {
            let mut store = SqliteStore::open(&path).unwrap();
            store.commit_admission(write(&k, &[], 1)).unwrap();
        }
        let store = SqliteStore::open(&path).unwrap();
        assert!(matches!(
            store.ledger_get(&k).unwrap(),
            Some(LedgerEntry::Live { .. })
        ));
    }
}

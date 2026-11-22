//! [`PostgresStore`]: the engine's store on one PostgreSQL connection.

use std::collections::BTreeMap;

use core_memory_contract::{
    Abstention, BeadId, CallId, CatalogRef, Checkpoint, CheckpointReceipt, ChunkFacets, ChunkId,
    ChunkReadRecord, ChunkRecord, ChunkerRef, CompressorRef, ErasureRecord, EventDetails, EventId,
    EventKey, EventRecord, IdKey, ProfileRef, ReadDecision, ReadSpan, ReaderRef, RecallAnswer,
    Sha256Digest, SpanKind, TimeBasis, Timestamp, ToolCallDetails, ToolOutcomeRecord, ToolStatus,
    TypeRef,
};
use core_memory_core::store::{
    AdmissionCommit, AdmissionWrite, BeadsOf, CallFinish, CallFinished, Change, CheckpointAdvance,
    CheckpointState, ExportedEvent, JudgeBatch, JudgeClaim, JudgePermit, JudgePermitRequest,
    JudgeSettle, JudgmentFinish, JudgmentFinished, LeasedChunk, LeasedToolCall, Permit,
    PermitRequest, PermittedChunk, PinKind, PinResult, Purpose, ReadBatch, ScopeInfo, ScopeState,
    Sending, Store, StoredEvent, WorkCounts, WorkItem, WorkNext, WorkUpdate,
};
use core_memory_core::{RecallQuery, StoreError};
use postgres::types::{Json, ToSql};
use postgres::{Client, GenericClient, IsolationLevel, Row, SimpleQueryMessage, Transaction};
use time::OffsetDateTime;
use uuid::Uuid;

use crate::connect::{ConnectConfig, Connector};
use crate::error::{READ_ONLY, backend, commit, corrupt, governed};
use crate::migrate::{self, MigrationReport, SCHEMA_VERSION};

/// How an erase request ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EraseOutcome {
    Erased,
    AlreadyErased,
    /// The store holds no event with that ID.
    Unknown,
}

/// How an enrollment ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Enrollment {
    Enrolled,
    /// The scope was revoked and is active again.
    Reactivated,
    Unchanged,
}

/// One enrolled scope, for listing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopeSummary {
    pub scope: String,
    pub workspace: String,
    pub state: ScopeState,
    pub live_events: u64,
    pub erased_events: u64,
    /// The model destinations the scope's text may be sent to.
    pub destinations: Vec<String>,
}

/// What [`PostgresStore::queue_reads`] queues again although a model has
/// read it.
#[derive(Debug, Clone, Copy, Default)]
pub struct Reread<'a> {
    /// Chunks whose reads were all made by another reader, or with another
    /// vocabulary, than these.
    pub chunks: Option<(&'a ReaderRef, &'a CatalogRef)>,
    /// Tool calls whose outcomes were all written by another reader than
    /// this one.
    pub tool_calls: Option<&'a ReaderRef>,
}

/// How [`PostgresStore::queue_reads`] ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueuedReads {
    /// This many chunks and tool calls were added to the queue.
    Queued { chunks: u64, tool_calls: u64 },
    /// Nothing is read for a scope that is not active. `None` means it is
    /// not enrolled.
    ScopeNotActive(Option<ScopeState>),
}

/// The engine's store on one PostgreSQL connection.
///
/// The connection is opened on first use and reopened if it drops. Every
/// [`Store`] method is one transaction.
pub struct PostgresStore {
    connector: Connector,
    client: Option<Client>,
    /// Whether the schema version was checked on this connection.
    checked: bool,
}

impl std::fmt::Debug for PostgresStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PostgresStore")
            .field("connected", &self.client.is_some())
            .finish_non_exhaustive()
    }
}

pub(crate) fn digest(bytes: &[u8], what: &str) -> Result<Sha256Digest, StoreError> {
    <[u8; 32]>::try_from(bytes)
        .map(Sha256Digest)
        .map_err(|_| corrupt(format!("{what} is not 32 bytes")))
}

pub(crate) fn to_i64(value: u64, what: &str) -> Result<i64, StoreError> {
    i64::try_from(value).map_err(|_| corrupt(format!("{what} {value} does not fit the store")))
}

pub(crate) fn to_u64(value: i64, what: &str) -> Result<u64, StoreError> {
    u64::try_from(value).map_err(|_| corrupt(format!("{what} {value} is negative")))
}

pub(crate) fn to_u32(value: i32, what: &str) -> Result<u32, StoreError> {
    u32::try_from(value).map_err(|_| corrupt(format!("{what} {value} is negative")))
}

pub(crate) fn to_i32(value: u32, what: &str) -> Result<i32, StoreError> {
    i32::try_from(value).map_err(|_| corrupt(format!("{what} {value} does not fit the store")))
}

fn scope_state(name: &str) -> Result<ScopeState, StoreError> {
    ScopeState::from_name(name).ok_or_else(|| corrupt(format!("unknown scope state `{name}`")))
}

fn erase_outcome(name: &str) -> Result<EraseOutcome, StoreError> {
    match name {
        "erased" => Ok(EraseOutcome::Erased),
        "already_erased" => Ok(EraseOutcome::AlreadyErased),
        "unknown" => Ok(EraseOutcome::Unknown),
        other => Err(corrupt(format!("unknown erase result `{other}`"))),
    }
}

impl PostgresStore {
    /// Validates the settings. The first call connects and checks that the
    /// database is at this build's schema version.
    pub fn new(config: &ConnectConfig) -> Result<Self, StoreError> {
        Ok(Self::with_connector(Connector::new(config)?))
    }

    pub(crate) fn with_connector(connector: Connector) -> Self {
        Self {
            connector,
            client: None,
            checked: false,
        }
    }

    /// Whether the connection is known to be gone.
    pub fn is_closed(&self) -> bool {
        self.client.as_ref().is_some_and(Client::is_closed)
    }

    fn raw(&mut self) -> Result<&mut Client, StoreError> {
        if self.is_closed() {
            self.client = None;
            self.checked = false;
        }
        let client = match self.client.take() {
            Some(client) => client,
            None => self.connector.connect()?,
        };
        Ok(self.client.insert(client))
    }

    /// The connection, after checking the schema version once.
    pub(crate) fn client(&mut self) -> Result<&mut Client, StoreError> {
        if !self.checked {
            migrate::require_current(self.raw()?)?;
            self.checked = true;
        }
        self.raw()
    }

    /// Passes a result through. A connection to a database that takes no
    /// writes is dropped, so the next call connects again and may reach a
    /// server that does.
    pub(crate) fn settled<T>(&mut self, result: Result<T, StoreError>) -> Result<T, StoreError> {
        if matches!(&result, Err(StoreError::Unavailable(why)) if why.starts_with(READ_ONLY)) {
            self.client = None;
            self.checked = false;
        }
        result
    }

    /// Applies pending migrations.
    pub fn migrate(&mut self) -> Result<MigrationReport, StoreError> {
        let report = migrate::migrate(self.raw()?);
        // Migrating lifts the session's limits. The next call reconnects
        // with the usual ones.
        self.client = None;
        self.checked = false;
        report
    }

    /// The database's schema version, or `None` if it has no schema.
    pub fn schema_version(&mut self) -> Result<Option<u32>, StoreError> {
        migrate::current_version(self.raw()?)
    }

    /// Fails unless the store is reachable, at this build's schema version
    /// and taking writes.
    pub fn check(&mut self) -> Result<(), StoreError> {
        self.checked = false;
        let checked = self.client().and_then(|client| {
            let row = client
                .query_one("SELECT current_setting('transaction_read_only')", &[])
                .map_err(backend)?;
            // A standby answers reads and refuses every write.
            if row.get::<_, &str>(0) == "on" {
                return Err(StoreError::Unavailable(READ_ONLY.to_string()));
            }
            Ok(())
        });
        self.settled(checked)
    }

    // ---- governance: the functions a host or an operator calls ----

    /// Runs one governance function and returns its one row.
    fn govern(&mut self, sql: &str, params: &[&(dyn ToSql + Sync)]) -> Result<Row, StoreError> {
        let row = self
            .client()
            .and_then(|client| client.query_one(sql, params).map_err(governed));
        self.settled(row)
    }

    /// Creates the workspace. Returns false if it already exists.
    pub fn create_workspace(&mut self, workspace: &str) -> Result<bool, StoreError> {
        let row = self.govern("SELECT core_memory.create_workspace($1)", &[&workspace])?;
        Ok(row.get(0))
    }

    pub fn enroll_scope(&mut self, scope: &str, workspace: &str) -> Result<Enrollment, StoreError> {
        let row = self.govern(
            "SELECT core_memory.enroll_scope($1, $2)",
            &[&scope, &workspace],
        )?;
        match row.get::<_, &str>(0) {
            "enrolled" => Ok(Enrollment::Enrolled),
            "reactivated" => Ok(Enrollment::Reactivated),
            "unchanged" => Ok(Enrollment::Unchanged),
            other => Err(corrupt(format!("unknown enrollment result `{other}`"))),
        }
    }

    /// Pauses, resumes or revokes a scope. Returns the state it had.
    pub fn set_scope_state(
        &mut self,
        scope: &str,
        state: ScopeState,
    ) -> Result<ScopeState, StoreError> {
        let row = self.govern(
            "SELECT core_memory.set_scope_state($1, $2)",
            &[&scope, &state.as_str()],
        )?;
        scope_state(row.get(0))
    }

    /// Erases one whole message.
    pub fn erase_event(
        &mut self,
        event_id: &EventId,
        reason: &str,
        actor: &str,
    ) -> Result<EraseOutcome, StoreError> {
        let row = self.govern(
            "SELECT core_memory.erase_event($1, $2, $3)",
            &[&event_id.0, &reason, &actor],
        )?;
        erase_outcome(row.get(0))
    }

    /// Erases many whole messages in one transaction, and returns the
    /// outcome for each distinct ID given, in ID order.
    ///
    /// The transaction takes each workspace once, so this is the way to
    /// erase more than a handful of events.
    pub fn erase_events(
        &mut self,
        event_ids: &[EventId],
        reason: &str,
        actor: &str,
    ) -> Result<Vec<(EventId, EraseOutcome)>, StoreError> {
        let ids: Vec<Uuid> = event_ids.iter().map(|id| id.0).collect();
        let rows = self.client().and_then(|client| {
            client
                .query(
                    "SELECT event_id, outcome FROM core_memory.erase_events($1, $2, $3)",
                    &[&ids, &reason, &actor],
                )
                .map_err(governed)
        });
        self.settled(rows)?
            .iter()
            .map(|row| Ok((EventId(row.get(0)), erase_outcome(row.get(1))?)))
            .collect()
    }

    /// Lets a scope's text be sent to a model destination: the host, and
    /// port if any, of a binding's endpoint. Returns false if it already
    /// could be.
    pub fn grant_destination(
        &mut self,
        scope: &str,
        destination: &str,
    ) -> Result<bool, StoreError> {
        let row = self.govern(
            "SELECT core_memory.grant_destination($1, $2)",
            &[&scope, &destination],
        )?;
        Ok(row.get::<_, &str>(0) == "granted")
    }

    /// Stops a scope's text from being sent to a model destination. A call
    /// that was already permitted is not recalled; no later one is
    /// permitted. Returns false if nothing was granted.
    pub fn revoke_destination(
        &mut self,
        scope: &str,
        destination: &str,
    ) -> Result<bool, StoreError> {
        let row = self.govern(
            "SELECT core_memory.revoke_destination($1, $2)",
            &[&scope, &destination],
        )?;
        Ok(row.get::<_, &str>(0) == "revoked")
    }

    /// Queues what a model has still to read in a scope's live events:
    /// chunks no model has read and tool calls no model has read, and what
    /// `reread` adds to them. Work already queued stays as it is.
    ///
    /// Admission queues what it stores while a reader is configured. This
    /// is for what was stored before one was, or before it changed.
    pub fn queue_reads(
        &mut self,
        scope: &str,
        reread: Reread<'_>,
        at: Timestamp,
    ) -> Result<QueuedReads, StoreError> {
        let queued = self.write_queue(scope, reread, at);
        self.settled(queued)
    }

    fn write_queue(
        &mut self,
        scope: &str,
        reread: Reread<'_>,
        at: Timestamp,
    ) -> Result<QueuedReads, StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        // The workspace is held, so no event of the scope is erased
        // between being found live and being queued.
        match begin_write(&mut tx, scope, None)? {
            None => return Ok(QueuedReads::ScopeNotActive(None)),
            Some(writing) if writing.state != ScopeState::Active => {
                return Ok(QueuedReads::ScopeNotActive(Some(writing.state)));
            }
            Some(_) => {}
        }
        let current = reread.chunks.map(|(reader, vocabulary)| {
            (
                reader.binding_rev.as_bytes().as_slice(),
                vocabulary.hash.as_bytes().as_slice(),
            )
        });
        let chunks = tx
            .execute(
                "INSERT INTO core_memory.work
                     (kind, item_id, event_id, workspace_id, scope, state, not_before,
                      enqueued_at)
                 SELECT 'chunk_read', c.chunk_id, e.event_id, e.workspace_id, e.scope, 'ready',
                        $2, $2
                   FROM core_memory.events AS e
                   JOIN core_memory.chunks AS c ON c.event_id = e.event_id
                  WHERE e.scope = $1 AND e.state = 'live'
                    AND e.info ->> 'content_kind' <> 'tool_call'
                    AND NOT EXISTS (
                        SELECT 1 FROM core_memory.chunk_reads AS r
                         WHERE r.chunk_id = c.chunk_id
                           AND ($3::bytea IS NULL
                                OR (r.binding_rev = $3 AND r.vocabulary_hash = $4)))
                 ON CONFLICT (kind, item_id) DO NOTHING",
                &[
                    &scope,
                    &at.0,
                    &current.map(|(binding, _)| binding),
                    &current.map(|(_, vocabulary)| vocabulary),
                ],
            )
            .map_err(backend)?;
        // A tool call is read whole, so its work is one item for the event.
        let tool_reader = reread
            .tool_calls
            .map(|reader| reader.binding_rev.as_bytes().as_slice());
        let tool_calls = tx
            .execute(
                "INSERT INTO core_memory.work
                     (kind, item_id, event_id, workspace_id, scope, state, not_before,
                      enqueued_at)
                 SELECT 'tool_read', e.event_id, e.event_id, e.workspace_id, e.scope, 'ready',
                        $2, $2
                   FROM core_memory.events AS e
                  WHERE e.scope = $1 AND e.state = 'live'
                    AND e.info ->> 'content_kind' = 'tool_call'
                    AND NOT EXISTS (
                        SELECT 1 FROM core_memory.tool_outcomes AS t
                         WHERE t.event_id = e.event_id
                           AND ($3::bytea IS NULL OR t.binding_rev = $3))
                 ON CONFLICT (kind, item_id) DO NOTHING",
                &[&scope, &at.0, &tool_reader],
            )
            .map_err(backend)?;
        tx.commit().map_err(backend)?;
        Ok(QueuedReads::Queued { chunks, tool_calls })
    }

    /// Puts reads that were set aside as failed back in the queue, with
    /// their attempts counted from the start: all of them, or one scope's.
    /// Returns how many.
    pub fn retry_failed_reads(
        &mut self,
        scope: Option<&str>,
        at: Timestamp,
    ) -> Result<u64, StoreError> {
        let retried = self.write_retry(scope, at);
        self.settled(retried)
    }

    fn write_retry(&mut self, scope: Option<&str>, at: Timestamp) -> Result<u64, StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        let retried = tx
            .execute(
                "UPDATE core_memory.work
                    SET state = 'ready', attempts = 0, not_before = $2, problem = NULL
                  WHERE state = 'failed' AND ($1::text IS NULL OR scope = $1)",
                &[&scope, &at.0],
            )
            .map_err(backend)?;
        tx.commit().map_err(backend)?;
        Ok(retried)
    }

    /// Erases every live revision of one source object: every event of the
    /// scope with this `source_ns` and `external_id`. Returns how many
    /// were erased.
    pub fn erase_key(
        &mut self,
        scope: &str,
        source_ns: &str,
        external_id: &str,
        reason: &str,
        actor: &str,
    ) -> Result<u64, StoreError> {
        let row = self.govern(
            "SELECT core_memory.erase_key($1, $2, $3, $4, $5)",
            &[&scope, &source_ns, &external_id, &reason, &actor],
        )?;
        to_u64(row.get(0), "erased count")
    }

    /// Erases live events of a scope, oldest first: all of them, or at most
    /// `max_events`. Returns how many were erased; 0 means none are left.
    ///
    /// One call is one transaction that holds the scope's workspace, so a
    /// large scope is better erased in batches, after it is paused or
    /// revoked.
    pub fn erase_scope(
        &mut self,
        scope: &str,
        reason: &str,
        actor: &str,
        max_events: Option<u32>,
    ) -> Result<u64, StoreError> {
        let max_events = max_events.map(|n| i32::try_from(n).unwrap_or(i32::MAX));
        let row = self.govern(
            "SELECT core_memory.erase_scope($1, $2, $3, $4)",
            &[&scope, &reason, &actor, &max_events],
        )?;
        to_u64(row.get(0), "erased count")
    }

    /// Every workspace, by ID.
    pub fn workspaces(&mut self) -> Result<Vec<String>, StoreError> {
        let listed = self.list_workspaces();
        self.settled(listed)
    }

    fn list_workspaces(&mut self) -> Result<Vec<String>, StoreError> {
        let mut tx = read_transaction(self.client()?)?;
        let rows = tx
            .query(
                "SELECT workspace_id FROM core_memory.workspaces ORDER BY workspace_id",
                &[],
            )
            .map_err(backend)?;
        tx.commit().map_err(backend)?;
        Ok(rows.iter().map(|row| row.get(0)).collect())
    }

    /// Every enrolled scope, by name.
    pub fn scopes(&mut self) -> Result<Vec<ScopeSummary>, StoreError> {
        let listed = self.list_scopes();
        self.settled(listed)
    }

    fn list_scopes(&mut self) -> Result<Vec<ScopeSummary>, StoreError> {
        let mut tx = read_transaction(self.client()?)?;
        let rows = tx
            .query(
                "SELECT s.scope, s.workspace_id, s.state,
                        count(e.event_id) FILTER (WHERE e.state = 'live'),
                        count(e.event_id) FILTER (WHERE e.state = 'erased'),
                        ARRAY(SELECT d.destination FROM core_memory.scope_destinations AS d
                               WHERE d.scope = s.scope AND d.state = 'granted'
                               ORDER BY d.destination)
                   FROM core_memory.scopes AS s
                   LEFT JOIN core_memory.events AS e ON e.scope = s.scope
                  GROUP BY s.scope, s.workspace_id, s.state
                  ORDER BY s.scope",
                &[],
            )
            .map_err(backend)?;
        tx.commit().map_err(backend)?;
        rows.iter()
            .map(|row| {
                Ok(ScopeSummary {
                    scope: row.get(0),
                    workspace: row.get(1),
                    state: scope_state(row.get(2))?,
                    live_events: to_u64(row.get(3), "event count")?,
                    erased_events: to_u64(row.get(4), "event count")?,
                    destinations: row.get(5),
                })
            })
            .collect()
    }
}

// ---- the schema fence ----
//
// A migration locks `schema_migrations` exclusively before it changes
// anything. Everything here takes that table before any other, so a call
// that arrives during a migration waits for it holding nothing the
// migration needs, and then sees the version the migration left:
//
// - a write starts with `core_memory.begin_write`, which holds the schema
//   and checks the version;
// - a transaction of several statements starts with [`hold_schema`];
// - a read of one statement goes through [`read_one`], and its statement
//   has `schema_migrations` first in its FROM clause. PostgreSQL locks the
//   tables of a FROM clause in the order they are written there. A table
//   named anywhere else, in the select list for one, is locked after them.

/// Fails unless `stored`, the store's schema version as a statement just
/// read it, is this build's. A connection is checked once when it opens;
/// this catches a migration that ran since.
fn fence(stored: Option<i32>) -> Result<(), StoreError> {
    if stored == Some(SCHEMA_VERSION as i32) {
        return Ok(());
    }
    Err(StoreError::SchemaMismatch(format!(
        "the store is at schema version {}; this engine was built for version {SCHEMA_VERSION}",
        stored.map_or_else(|| "none".to_string(), |v| v.to_string())
    )))
}

/// What `core_memory.begin_write` found, with the transaction now the only
/// writer of the scope's workspace.
pub(crate) struct Writing {
    pub(crate) workspace: String,
    pub(crate) state: ScopeState,
    /// The first of the change-counter numbers taken, if any were asked for
    /// and the scope is active.
    pub(crate) first_seq: Option<i64>,
}

/// Starts a transaction's writes for `scope`. The store refuses an engine
/// built for another schema version, makes the transaction the only writer
/// of the scope's workspace until it ends, and reports the scope's state
/// under that lock. With `take`, an active scope's workspace also gives up
/// that many numbers from its change counter. `None` means the scope is
/// not enrolled.
pub(crate) fn begin_write<C: GenericClient>(
    tx: &mut C,
    scope: &str,
    take: Option<i32>,
) -> Result<Option<Writing>, StoreError> {
    let row = tx
        .query_opt(
            "SELECT workspace_id, state, first_seq FROM core_memory.begin_write($1, $2, $3)",
            &[&scope, &(SCHEMA_VERSION as i32), &take],
        )
        .map_err(backend)?;
    row.map(|r| {
        Ok(Writing {
            workspace: r.get(0),
            state: scope_state(r.get(1))?,
            first_seq: r.get(2),
        })
    })
    .transpose()
}

/// A transaction that writes. The workspace lock and the schema check both
/// rely on each statement seeing what committed before it, whatever the
/// database's default isolation level is.
pub(crate) fn write_transaction(client: &mut Client) -> Result<Transaction<'_>, StoreError> {
    client
        .build_transaction()
        .isolation_level(IsolationLevel::ReadCommitted)
        .start()
        .map_err(backend)
}

/// Holds the schema until the transaction ends, and fails unless it is
/// this build's. It must be the transaction's first statement.
///
/// Locking takes no snapshot. A transaction that reads from one snapshot
/// therefore takes it after any migration it waited for here, and no
/// migration starts while it runs. A snapshot taken before a migration
/// would show a table the migration rewrote as empty.
pub(crate) fn hold_schema(tx: &mut Transaction<'_>) -> Result<(), StoreError> {
    let reply = tx
        .simple_query(
            "LOCK TABLE core_memory.schema_migrations IN ACCESS SHARE MODE;
             SELECT max(version) FROM core_memory.schema_migrations",
        )
        .map_err(backend)?;
    let stored = reply.iter().find_map(|message| match message {
        SimpleQueryMessage::Row(row) => row.get(0),
        _ => None,
    });
    match stored.map(str::parse::<i32>) {
        Some(Ok(version)) => fence(Some(version)),
        Some(Err(_)) => Err(corrupt("the schema version is not a number")),
        None => fence(None),
    }
}

/// A read-only transaction on one snapshot of a store at this build's
/// schema version.
pub(crate) fn read_transaction(client: &mut Client) -> Result<Transaction<'_>, StoreError> {
    let mut tx = client
        .build_transaction()
        .isolation_level(IsolationLevel::RepeatableRead)
        .read_only(true)
        .start()
        .map_err(backend)?;
    hold_schema(&mut tx)?;
    Ok(tx)
}

/// Runs a read of one statement and returns its one row, from a store at
/// this build's schema version.
///
/// `sql` must give exactly one row whose first column is the schema
/// version, read from `schema_migrations` as the first table of its FROM
/// clause.
///
/// The statement shows the version only if all of it runs. After a
/// migration that renamed a column it reads, it no longer does, and the
/// failure would be reported as the store's own. So before a failure that
/// would repeat is reported, the version is read by itself. If the store
/// has moved to another version, that is what went wrong.
fn read_one<C: GenericClient>(
    client: &mut C,
    sql: &str,
    params: &[&(dyn ToSql + Sync)],
) -> Result<Row, StoreError> {
    let row = match client.query_one(sql, params) {
        Ok(row) => row,
        Err(e) => {
            let failed = backend(e);
            if !matches!(failed, StoreError::Backend(_)) {
                return Err(failed);
            }
            let stored = client
                .query_one(
                    "SELECT max(version) FROM core_memory.schema_migrations",
                    &[],
                )
                .ok()
                .and_then(|row| row.try_get::<_, Option<i32>>(0).ok());
            return Err(match stored.map(fence) {
                Some(Err(another_version)) => another_version,
                // This build's version, or a version that could not be
                // read: the first failure stands.
                _ => failed,
            });
        }
    };
    fence(row.get(0))?;
    Ok(row)
}

/// The stored event with this ID, read by one statement so that the event
/// and its chunk list are from one snapshot.
fn read_event<C: GenericClient>(
    client: &mut C,
    event_id: &EventId,
) -> Result<Option<StoredEvent>, StoreError> {
    let row = read_one(
        client,
        "SELECT v.version, e.state, e.event_hash,
                ARRAY(SELECT c.chunk_id FROM core_memory.chunks AS c
                       WHERE c.event_id = e.event_id ORDER BY c.ordinal)
           FROM (SELECT max(m.version) AS version
                   FROM core_memory.schema_migrations AS m) AS v
           LEFT JOIN core_memory.events AS e ON e.event_id = $1",
        &[&event_id.0],
    )?;
    match row.get::<_, Option<&str>>(1) {
        None => Ok(None),
        Some("erased") => Ok(Some(StoredEvent::Erased)),
        Some(_) => {
            let event_hash: Option<&[u8]> = row.get(2);
            let chunk_ids: Vec<Uuid> = row.get(3);
            Ok(Some(StoredEvent::Live {
                event_hash: digest(
                    event_hash.ok_or_else(|| corrupt("a live event has no hash"))?,
                    "event hash",
                )?,
                chunk_ids: chunk_ids.into_iter().map(ChunkId).collect(),
            }))
        }
    }
}

enum Advance {
    Advanced(CheckpointReceipt),
    Conflict(CheckpointState),
}

/// Moves `(scope, key)` from `expected_sequence` to `next_sequence`, or
/// reports what is stored. Offering an applied checkpoint again succeeds.
/// The caller's transaction holds the scope's workspace, so no other
/// transaction is reading or moving this checkpoint.
fn advance<C: GenericClient>(
    tx: &mut C,
    scope: &str,
    offered: &Checkpoint,
    at: Timestamp,
) -> Result<Advance, StoreError> {
    let stored = tx
        .query_opt(
            "SELECT sequence, hash FROM core_memory.checkpoints
              WHERE scope = $1 AND checkpoint_key = $2",
            &[&scope, &offered.key],
        )
        .map_err(backend)?;
    let state = match &stored {
        None => CheckpointState {
            sequence: 0,
            hash: None,
        },
        Some(row) => CheckpointState {
            sequence: to_u64(row.get(0), "checkpoint sequence")?,
            hash: Some(digest(row.get(1), "checkpoint hash")?),
        },
    };
    let receipt = CheckpointReceipt {
        key: offered.key.clone(),
        sequence: offered.next_sequence,
    };
    if state.sequence == offered.expected_sequence {
        tx.execute(
            "INSERT INTO core_memory.checkpoints (scope, checkpoint_key, sequence, hash, updated_at)
             VALUES ($1, $2, $3, $4, $5)
             ON CONFLICT (scope, checkpoint_key) DO UPDATE
                SET sequence = EXCLUDED.sequence, hash = EXCLUDED.hash,
                    updated_at = EXCLUDED.updated_at",
            &[
                &scope,
                &offered.key,
                &to_i64(offered.next_sequence, "checkpoint sequence")?,
                &offered.hash.as_bytes().as_slice(),
                &at.0,
            ],
        )
        .map_err(backend)?;
        return Ok(Advance::Advanced(receipt));
    }
    if state.sequence == offered.next_sequence && state.hash == Some(offered.hash) {
        return Ok(Advance::Advanced(receipt));
    }
    Ok(Advance::Conflict(state))
}

impl Store for PostgresStore {
    fn scope(&mut self, scope: &str) -> Result<Option<ScopeInfo>, StoreError> {
        let found = self.read_scope(scope);
        self.settled(found)
    }

    fn event(&mut self, event_id: &EventId) -> Result<Option<StoredEvent>, StoreError> {
        let found = self.read_stored_event(event_id);
        self.settled(found)
    }

    fn commit_admission(&mut self, w: &AdmissionWrite<'_>) -> Result<AdmissionCommit, StoreError> {
        let committed = self.write_admission(w);
        self.settled(committed)
    }

    fn advance_checkpoint(
        &mut self,
        scope: &str,
        checkpoint: &Checkpoint,
        at: Timestamp,
    ) -> Result<CheckpointAdvance, StoreError> {
        let advanced = self.write_checkpoint(scope, checkpoint, at);
        self.settled(advanced)
    }

    fn checkpoint(
        &mut self,
        scope: &str,
        key: &str,
    ) -> Result<Option<CheckpointState>, StoreError> {
        let found = self.read_checkpoint(scope, key);
        self.settled(found)
    }

    fn pin(
        &mut self,
        kind: PinKind,
        id: &str,
        rev: u32,
        hash: &Sha256Digest,
    ) -> Result<PinResult, StoreError> {
        let pinned = self.write_pin(kind, id, rev, hash);
        self.settled(pinned)
    }

    fn export(
        &mut self,
        workspace: &str,
        after: u64,
        limit: u32,
        scope: Option<&str>,
    ) -> Result<Vec<Change>, StoreError> {
        let exported = self.read_export(workspace, after, limit, scope);
        self.settled(exported)
    }

    fn recall(&mut self, query: &RecallQuery) -> Result<RecallAnswer, StoreError> {
        let answer = self.read_recall(query);
        self.settled(answer)
    }

    fn claim_reads(
        &mut self,
        limit: u32,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<ReadBatch>, StoreError> {
        let claimed = self.write_claim(limit, max_attempts, now, lease_until);
        self.settled(claimed)
    }

    fn claim_tool_read(
        &mut self,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<LeasedToolCall>, StoreError> {
        let claimed = self.write_claim_tool(max_attempts, now, lease_until);
        self.settled(claimed)
    }

    fn permit(&mut self, request: &PermitRequest<'_>) -> Result<Permit, StoreError> {
        let permit = self.write_permit(request);
        self.settled(permit)
    }

    fn finish_call(&mut self, finish: &CallFinish<'_>) -> Result<CallFinished, StoreError> {
        let finished = self.write_finish(finish);
        self.settled(finished)
    }

    fn update_work(&mut self, work: &[WorkUpdate]) -> Result<(), StoreError> {
        let updated = self.write_work(work, None);
        self.settled(updated)
    }

    fn settle_judgment(&mut self, settle: &JudgeSettle<'_>) -> Result<(), StoreError> {
        let updated = self.write_work(settle.work, Some(settle.lease));
        self.settled(updated)
    }

    fn claim_judgment(&mut self, claim: &JudgeClaim) -> Result<Option<JudgeBatch>, StoreError> {
        let claimed = self.write_claim_judgment(claim);
        self.settled(claimed)
    }

    fn permit_judgment(
        &mut self,
        request: &JudgePermitRequest<'_>,
    ) -> Result<JudgePermit, StoreError> {
        let permit = self.write_permit_judgment(request);
        self.settled(permit)
    }

    fn finish_judgment(
        &mut self,
        finish: &JudgmentFinish<'_>,
    ) -> Result<JudgmentFinished, StoreError> {
        let finished = self.write_finish_judgment(finish);
        self.settled(finished)
    }

    fn work_counts(&mut self, purpose: Purpose) -> Result<WorkCounts, StoreError> {
        let counts = self.read_work_counts(purpose);
        self.settled(counts)
    }
}

impl PostgresStore {
    fn read_scope(&mut self, scope: &str) -> Result<Option<ScopeInfo>, StoreError> {
        // Always one row, so the schema version comes back even when the
        // scope is not enrolled. Every admission starts here.
        let row = read_one(
            self.client()?,
            "SELECT v.version, s.workspace_id, s.state, w.id_key
               FROM (SELECT max(m.version) AS version
                       FROM core_memory.schema_migrations AS m) AS v
               LEFT JOIN core_memory.scopes AS s ON s.scope = $1
               LEFT JOIN core_memory.workspaces AS w ON w.workspace_id = s.workspace_id",
            &[&scope],
        )?;
        let Some(workspace) = row.get::<_, Option<String>>(1) else {
            return Ok(None);
        };
        let id_key: Option<&[u8]> = row.get(3);
        Ok(Some(ScopeInfo {
            workspace,
            state: scope_state(row.get(2))?,
            id_key: id_key
                .and_then(IdKey::from_slice)
                .ok_or_else(|| corrupt("a workspace ID key is not 32 bytes"))?,
        }))
    }

    fn read_stored_event(&mut self, event_id: &EventId) -> Result<Option<StoredEvent>, StoreError> {
        read_event(self.client()?, event_id)
    }

    fn write_admission(&mut self, w: &AdmissionWrite<'_>) -> Result<AdmissionCommit, StoreError> {
        let scope = w.key.scope.as_str();
        let chunk_count = i32::try_from(w.chunks.len())
            .ok()
            .filter(|n| *n < i32::MAX)
            .ok_or_else(|| corrupt("an event has too many chunks"))?;
        let mut tx = write_transaction(self.client()?)?;

        // The scope must be active for as long as this transaction runs.
        // One number for the event and one for each bead.
        let beads = i32::try_from(w.beads.count(w.chunks.len()))
            .map_err(|_| corrupt("an event has too many chunks"))?;
        let event_seq = match begin_write(&mut tx, scope, Some(beads + 1))? {
            None => return Ok(AdmissionCommit::ScopeNotActive(None)),
            Some(writing) if writing.state != ScopeState::Active => {
                return Ok(AdmissionCommit::ScopeNotActive(Some(writing.state)));
            }
            Some(writing) if writing.workspace != w.workspace => {
                return Err(corrupt(format!(
                    "scope `{scope}` moved from workspace `{}` to `{}`",
                    w.workspace, writing.workspace
                )));
            }
            Some(writing) => writing
                .first_seq
                .ok_or_else(|| corrupt("an active scope's workspace gave no change number"))?,
        };

        // An edit is stored only while the record it edits is live. Erasing
        // holds the same workspace, so this holds until the commit.
        if let Some(revised) = w.revises {
            let state = tx
                .query_opt(
                    "SELECT state FROM core_memory.events WHERE event_id = $1",
                    &[&revised.0],
                )
                .map_err(backend)?;
            match state.as_ref().map(|row| row.get::<_, &str>(0)) {
                Some("live") => {}
                Some(_) => return Ok(AdmissionCommit::RevisedEventErased),
                None => return Err(corrupt("an edit names an event that is not stored")),
            }
        }

        let inserted = tx
            .execute(
                "INSERT INTO core_memory.events (
                     event_id, workspace_id, scope, state,
                     source_ns, external_id, revision, engine_assigned_revision,
                     event_hash, content_hash, info,
                     profile_id, profile_rev, chunker_id, chunker_rev, chunker_config_hash,
                     chunk_count, time_at, time_basis, captured_at, seq
                 ) VALUES (
                     $1, $2, $3, 'live', $4, $5, $6, $7, $8, $9, $10,
                     $11, $12, $13, $14, $15, $16, $17, $18, $19, $20
                 )
                 ON CONFLICT (event_id) DO NOTHING",
                &[
                    &w.event_id.0,
                    &w.workspace,
                    &scope,
                    &w.key.source_ns,
                    &w.key.external_id,
                    &w.key.revision,
                    &w.engine_assigned_revision,
                    &w.event_hash.as_bytes().as_slice(),
                    &w.content_hash.as_bytes().as_slice(),
                    &Json(w.details),
                    &w.profile.id,
                    &to_i32(w.profile.rev, "profile revision")?,
                    &w.chunker.id,
                    &to_i32(w.chunker.rev, "chunker revision")?,
                    &w.chunker.config_hash.as_bytes().as_slice(),
                    &chunk_count,
                    &w.time_at.0,
                    &w.time_basis.as_str(),
                    &w.captured_at.0,
                    &event_seq,
                ],
            )
            .map_err(backend)?;
        if inserted == 0 {
            // Another admission of this key committed first. Dropping the
            // transaction gives the numbers back.
            let stored = read_event(&mut tx, &w.event_id)?
                .ok_or_else(|| corrupt("an event conflicted and then was not found"))?;
            return Ok(AdmissionCommit::AlreadyPresent(stored));
        }

        let mut ids = Vec::with_capacity(w.chunks.len());
        let mut ordinals = Vec::with_capacity(w.chunks.len());
        let mut ranges: [Vec<i64>; 4] = Default::default();
        let mut texts = Vec::with_capacity(w.chunks.len());
        let mut hashes = Vec::with_capacity(w.chunks.len());
        let mut facets = Vec::with_capacity(w.chunks.len());
        for c in w.chunks {
            ids.push(c.chunk_id.0);
            ordinals.push(to_i32(c.ordinal, "chunk ordinal")?);
            for (column, value) in
                ranges
                    .iter_mut()
                    .zip([c.byte_start, c.byte_end, c.char_start, c.char_end])
            {
                column.push(to_i64(value, "chunk offset")?);
            }
            texts.push(c.text.as_str());
            hashes.push(c.content_hash.as_bytes().as_slice());
            facets.push(Json(&c.facets));
        }
        tx.execute(
            "INSERT INTO core_memory.chunks
                 (chunk_id, event_id, ordinal, byte_start, byte_end, char_start, char_end)
             SELECT c, $1, o, bs, be, cs, ce
               FROM UNNEST($2::uuid[], $3::int4[], $4::int8[], $5::int8[], $6::int8[], $7::int8[])
                    AS t (c, o, bs, be, cs, ce)",
            &[
                &w.event_id.0,
                &ids,
                &ordinals,
                &ranges[0],
                &ranges[1],
                &ranges[2],
                &ranges[3],
            ],
        )
        .map_err(backend)?;
        tx.execute(
            "INSERT INTO core_memory.chunk_text (chunk_id, scope, text, content_hash, facets)
             SELECT c, $1, t, h, f
               FROM UNNEST($2::uuid[], $3::text[], $4::bytea[], $5::jsonb[]) AS u (c, t, h, f)",
            &[&scope, &ids, &texts, &hashes, &facets],
        )
        .map_err(backend)?;
        // The queue holds IDs only. The text is read when a call is
        // permitted, so erasing it in between leaves nothing to send.
        match w.queue {
            None => {}
            Some(Purpose::ChunkRead) => {
                tx.execute(
                    "INSERT INTO core_memory.work
                         (kind, item_id, event_id, workspace_id, scope, state, not_before,
                          enqueued_at)
                     SELECT 'chunk_read', c, $1, $2, $3, 'ready', $4, $4
                       FROM UNNEST($5::uuid[]) AS u (c)",
                    &[&w.event_id.0, &w.workspace, &scope, &w.captured_at.0, &ids],
                )
                .map_err(backend)?;
            }
            Some(Purpose::ToolRead) => {
                tx.execute(
                    "INSERT INTO core_memory.work
                         (kind, item_id, event_id, workspace_id, scope, state, not_before,
                          enqueued_at)
                     VALUES ('tool_read', $1, $1, $2, $3, 'ready', $4, $4)",
                    &[&w.event_id.0, &w.workspace, &scope, &w.captured_at.0],
                )
                .map_err(backend)?;
            }
            Some(Purpose::Judge | Purpose::SecondRead) => {
                return Err(corrupt(
                    "an admission queues reads, and names the judge apart",
                ));
            }
        }
        // Each chunk's bead is owed to the judge, not before the given
        // time. A tool call's bead is owed once a model has written about
        // the call, and is queued then.
        if let (Some(not_before), BeadsOf::Chunks) = (w.judge, w.beads) {
            tx.execute(
                "INSERT INTO core_memory.work
                     (kind, item_id, event_id, workspace_id, scope, state, not_before,
                      enqueued_at)
                 SELECT 'judge', c, $1, $2, $3, 'ready', $4, $5
                   FROM UNNEST($6::uuid[]) AS u (c)",
                &[
                    &w.event_id.0,
                    &w.workspace,
                    &scope,
                    &not_before.0,
                    &w.captured_at.0,
                    &ids,
                ],
            )
            .map_err(backend)?;
        }
        match w.beads {
            // Every chunk of a message is a bead, in chunk order, numbered
            // straight after the event.
            BeadsOf::Chunks => {
                tx.execute(
                    "INSERT INTO core_memory.beads (
                         bead_id, workspace_id, scope, kind, state, event_id, chunk_id,
                         created_by, time_at, time_basis, created_at, seq
                     )
                     SELECT c, $1, $2, 'chunk', 'live', $3, c, 'engine', $4, $5, $6, $8 + n
                       FROM UNNEST($7::uuid[]) WITH ORDINALITY AS u (c, n)
                      ORDER BY n",
                    &[
                        &w.workspace,
                        &scope,
                        &w.event_id.0,
                        &w.time_at.0,
                        &w.time_basis.as_str(),
                        &w.captured_at.0,
                        &ids,
                        &event_seq,
                    ],
                )
                .map_err(backend)?;
            }
            // A tool call is one bead, with its event's ID.
            BeadsOf::Event => {
                tx.execute(
                    "INSERT INTO core_memory.beads (
                         bead_id, workspace_id, scope, kind, state, event_id, chunk_id,
                         created_by, time_at, time_basis, created_at, seq
                     )
                     VALUES ($3, $1, $2, 'tool', 'live', $3, NULL, 'engine', $4, $5, $6,
                             $7::int8 + 1)",
                    &[
                        &w.workspace,
                        &scope,
                        &w.event_id.0,
                        &w.time_at.0,
                        &w.time_basis.as_str(),
                        &w.captured_at.0,
                        &event_seq,
                    ],
                )
                .map_err(backend)?;
            }
        }

        let checkpoint = match w.checkpoint {
            None => None,
            Some(offered) => match advance(&mut tx, scope, offered, w.captured_at)? {
                Advance::Advanced(receipt) => Some(receipt),
                // Dropping the transaction rolls the event back.
                Advance::Conflict(stored) => {
                    return Ok(AdmissionCommit::CheckpointConflict(stored));
                }
            },
        };
        tx.commit().map_err(backend)?;
        Ok(AdmissionCommit::Committed { checkpoint })
    }

    fn write_checkpoint(
        &mut self,
        scope: &str,
        checkpoint: &Checkpoint,
        at: Timestamp,
    ) -> Result<CheckpointAdvance, StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        match begin_write(&mut tx, scope, None)? {
            None => return Ok(CheckpointAdvance::ScopeNotActive(None)),
            Some(writing) if writing.state != ScopeState::Active => {
                return Ok(CheckpointAdvance::ScopeNotActive(Some(writing.state)));
            }
            Some(_) => {}
        }
        match advance(&mut tx, scope, checkpoint, at)? {
            Advance::Advanced(receipt) => {
                tx.commit().map_err(backend)?;
                Ok(CheckpointAdvance::Advanced(receipt))
            }
            Advance::Conflict(stored) => Ok(CheckpointAdvance::Conflict(stored)),
        }
    }

    fn read_checkpoint(
        &mut self,
        scope: &str,
        key: &str,
    ) -> Result<Option<CheckpointState>, StoreError> {
        let row = read_one(
            self.client()?,
            "SELECT v.version, c.sequence, c.hash
               FROM (SELECT max(m.version) AS version
                       FROM core_memory.schema_migrations AS m) AS v
               LEFT JOIN core_memory.checkpoints AS c
                      ON c.scope = $1 AND c.checkpoint_key = $2",
            &[&scope, &key],
        )?;
        let Some(sequence) = row.get::<_, Option<i64>>(1) else {
            return Ok(None);
        };
        let hash: Option<&[u8]> = row.get(2);
        Ok(Some(CheckpointState {
            sequence: to_u64(sequence, "checkpoint sequence")?,
            hash: Some(digest(
                hash.ok_or_else(|| corrupt("a stored checkpoint has no hash"))?,
                "checkpoint hash",
            )?),
        }))
    }

    fn write_pin(
        &mut self,
        kind: PinKind,
        id: &str,
        rev: u32,
        hash: &Sha256Digest,
    ) -> Result<PinResult, StoreError> {
        let rev = to_i32(rev, "revision")?;
        // A pin belongs to no workspace, so there is no begin_write to
        // check the schema version for it.
        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        let inserted = tx
            .execute(
                "INSERT INTO core_memory.pins (kind, id, rev, hash) VALUES ($1, $2, $3, $4)
                 ON CONFLICT (kind, id, rev) DO NOTHING",
                &[&kind.as_str(), &id, &rev, &hash.as_bytes().as_slice()],
            )
            .map_err(backend)?;
        if inserted == 1 {
            tx.commit().map_err(backend)?;
            return Ok(PinResult::Pinned);
        }
        let row = tx
            .query_one(
                "SELECT hash FROM core_memory.pins WHERE kind = $1 AND id = $2 AND rev = $3",
                &[&kind.as_str(), &id, &rev],
            )
            .map_err(backend)?;
        tx.commit().map_err(backend)?;
        let stored = digest(row.get(0), "pin hash")?;
        Ok(if stored == *hash {
            PinResult::Matches
        } else {
            PinResult::Mismatch { stored }
        })
    }

    fn read_export(
        &mut self,
        workspace: &str,
        after: u64,
        limit: u32,
        scope: Option<&str>,
    ) -> Result<Vec<Change>, StoreError> {
        // One snapshot, so every change comes with exactly what belongs
        // to it.
        let mut tx = read_transaction(self.client()?)?;
        let after = to_i64(after, "export position")?;
        // The next `limit` changes across the numbered tables that are
        // exported. A read or a tool outcome exists only while its event
        // is live, so no state check is needed for it.
        let page = tx
            .query(
                &format!(
                    "SELECT seq FROM (
                         SELECT e.seq FROM core_memory.events AS e
                          WHERE e.workspace_id = $1 AND e.seq > $2 AND e.state = 'live'
                            AND ($3::text IS NULL OR e.scope = $3)
                         UNION ALL
                         SELECT r.seq FROM core_memory.chunk_reads AS r
                          WHERE r.workspace_id = $1 AND r.seq > $2
                            AND ($3::text IS NULL OR r.scope = $3)
                         UNION ALL
                         SELECT t.seq FROM core_memory.tool_outcomes AS t
                          WHERE t.workspace_id = $1 AND t.seq > $2
                            AND ($3::text IS NULL OR t.scope = $3)
                         UNION ALL
                         SELECT j.seq FROM core_memory.erasures AS j
                          WHERE j.workspace_id = $1 AND j.seq > $2
                            AND ($3::text IS NULL OR j.scope = $3)
                         {judged}
                     ) AS changes
                     ORDER BY seq
                     LIMIT $4",
                    judged = crate::judge::exported_seqs()
                ),
                &[&workspace, &after, &scope, &i64::from(limit)],
            )
            .map_err(backend)?;
        let Some(last) = page.last().map(|row| row.get::<_, i64>(0)) else {
            return Ok(Vec::new());
        };
        let mut changes: BTreeMap<u64, Change> = BTreeMap::new();

        // ---- events and their chunks ----
        let rows = tx
            .query(
                "SELECT event_id, seq, workspace_id, scope, source_ns, external_id,
                        revision, engine_assigned_revision, event_hash, content_hash, info,
                        profile_id, profile_rev, chunker_id, chunker_rev, chunker_config_hash,
                        chunk_count, time_at, time_basis, captured_at
                   FROM core_memory.events
                  WHERE workspace_id = $1 AND state = 'live' AND seq > $2 AND seq <= $3
                    AND ($4::text IS NULL OR scope = $4)
                  ORDER BY seq",
                &[&workspace, &after, &last, &scope],
            )
            .map_err(backend)?;
        let mut events = Vec::with_capacity(rows.len());
        for row in &rows {
            let event_id = EventId(row.get::<_, Uuid>(0));
            let scope: String = row.get(3);
            let Json(details): Json<EventDetails> = row.try_get(10).map_err(|e| {
                corrupt(format!("event {event_id} metadata does not read back: {e}"))
            })?;
            let basis: &str = row.get(18);
            events.push(ExportedEvent {
                event: EventRecord {
                    event_id,
                    workspace: row.get(2),
                    seq: to_u64(row.get(1), "change number")?,
                    key: EventKey {
                        scope,
                        source_ns: row.get(4),
                        external_id: row.get(5),
                        revision: row.get(6),
                    },
                    engine_assigned_revision: row.get(7),
                    event_hash: digest(row.get(8), "event hash")?,
                    content_hash: digest(row.get(9), "content hash")?,
                    details,
                    source_profile: ProfileRef {
                        id: row.get(11),
                        rev: to_u32(row.get(12), "profile revision")?,
                    },
                    chunker: ChunkerRef {
                        id: row.get(13),
                        rev: to_u32(row.get(14), "chunker revision")?,
                        config_hash: digest(row.get(15), "chunker config hash")?,
                    },
                    captured_at: Timestamp(row.get::<_, OffsetDateTime>(19)),
                    time_at: Timestamp(row.get::<_, OffsetDateTime>(17)),
                    time_basis: TimeBasis::from_name(basis)
                        .ok_or_else(|| corrupt(format!("unknown time basis `{basis}`")))?,
                    chunk_count: to_u32(row.get(16), "chunk count")?,
                },
                chunks: Vec::new(),
            });
        }
        let ids: Vec<Uuid> = events.iter().map(|e| e.event.event_id.0).collect();
        let rows = tx
            .query(
                "SELECT c.event_id, c.chunk_id, c.ordinal, c.byte_start, c.byte_end,
                        c.char_start, c.char_end, t.content_hash, t.text, t.facets
                   FROM core_memory.chunks AS c
                   JOIN core_memory.chunk_text AS t USING (chunk_id)
                  WHERE c.event_id = ANY ($1)
                  ORDER BY c.event_id, c.ordinal",
                &[&ids],
            )
            .map_err(backend)?;
        let mut by_event: BTreeMap<Uuid, Vec<ChunkRecord>> = BTreeMap::new();
        for row in &rows {
            let event_id: Uuid = row.get(0);
            let chunk_id = ChunkId(row.get::<_, Uuid>(1));
            let Json(facets): Json<ChunkFacets> = row
                .try_get(9)
                .map_err(|e| corrupt(format!("chunk {chunk_id} facets do not read back: {e}")))?;
            by_event.entry(event_id).or_default().push(ChunkRecord {
                chunk_id,
                event_id: EventId(event_id),
                ordinal: to_u32(row.get(2), "chunk ordinal")?,
                byte_start: to_u64(row.get(3), "chunk offset")?,
                byte_end: to_u64(row.get(4), "chunk offset")?,
                char_start: to_u64(row.get(5), "chunk offset")?,
                char_end: to_u64(row.get(6), "chunk offset")?,
                content_hash: digest(row.get(7), "chunk content hash")?,
                text: row.get(8),
                facets,
            });
        }
        for mut exported in events {
            exported.chunks = by_event
                .remove(&exported.event.event_id.0)
                .unwrap_or_default();
            // A live event always has its text. Without it the export
            // would silently be short.
            if exported.chunks.len() != exported.event.chunk_count as usize {
                return Err(corrupt(format!(
                    "event {} has {} chunks with text, not {}",
                    exported.event.event_id,
                    exported.chunks.len(),
                    exported.event.chunk_count
                )));
            }
            changes.insert(exported.event.seq, Change::Event(Box::new(exported)));
        }

        // ---- reads and their spans ----
        let rows = tx
            .query(
                "SELECT seq, chunk_id, scope, call_id, status, type_key, type_rev, abstained,
                        vocabulary_id, vocabulary_hash, binding_id, binding_rev, model,
                        prompt_rev, spans_rejected, read_at
                   FROM core_memory.chunk_reads
                  WHERE workspace_id = $1 AND seq > $2 AND seq <= $3
                    AND ($4::text IS NULL OR scope = $4)
                  ORDER BY seq",
                &[&workspace, &after, &last, &scope],
            )
            .map_err(backend)?;
        let read_seqs: Vec<i64> = rows.iter().map(|row| row.get(0)).collect();
        let span_rows = tx
            .query(
                "SELECT read_seq, kind, entity_kind, byte_start, byte_end
                   FROM core_memory.chunk_read_spans
                  WHERE workspace_id = $1 AND read_seq = ANY ($2)
                  ORDER BY read_seq, ordinal",
                &[&workspace, &read_seqs],
            )
            .map_err(backend)?;
        let mut spans: BTreeMap<i64, Vec<ReadSpan>> = BTreeMap::new();
        for row in &span_rows {
            let (kind, entity_kind): (&str, Option<&str>) = (row.get(1), row.get(2));
            spans.entry(row.get(0)).or_default().push(ReadSpan {
                kind: SpanKind::from_names(kind, entity_kind)
                    .ok_or_else(|| corrupt(format!("unknown span kind `{kind}`")))?,
                byte_start: to_u64(row.get(3), "span offset")?,
                byte_end: to_u64(row.get(4), "span offset")?,
            });
        }
        for row in &rows {
            let seq: i64 = row.get(0);
            let (status, type_key, type_rev, abstained): (
                &str,
                Option<String>,
                Option<i32>,
                Option<&str>,
            ) = (row.get(4), row.get(5), row.get(6), row.get(7));
            let decision = match (status, type_key, type_rev, abstained) {
                ("typed", Some(key), Some(rev), None) => ReadDecision::Typed {
                    type_ref: TypeRef {
                        key,
                        rev: to_u32(rev, "type revision")?,
                    },
                },
                ("abstained", None, None, Some(reason)) => ReadDecision::Abstained {
                    reason: Abstention::from_name(reason)
                        .ok_or_else(|| corrupt(format!("unknown abstention `{reason}`")))?,
                },
                _ => {
                    return Err(corrupt(format!(
                        "read {seq} is neither typed nor abstained"
                    )));
                }
            };
            let read = ChunkReadRecord {
                chunk_id: ChunkId(row.get::<_, Uuid>(1)),
                workspace: workspace.to_string(),
                seq: to_u64(seq, "change number")?,
                decision,
                vocabulary: CatalogRef {
                    id: row.get(8),
                    hash: digest(row.get(9), "vocabulary hash")?,
                },
                reader: ReaderRef {
                    binding_id: row.get(10),
                    binding_rev: digest(row.get(11), "binding revision")?,
                    model: row.get(12),
                    prompt_rev: to_u32(row.get(13), "prompt revision")?,
                },
                call_id: CallId(row.get::<_, Uuid>(3)),
                spans: spans.remove(&seq).unwrap_or_default(),
                spans_rejected: to_u32(row.get(14), "rejected spans")?,
                read_at: Timestamp(row.get::<_, OffsetDateTime>(15)),
            };
            changes.insert(
                read.seq,
                Change::Read {
                    scope: row.get(2),
                    read: Box::new(read),
                },
            );
        }

        // ---- tool outcomes ----
        let rows = tx
            .query(
                "SELECT seq, event_id, scope, call_id, status, action, outcome, compressor_id,
                        compressor_rev, compressed_bytes, binding_id, binding_rev, model,
                        prompt_rev, read_at
                   FROM core_memory.tool_outcomes
                  WHERE workspace_id = $1 AND seq > $2 AND seq <= $3
                    AND ($4::text IS NULL OR scope = $4)
                  ORDER BY seq",
                &[&workspace, &after, &last, &scope],
            )
            .map_err(backend)?;
        for row in &rows {
            let event_id = EventId(row.get::<_, Uuid>(1));
            let status: &str = row.get(4);
            let outcome = ToolOutcomeRecord {
                event_id,
                bead_id: BeadId::from(event_id),
                workspace: workspace.to_string(),
                seq: to_u64(row.get(0), "change number")?,
                status: ToolStatus::from_name(status)
                    .ok_or_else(|| corrupt(format!("unknown tool status `{status}`")))?,
                action: row.get(5),
                outcome: row.get(6),
                compressor: CompressorRef {
                    id: row.get(7),
                    rev: to_u32(row.get(8), "compressor revision")?,
                },
                compressed_bytes: to_u64(row.get(9), "compressed bytes")?,
                reader: ReaderRef {
                    binding_id: row.get(10),
                    binding_rev: digest(row.get(11), "binding revision")?,
                    model: row.get(12),
                    prompt_rev: to_u32(row.get(13), "prompt revision")?,
                },
                call_id: CallId(row.get::<_, Uuid>(3)),
                read_at: Timestamp(row.get::<_, OffsetDateTime>(14)),
            };
            changes.insert(
                outcome.seq,
                Change::ToolOutcome {
                    scope: row.get(2),
                    outcome: Box::new(outcome),
                },
            );
        }

        // ---- erasures ----
        let rows = tx
            .query(
                "SELECT seq, event_id, scope, erased_at
                   FROM core_memory.erasures
                  WHERE workspace_id = $1 AND seq > $2 AND seq <= $3
                    AND ($4::text IS NULL OR scope = $4)
                  ORDER BY seq",
                &[&workspace, &after, &last, &scope],
            )
            .map_err(backend)?;
        for row in &rows {
            let erasure = ErasureRecord {
                event_id: EventId(row.get::<_, Uuid>(1)),
                workspace: workspace.to_string(),
                seq: to_u64(row.get(0), "change number")?,
                erased_at: Timestamp(row.get::<_, OffsetDateTime>(3)),
            };
            changes.insert(
                erasure.seq,
                Change::Erasure {
                    scope: row.get(2),
                    erasure,
                },
            );
        }
        crate::judge::export_judgments(&mut tx, workspace, (after, last), scope, &mut changes)?;
        tx.commit().map_err(backend)?;
        Ok(changes.into_values().collect())
    }

    // ---- model calls ----

    fn write_claim(
        &mut self,
        limit: u32,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<ReadBatch>, StoreError> {
        // Queue rows are claimed with SKIP LOCKED, so workers never wait
        // for each other. A lease that ran out is claimed like ready work.
        const DUE: &str = "w.kind = 'chunk_read'
            AND (w.state = 'ready' OR (w.state = 'leased' AND w.leased_until < $1))
            AND w.not_before <= $1";
        // The queue belongs to no one workspace, so there is no
        // begin_write to check the schema version for it.
        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        set_aside_spent(&mut tx, Purpose::ChunkRead, max_attempts, now)?;
        let Some(head) = tx
            .query_opt(
                &format!(
                    "SELECT w.item_id, w.scope, w.workspace_id, w.alone,
                            e.profile_id, e.profile_rev, e.seq, c.ordinal
                       FROM core_memory.work AS w
                       JOIN core_memory.chunks AS c ON c.chunk_id = w.item_id
                       JOIN core_memory.events AS e ON e.event_id = c.event_id
                      WHERE {DUE}
                      ORDER BY w.not_before, w.enqueued_at, w.item_id
                      LIMIT 1
                        FOR UPDATE OF w SKIP LOCKED"
                ),
                &[&now.0],
            )
            .map_err(backend)?
        else {
            // Nothing is due. What was set aside above still is.
            tx.commit().map_err(backend)?;
            return Ok(None);
        };
        let (scope, workspace): (String, String) = (head.get(1), head.get(2));
        let (profile_id, profile_rev): (String, i32) = (head.get(4), head.get(5));
        let mut order: Vec<(i64, i32, Uuid)> = vec![(head.get(6), head.get(7), head.get(0))];
        // A chunk that was part of a failed call goes alone from then on.
        if !head.get::<_, bool>(3) && limit > 1 {
            let rest = tx
                .query(
                    &format!(
                        "SELECT w.item_id, e.seq, c.ordinal
                           FROM core_memory.work AS w
                           JOIN core_memory.chunks AS c ON c.chunk_id = w.item_id
                           JOIN core_memory.events AS e ON e.event_id = c.event_id
                          WHERE {DUE} AND w.scope = $2 AND NOT w.alone AND w.item_id <> $3
                            AND e.profile_id = $4 AND e.profile_rev = $5
                          ORDER BY e.seq, c.ordinal
                          LIMIT $6
                            FOR UPDATE OF w SKIP LOCKED"
                    ),
                    &[
                        &now.0,
                        &scope,
                        &order[0].2,
                        &profile_id,
                        &profile_rev,
                        &i64::from(limit - 1),
                    ],
                )
                .map_err(backend)?;
            order.extend(rest.iter().map(|row| (row.get(1), row.get(2), row.get(0))));
        }
        // In the order the chunks were written, so a batch reads like its
        // source.
        order.sort_unstable();
        let ids: Vec<Uuid> = order.iter().map(|(_, _, id)| *id).collect();
        let leased = tx
            .query(
                "UPDATE core_memory.work
                    SET state = 'leased', leased_until = $1, attempts = attempts + 1
                  WHERE kind = 'chunk_read' AND item_id = ANY ($2)
              RETURNING item_id, attempts",
                &[&lease_until.0, &ids],
            )
            .map_err(backend)?;
        let attempts: BTreeMap<Uuid, i32> =
            leased.iter().map(|row| (row.get(0), row.get(1))).collect();
        tx.commit().map_err(backend)?;
        let chunks = ids
            .iter()
            .map(|id| {
                Ok(LeasedChunk {
                    chunk_id: ChunkId(*id),
                    attempts: to_u32(*attempts.get(id).unwrap_or(&1), "attempts")?,
                })
            })
            .collect::<Result<_, StoreError>>()?;
        Ok(Some(ReadBatch {
            scope,
            workspace,
            profile: ProfileRef {
                id: profile_id,
                rev: to_u32(profile_rev, "profile revision")?,
            },
            chunks,
        }))
    }

    fn write_claim_tool(
        &mut self,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<LeasedToolCall>, StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        set_aside_spent(&mut tx, Purpose::ToolRead, max_attempts, now)?;
        // One tool call to a model call. A lease that ran out is claimed
        // like ready work.
        let Some(head) = tx
            .query_opt(
                "SELECT w.item_id, w.scope, w.workspace_id, e.info -> 'tool_call',
                        ARRAY(SELECT c.chunk_id FROM core_memory.chunks AS c
                               WHERE c.event_id = e.event_id ORDER BY c.ordinal)
                   FROM core_memory.work AS w
                   JOIN core_memory.events AS e ON e.event_id = w.item_id
                  WHERE w.kind = 'tool_read'
                    AND (w.state = 'ready' OR (w.state = 'leased' AND w.leased_until < $1))
                    AND w.not_before <= $1
                  ORDER BY w.not_before, w.enqueued_at, w.item_id
                  LIMIT 1
                    FOR UPDATE OF w SKIP LOCKED",
                &[&now.0],
            )
            .map_err(backend)?
        else {
            // Nothing is due. What was set aside above still is.
            tx.commit().map_err(backend)?;
            return Ok(None);
        };
        let event_id = EventId(head.get::<_, Uuid>(0));
        let Json(tool_call): Json<ToolCallDetails> = head
            .try_get::<_, Option<Json<ToolCallDetails>>>(3)
            .ok()
            .flatten()
            .ok_or_else(|| corrupt(format!("tool call {event_id} has no tool details")))?;
        let chunk_ids: Vec<Uuid> = head.get(4);
        let attempts: i32 = tx
            .query_one(
                "UPDATE core_memory.work
                    SET state = 'leased', leased_until = $1, attempts = attempts + 1
                  WHERE kind = 'tool_read' AND item_id = $2
              RETURNING attempts",
                &[&lease_until.0, &event_id.0],
            )
            .map_err(backend)?
            .get(0);
        tx.commit().map_err(backend)?;
        Ok(Some(LeasedToolCall {
            scope: head.get(1),
            workspace: head.get(2),
            event_id,
            chunk_ids: chunk_ids.into_iter().map(ChunkId).collect(),
            tool_call,
            attempts: to_u32(attempts, "attempts")?,
        }))
    }

    fn write_permit(&mut self, request: &PermitRequest<'_>) -> Result<Permit, StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        // One number, for the receipt. From here to the commit nothing in
        // the workspace changes: not the scope's state, not its consent,
        // not its text.
        let (workspace, receipt_seq) = match begin_write(&mut tx, request.scope, Some(1))? {
            None => return Ok(Permit::ScopeNotActive(None)),
            Some(writing) if writing.state != ScopeState::Active => {
                return Ok(Permit::ScopeNotActive(Some(writing.state)));
            }
            Some(writing) => {
                let seq = writing
                    .first_seq
                    .ok_or_else(|| corrupt("an active scope's workspace gave no change number"))?;
                (writing.workspace, seq)
            }
        };
        let consents: bool = tx
            .query_one(
                "SELECT EXISTS (SELECT 1 FROM core_memory.scope_destinations
                                 WHERE scope = $1 AND destination = $2 AND state = 'granted')",
                &[&request.scope, &request.destination],
            )
            .map_err(backend)?
            .get(0);
        if !consents {
            return Ok(Permit::NoConsent);
        }
        // Only chunks of this scope, so consent given by one scope never
        // covers another's text. Erased chunks have no text to read.
        let ids: Vec<Uuid> = request.chunk_ids.iter().map(|c| c.0).collect();
        let rows = tx
            .query(
                "SELECT t.chunk_id, t.text, e.info -> 'actor' ->> 'role'
                   FROM UNNEST($1::uuid[]) WITH ORDINALITY AS u (chunk_id, n)
                   JOIN core_memory.chunk_text AS t ON t.chunk_id = u.chunk_id
                   JOIN core_memory.chunks AS c ON c.chunk_id = t.chunk_id
                   JOIN core_memory.events AS e ON e.event_id = c.event_id
                  WHERE t.scope = $2 AND e.state = 'live'
                  ORDER BY u.n",
                &[&ids, &request.scope],
            )
            .map_err(backend)?;
        if rows.is_empty() {
            return Ok(Permit::NothingToSend);
        }
        let chunks: Vec<PermittedChunk> = rows
            .iter()
            .map(|row| PermittedChunk {
                chunk_id: ChunkId(row.get::<_, Uuid>(0)),
                text: row.get(1),
                role: row.get(2),
            })
            .collect();
        // What will leave, which for a tool call is less than what is
        // stored, and what it may cost. Text that cannot be sent as it is
        // gets no permit and no receipt.
        let Some(Sending {
            bytes,
            reserved_tokens: reserved,
        }) = (request.sending)(&chunks)
        else {
            return Ok(Permit::Unsendable);
        };
        if let Some(budget) = request.budget {
            let spent = tx
                .query_one(
                    "SELECT count(*), coalesce(sum(reserved_tokens), 0)::bigint
                       FROM core_memory.outbound_calls
                      WHERE workspace_id = $1 AND binding_id = $2
                        AND permitted_at >= date_trunc('day', $3::timestamptz, 'UTC')",
                    &[&workspace, &request.reader.binding_id, &request.at.0],
                )
                .map_err(backend)?;
            let calls = to_u64(spent.get(0), "calls today")?;
            let tokens = to_u64(spent.get(1), "tokens today")?;
            if calls >= budget.max_requests_per_day
                || tokens.saturating_add(reserved) > budget.max_tokens_per_day
            {
                return Ok(Permit::OverBudget);
            }
        }
        let sent: Vec<Uuid> = chunks.iter().map(|c| c.chunk_id.0).collect();
        let receipt = tx
            .query_one(
                "INSERT INTO core_memory.outbound_calls (
                     call_id, workspace_id, seq, purpose, scope, destination,
                     binding_id, binding_rev, model, attempt, repairs,
                     chunk_ids, bytes_sent, reserved_tokens, permitted_at
                 ) VALUES (
                     gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                     $11, $12, $13, $14
                 ) RETURNING call_id",
                &[
                    &workspace,
                    &receipt_seq,
                    &request.purpose.as_str(),
                    &request.scope,
                    &request.destination,
                    &request.reader.binding_id,
                    &request.reader.binding_rev.as_bytes().as_slice(),
                    &request.reader.model,
                    &i32::from(request.attempt),
                    &request.repairs.map(|c| c.0),
                    &sent,
                    &to_i64(bytes, "bytes sent")?,
                    &to_i64(reserved, "reserved tokens")?,
                    &request.at.0,
                ],
            )
            .map_err(backend)?;
        let call_id = CallId(receipt.get::<_, Uuid>(0));
        // The receipt is durable before any text leaves this function.
        tx.commit().map_err(commit)?;
        Ok(Permit::Granted { call_id, chunks })
    }

    fn write_finish(&mut self, f: &CallFinish<'_>) -> Result<CallFinished, StoreError> {
        // One number for each read, and one for a tool call's outcome.
        let take = match i32::try_from(f.reads.len() + usize::from(f.tool_outcome.is_some())) {
            Ok(0) => None,
            Ok(n) => Some(n),
            Err(_) => return Err(corrupt("a call read too many chunks")),
        };
        let vocabulary = match (f.reads.is_empty(), f.vocabulary) {
            (true, _) => None,
            (false, Some(vocabulary)) => Some(vocabulary),
            (false, None) => return Err(corrupt("reads were offered without a vocabulary")),
        };
        let offered = (f.reads.len() + usize::from(f.tool_outcome.is_some())) as u32;
        let mut tx = write_transaction(self.client()?)?;
        let writing = begin_write(&mut tx, f.scope, take)?;
        // A provider's own count of tokens is a number from outside. One
        // too large for the column is recorded as the largest it holds,
        // so that it cannot keep a call's end from being recorded.
        let tokens = |count: u64| i64::try_from(count).unwrap_or(i64::MAX);
        let recorded = tx
            .execute(
                "INSERT INTO core_memory.outbound_results
                     (call_id, result, input_tokens, output_tokens, finished_at)
                 VALUES ($1, $2, $3, $4, $5)
                 ON CONFLICT (call_id) DO NOTHING",
                &[
                    &f.call_id.0,
                    &f.result.as_str(),
                    &f.usage.map(|u| tokens(u.input_tokens)),
                    &f.usage.map(|u| tokens(u.output_tokens)),
                    &f.at.0,
                ],
            )
            .map_err(backend)?;
        if recorded == 0 {
            // The end of this call is recorded: an earlier try committed,
            // and its answer was lost on the way back. What that try
            // stored and settled stands, and nothing is stored twice.
            let stored: i64 = tx
                .query_one(
                    "SELECT (SELECT count(*) FROM core_memory.chunk_reads WHERE call_id = $1)
                          + (SELECT count(*) FROM core_memory.tool_outcomes WHERE call_id = $1)",
                    &[&f.call_id.0],
                )
                .map_err(backend)?
                .get(0);
            tx.commit().map_err(backend)?;
            let stored = u32::try_from(stored).unwrap_or(u32::MAX).min(offered);
            return Ok(CallFinished {
                stored,
                dropped: offered - stored,
            });
        }

        let mut finished = CallFinished {
            stored: 0,
            dropped: offered,
        };
        // A scope that stopped being active while the model read keeps its
        // work and gets nothing stored.
        let numbered = writing
            .filter(|w| w.state == ScopeState::Active)
            .and_then(|w| w.first_seq.map(|seq| (w.workspace, seq)));
        if let (Some((workspace, first_seq)), Some(outcome)) = (&numbered, f.tool_outcome) {
            // Only for the event the call's receipt names, and only while
            // it still has its text: erasing a tool call while the model
            // read it leaves nothing behind.
            let stored = tx
                .execute(
                    "INSERT INTO core_memory.tool_outcomes (
                         workspace_id, seq, event_id, scope, call_id, status, action, outcome,
                         compressor_id, compressor_rev, compressed_bytes, binding_id,
                         binding_rev, model, prompt_rev, read_at
                     )
                     SELECT $1, $2, e.event_id, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                            $14, $15
                       FROM core_memory.events AS e
                      WHERE e.event_id = $16 AND e.scope = $3 AND e.state = 'live'
                        AND EXISTS (
                            SELECT 1 FROM core_memory.outbound_calls AS o
                             WHERE o.call_id = $4 AND o.scope = $3 AND o.purpose = 'tool_read'
                               AND o.chunk_ids <@ ARRAY(
                                   SELECT c.chunk_id FROM core_memory.chunks AS c
                                    WHERE c.event_id = e.event_id))",
                    &[
                        workspace,
                        &(first_seq + f.reads.len() as i64),
                        &f.scope,
                        &f.call_id.0,
                        &outcome.status.as_str(),
                        &outcome.action,
                        &outcome.outcome,
                        &outcome.compressor.id,
                        &to_i32(outcome.compressor.rev, "compressor revision")?,
                        &to_i64(outcome.compressed_bytes, "compressed bytes")?,
                        &f.reader.binding_id,
                        &f.reader.binding_rev.as_bytes().as_slice(),
                        &f.reader.model,
                        &to_i32(f.reader.prompt_rev, "prompt revision")?,
                        &f.at.0,
                        &outcome.event_id.0,
                    ],
                )
                .map_err(backend)?;
            if stored == 1 {
                // What was read is no longer owed.
                tx.execute(
                    "DELETE FROM core_memory.work WHERE kind = 'tool_read' AND item_id = $1",
                    &[&outcome.event_id.0],
                )
                .map_err(backend)?;
                finished.stored += 1;
                finished.dropped -= 1;
                // What a judge is shown of a tool call now exists, so the
                // call's bead is owed to the judge. Reading the call again
                // does not have it judged again.
                if let Some(not_before) = f.judge_after {
                    tx.execute(
                        "INSERT INTO core_memory.work
                             (kind, item_id, event_id, workspace_id, scope, state, not_before,
                              enqueued_at)
                         SELECT 'judge', e.event_id, e.event_id, e.workspace_id, e.scope,
                                'ready', $2, $3
                           FROM core_memory.events AS e
                          WHERE e.event_id = $1
                            AND NOT EXISTS (
                                SELECT 1 FROM core_memory.judge_reads AS r
                                 WHERE r.new_beads @> ARRAY[e.event_id])
                         ON CONFLICT (kind, item_id) DO NOTHING",
                        &[&outcome.event_id.0, &not_before.0, &f.at.0],
                    )
                    .map_err(backend)?;
                }
            }
        }
        if let (Some((workspace, first_seq)), Some(vocabulary)) = (numbered, vocabulary) {
            let mut ids = Vec::with_capacity(f.reads.len());
            let mut statuses = Vec::with_capacity(f.reads.len());
            let mut type_keys: Vec<Option<&str>> = Vec::with_capacity(f.reads.len());
            let mut type_revs: Vec<Option<i32>> = Vec::with_capacity(f.reads.len());
            let mut abstained: Vec<Option<&str>> = Vec::with_capacity(f.reads.len());
            let mut rejected = Vec::with_capacity(f.reads.len());
            for read in f.reads {
                ids.push(read.chunk_id.0);
                match &read.decision {
                    ReadDecision::Typed { type_ref } => {
                        statuses.push("typed");
                        type_keys.push(Some(type_ref.key.as_str()));
                        type_revs.push(Some(to_i32(type_ref.rev, "type revision")?));
                        abstained.push(None);
                    }
                    ReadDecision::Abstained { reason } => {
                        statuses.push("abstained");
                        type_keys.push(None);
                        type_revs.push(None);
                        abstained.push(Some(reason.as_str()));
                    }
                }
                rejected.push(to_i32(read.spans_rejected, "rejected spans")?);
            }
            // Only chunks the call's receipt names, and only those that
            // still have their text: erasing a chunk while the model read
            // it leaves nothing behind.
            let stored = tx
                .query(
                    "INSERT INTO core_memory.chunk_reads (
                         workspace_id, seq, chunk_id, scope, call_id, status, type_key,
                         type_rev, abstained, vocabulary_id, vocabulary_hash, binding_id,
                         binding_rev, model, prompt_rev, spans_rejected, read_at
                     )
                     SELECT $1, $2 + u.n - 1, u.chunk_id, $3, $4, u.status, u.type_key,
                            u.type_rev, u.abstained, $5, $6, $7, $8, $9, $10, u.rejected, $11
                       FROM UNNEST($12::uuid[], $13::text[], $14::text[], $15::int4[],
                                   $16::text[], $17::int4[])
                            WITH ORDINALITY
                            AS u (chunk_id, status, type_key, type_rev, abstained, rejected, n)
                       JOIN core_memory.chunk_text AS t
                         ON t.chunk_id = u.chunk_id AND t.scope = $3
                      WHERE u.chunk_id = ANY ((SELECT o.chunk_ids FROM core_memory.outbound_calls AS o
                                                WHERE o.call_id = $4 AND o.scope = $3)::uuid[])
                  RETURNING chunk_id, seq",
                    &[
                        &workspace,
                        &first_seq,
                        &f.scope,
                        &f.call_id.0,
                        &vocabulary.id,
                        &vocabulary.hash.as_bytes().as_slice(),
                        &f.reader.binding_id,
                        &f.reader.binding_rev.as_bytes().as_slice(),
                        &f.reader.model,
                        &to_i32(f.reader.prompt_rev, "prompt revision")?,
                        &f.at.0,
                        &ids,
                        &statuses,
                        &type_keys,
                        &type_revs,
                        &abstained,
                        &rejected,
                    ],
                )
                .map_err(backend)?;
            let seq_of: BTreeMap<Uuid, i64> =
                stored.iter().map(|row| (row.get(0), row.get(1))).collect();
            let (mut read_seqs, mut ordinals, mut chunk_ids) = (Vec::new(), Vec::new(), Vec::new());
            let (mut kinds, mut entity_kinds) = (Vec::new(), Vec::<Option<&str>>::new());
            let (mut starts, mut ends) = (Vec::new(), Vec::new());
            for read in f.reads {
                let Some(seq) = seq_of.get(&read.chunk_id.0) else {
                    continue;
                };
                for (ordinal, span) in read.spans.iter().enumerate() {
                    read_seqs.push(*seq);
                    ordinals.push(i32::try_from(ordinal).map_err(|_| corrupt("too many spans"))?);
                    chunk_ids.push(read.chunk_id.0);
                    kinds.push(span.kind.as_str());
                    entity_kinds.push(span.kind.entity_kind().map(|k| k.as_str()));
                    starts.push(to_i64(span.byte_start, "span offset")?);
                    ends.push(to_i64(span.byte_end, "span offset")?);
                }
            }
            if !read_seqs.is_empty() {
                tx.execute(
                    "INSERT INTO core_memory.chunk_read_spans (
                         workspace_id, read_seq, ordinal, chunk_id, kind, entity_kind,
                         byte_start, byte_end
                     )
                     SELECT $1, s.read_seq, s.ordinal, s.chunk_id, s.kind, s.entity_kind,
                            s.byte_start, s.byte_end
                       FROM UNNEST($2::int8[], $3::int4[], $4::uuid[], $5::text[], $6::text[],
                                   $7::int8[], $8::int8[])
                            AS s (read_seq, ordinal, chunk_id, kind, entity_kind,
                                  byte_start, byte_end)",
                    &[
                        &workspace,
                        &read_seqs,
                        &ordinals,
                        &chunk_ids,
                        &kinds,
                        &entity_kinds,
                        &starts,
                        &ends,
                    ],
                )
                .map_err(backend)?;
            }
            // What was read is no longer owed.
            let done: Vec<Uuid> = seq_of.keys().copied().collect();
            tx.execute(
                "DELETE FROM core_memory.work WHERE kind = 'chunk_read' AND item_id = ANY ($1)",
                &[&done],
            )
            .map_err(backend)?;
            finished.stored += done.len() as u32;
            finished.dropped -= done.len() as u32;
        }
        update_work(&mut tx, f.work, None)?;
        tx.commit().map_err(backend)?;
        Ok(finished)
    }

    fn write_work(
        &mut self,
        work: &[WorkUpdate],
        lease: Option<Timestamp>,
    ) -> Result<(), StoreError> {
        let mut tx = write_transaction(self.client()?)?;
        hold_schema(&mut tx)?;
        update_work(&mut tx, work, lease)?;
        tx.commit().map_err(backend)
    }

    fn read_work_counts(&mut self, purpose: Purpose) -> Result<WorkCounts, StoreError> {
        let mut tx = read_transaction(self.client()?)?;
        let rows = tx
            .query(
                "SELECT state, problem, count(*) FROM core_memory.work
                  WHERE kind = $1 GROUP BY state, problem ORDER BY state, problem",
                &[&purpose.as_str()],
            )
            .map_err(backend)?;
        tx.commit().map_err(backend)?;
        let mut counts = WorkCounts::default();
        let mut problems: BTreeMap<String, u64> = BTreeMap::new();
        for row in &rows {
            let n = to_u64(row.get(2), "work count")?;
            match row.get::<_, &str>(0) {
                "ready" => counts.ready += n,
                "leased" => counts.leased += n,
                "failed" => counts.failed += n,
                other => return Err(corrupt(format!("unknown work state `{other}`"))),
            }
            if let Some(problem) = row.get::<_, Option<String>>(1) {
                *problems.entry(problem).or_default() += n;
            }
        }
        counts.problems = problems.into_iter().collect();
        Ok(counts)
    }
}

/// Sets aside work of one kind that was handed out as often as it may be
/// and never settled: a worker died with it, or could not record how its
/// call ended. Handing it out again would make the same call once every
/// lease, for good.
///
/// Rows another transaction holds are passed over, not waited for, so two
/// workers never wait on each other here.
pub(crate) fn set_aside_spent<C: GenericClient>(
    tx: &mut C,
    kind: Purpose,
    max_attempts: u32,
    now: Timestamp,
) -> Result<(), StoreError> {
    tx.execute(
        "WITH spent AS (
             SELECT w.kind, w.item_id
               FROM core_memory.work AS w
              WHERE w.kind = $1 AND w.attempts >= $2
                AND (w.state = 'ready' OR (w.state = 'leased' AND w.leased_until < $3))
                AND w.not_before <= $3
                FOR UPDATE SKIP LOCKED
         )
         UPDATE core_memory.work AS w
            SET state = 'failed', leased_until = NULL, problem = 'attempts_spent'
           FROM spent
          WHERE w.kind = spent.kind AND w.item_id = spent.item_id",
        &[
            &kind.as_str(),
            &i32::try_from(max_attempts).unwrap_or(i32::MAX),
            &now.0,
        ],
    )
    .map_err(backend)?;
    Ok(())
}

/// Puts work back in the queue, sets it aside as failed, or removes it as
/// done. Each item says which kind of work it is. With `lease`, only work
/// still held under that lease is changed: a worker whose lease ran out,
/// and whose work was handed to another, changes nothing.
pub(crate) fn update_work<C: GenericClient>(
    client: &mut C,
    work: &[WorkUpdate],
    lease: Option<Timestamp>,
) -> Result<(), StoreError> {
    if work.is_empty() {
        return Ok(());
    }
    let item_id = |update: &WorkUpdate| match update.item {
        WorkItem::Chunk(chunk_id) => chunk_id.0,
        WorkItem::ToolCall(event_id) => event_id.0,
        WorkItem::NewBead(bead_id) | WorkItem::HeldChain(bead_id) => bead_id.0,
    };
    let held = lease.map(|lease| lease.0);
    let (done, open): (Vec<&WorkUpdate>, Vec<&WorkUpdate>) = work
        .iter()
        .partition(|update| update.next == WorkNext::Done);
    if !done.is_empty() {
        let kinds: Vec<&str> = done.iter().map(|w| w.item.purpose().as_str()).collect();
        let ids: Vec<Uuid> = done.iter().map(|w| item_id(w)).collect();
        client
            .execute(
                "DELETE FROM core_memory.work AS w
                  USING UNNEST($1::text[], $2::uuid[]) AS u (kind, item_id)
                  WHERE w.kind = u.kind AND w.item_id = u.item_id
                    AND ($3::timestamptz IS NULL
                         OR (w.state = 'leased' AND w.leased_until = $3))",
                &[&kinds, &ids, &held],
            )
            .map_err(backend)?;
    }
    if open.is_empty() {
        return Ok(());
    }
    let kinds: Vec<&str> = open.iter().map(|w| w.item.purpose().as_str()).collect();
    let ids: Vec<Uuid> = open.iter().map(|w| item_id(w)).collect();
    let problems: Vec<&str> = open.iter().map(|w| w.problem).collect();
    let mut states = Vec::with_capacity(open.len());
    let mut give_back = Vec::with_capacity(open.len());
    let mut alone = Vec::with_capacity(open.len());
    let mut not_before: Vec<Option<OffsetDateTime>> = Vec::with_capacity(open.len());
    for update in &open {
        let (state, back, by_itself, when) = match update.next {
            WorkNext::Defer { not_before } => ("ready", true, false, Some(not_before.0)),
            WorkNext::Retry { not_before, alone } => ("ready", false, alone, Some(not_before.0)),
            WorkNext::Failed => ("failed", false, false, None),
            WorkNext::Done => unreachable!("done work was removed above"),
        };
        states.push(state);
        give_back.push(back);
        alone.push(by_itself);
        not_before.push(when);
    }
    client
        .execute(
            "UPDATE core_memory.work AS w
                SET state = u.state,
                    leased_until = NULL,
                    attempts = CASE WHEN u.give_back THEN greatest(w.attempts - 1, 0)
                                    ELSE w.attempts END,
                    alone = w.alone OR u.alone,
                    not_before = coalesce(u.not_before, w.not_before),
                    problem = u.problem
               FROM UNNEST($1::text[], $2::uuid[], $3::text[], $4::bool[], $5::bool[],
                           $6::timestamptz[], $7::text[])
                    AS u (kind, item_id, state, give_back, alone, not_before, problem)
              WHERE w.kind = u.kind AND w.item_id = u.item_id
                AND ($8::timestamptz IS NULL
                     OR (w.state = 'leased' AND w.leased_until = $8))",
            &[
                &kinds,
                &ids,
                &states,
                &give_back,
                &alone,
                &not_before,
                &problems,
                &held,
            ],
        )
        .map_err(backend)?;
    Ok(())
}

//! [`PostgresStore`]: the engine's store on one PostgreSQL connection.

use std::collections::BTreeMap;

use core_memory_contract::{
    Checkpoint, CheckpointReceipt, ChunkFacets, ChunkId, ChunkRecord, ChunkerRef, EventDetails,
    EventId, EventKey, EventRecord, IdKey, ProfileRef, Sha256Digest, TimeBasis, Timestamp,
};
use core_memory_core::StoreError;
use core_memory_core::store::{
    AdmissionCommit, AdmissionWrite, CheckpointAdvance, CheckpointState, ExportedEvent, PinKind,
    PinResult, ScopeInfo, ScopeState, Store, StoredEvent,
};
use postgres::types::{Json, ToSql};
use postgres::{Client, GenericClient, IsolationLevel, Row, SimpleQueryMessage, Transaction};
use time::OffsetDateTime;
use uuid::Uuid;

use crate::connect::{ConnectConfig, Connector};
use crate::error::{READ_ONLY, backend, corrupt, governed};
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

fn digest(bytes: &[u8], what: &str) -> Result<Sha256Digest, StoreError> {
    <[u8; 32]>::try_from(bytes)
        .map(Sha256Digest)
        .map_err(|_| corrupt(format!("{what} is not 32 bytes")))
}

fn to_i64(value: u64, what: &str) -> Result<i64, StoreError> {
    i64::try_from(value).map_err(|_| corrupt(format!("{what} {value} does not fit the store")))
}

fn to_u64(value: i64, what: &str) -> Result<u64, StoreError> {
    u64::try_from(value).map_err(|_| corrupt(format!("{what} {value} is negative")))
}

fn to_u32(value: i32, what: &str) -> Result<u32, StoreError> {
    u32::try_from(value).map_err(|_| corrupt(format!("{what} {value} is negative")))
}

fn to_i32(value: u32, what: &str) -> Result<i32, StoreError> {
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
    fn client(&mut self) -> Result<&mut Client, StoreError> {
        if !self.checked {
            migrate::require_current(self.raw()?)?;
            self.checked = true;
        }
        self.raw()
    }

    /// Passes a result through. A connection to a database that takes no
    /// writes is dropped, so the next call connects again and may reach a
    /// server that does.
    fn settled<T>(&mut self, result: Result<T, StoreError>) -> Result<T, StoreError> {
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
                        count(e.event_id) FILTER (WHERE e.state = 'erased')
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
struct Writing {
    workspace: String,
    state: ScopeState,
    /// The first of the change-counter numbers taken, if any were asked for
    /// and the scope is active.
    first_seq: Option<i64>,
}

/// Starts a transaction's writes for `scope`. The store refuses an engine
/// built for another schema version, makes the transaction the only writer
/// of the scope's workspace until it ends, and reports the scope's state
/// under that lock. With `take`, an active scope's workspace also gives up
/// that many numbers from its change counter. `None` means the scope is
/// not enrolled.
fn begin_write<C: GenericClient>(
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
fn write_transaction(client: &mut Client) -> Result<Transaction<'_>, StoreError> {
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
fn hold_schema(tx: &mut Transaction<'_>) -> Result<(), StoreError> {
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
fn read_transaction(client: &mut Client) -> Result<Transaction<'_>, StoreError> {
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

    fn export_events(
        &mut self,
        workspace: &str,
        after: u64,
        limit: u32,
        scope: Option<&str>,
    ) -> Result<Vec<ExportedEvent>, StoreError> {
        let exported = self.read_export(workspace, after, limit, scope);
        self.settled(exported)
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
        let event_seq = match begin_write(&mut tx, scope, Some(chunk_count + 1))? {
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
        // Every chunk of a message is a bead, in chunk order, numbered
        // straight after the event.
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
    ) -> Result<Vec<ExportedEvent>, StoreError> {
        // One snapshot, so every event comes with exactly its chunks.
        let mut tx = read_transaction(self.client()?)?;
        let rows = tx
            .query(
                "SELECT event_id, seq, workspace_id, scope, source_ns, external_id,
                        revision, engine_assigned_revision, event_hash, content_hash, info,
                        profile_id, profile_rev, chunker_id, chunker_rev, chunker_config_hash,
                        chunk_count, time_at, time_basis, captured_at
                   FROM core_memory.events
                  WHERE workspace_id = $1 AND state = 'live' AND seq > $2
                    AND ($3::text IS NULL OR scope = $3)
                  ORDER BY seq
                  LIMIT $4",
                &[
                    &workspace,
                    &to_i64(after, "export position")?,
                    &scope,
                    &i64::from(limit),
                ],
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
        tx.commit().map_err(backend)?;
        for exported in &mut events {
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
        }
        Ok(events)
    }
}

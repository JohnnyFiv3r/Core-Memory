//! The store itself: migrations, governance, checkpoints, erasing,
//! immutability and privileges.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use std::sync::atomic::{AtomicU32, Ordering};

use common::{World, behind_a_migration, chat, chat_in, checkpoint, crm, note};
use core_memory_contract::{AdmissionOutcome, CheckpointReceipt, EventId, RefusalReason};
use core_memory_core::store::{ScopeState, Store};
use core_memory_core::{Clock, ScopeGrants, StoreError};
use core_memory_store_postgres::testing::TestDb;
use core_memory_store_postgres::{
    ConnectConfig, Enrollment, EraseOutcome, PostgresStore, SCHEMA_VERSION,
};
use postgres::Client;

fn receipt(key: &str, sequence: u64) -> Option<CheckpointReceipt> {
    Some(CheckpointReceipt {
        key: key.into(),
        sequence,
    })
}

/// The database's message for a statement that must fail.
fn refused(client: &mut Client, sql: &str) -> String {
    match client.batch_execute(sql) {
        Ok(()) => panic!("`{sql}` was allowed"),
        Err(e) => e
            .as_db_error()
            .map_or(e.to_string(), |d| d.message().to_string()),
    }
}

// ---- migrations ----

#[test]
fn an_empty_database_is_refused_until_it_is_migrated() {
    let db = TestDb::create_empty();
    let mut store = db.store();
    assert_eq!(store.schema_version().unwrap(), None);
    let err = store.check().unwrap_err();
    assert!(matches!(err, StoreError::SchemaMismatch(_)), "{err:?}");
    assert!(err.to_string().contains("core-memory migrate"), "{err}");
    assert!(
        store.scope("acme").is_err(),
        "no call works before migrating"
    );

    let report = store.migrate().unwrap();
    assert_eq!((report.from, report.to), (0, SCHEMA_VERSION));
    let again = store.migrate().unwrap();
    assert_eq!((again.from, again.to), (SCHEMA_VERSION, SCHEMA_VERSION));
    store.check().unwrap();
    assert_eq!(store.schema_version().unwrap(), Some(SCHEMA_VERSION));
}

#[test]
fn a_changed_or_newer_schema_history_is_refused() {
    let db = TestDb::create();
    db.connect()
        .batch_execute("UPDATE core_memory.schema_migrations SET checksum = 'tampered'")
        .unwrap();
    let err = db.store().check().unwrap_err();
    assert!(matches!(err, StoreError::Corrupt(_)), "{err:?}");

    let newer = TestDb::create();
    newer
        .connect()
        .batch_execute(
            "INSERT INTO core_memory.schema_migrations (version, name, checksum)
             VALUES (9999, 'from the future', 'x')",
        )
        .unwrap();
    let err = newer.store().check().unwrap_err().to_string();
    assert!(err.contains("newer than this build"), "{err}");
}

// ---- governance ----

#[test]
fn workspaces_and_scopes_are_enrolled_once() {
    let db = TestDb::create();
    let mut store = db.store();
    assert!(store.create_workspace("w1").unwrap());
    assert!(!store.create_workspace("w1").unwrap(), "already there");
    assert!(store.create_workspace("w2").unwrap());

    let err = store.enroll_scope("acme", "nowhere").unwrap_err();
    assert!(matches!(err, StoreError::Rejected(_)), "{err:?}");
    assert_eq!(
        store.enroll_scope("acme", "w1").unwrap(),
        Enrollment::Enrolled
    );
    assert_eq!(
        store.enroll_scope("acme", "w1").unwrap(),
        Enrollment::Unchanged
    );
    let err = store.enroll_scope("acme", "w2").unwrap_err().to_string();
    assert!(err.contains("belongs to another workspace"), "{err}");

    let info = store.scope("acme").unwrap().unwrap();
    assert_eq!(
        (info.workspace.as_str(), info.state),
        ("w1", ScopeState::Active)
    );
    assert_eq!(store.scope("beta").unwrap(), None);
    // Each workspace has its own ID key.
    store.enroll_scope("beta", "w2").unwrap();
    assert_ne!(info.id_key, store.scope("beta").unwrap().unwrap().id_key);

    let err = store
        .set_scope_state("gamma", ScopeState::Paused)
        .unwrap_err();
    assert!(err.to_string().contains("not enrolled"), "{err}");
    assert_eq!(
        store.set_scope_state("acme", ScopeState::Paused).unwrap(),
        ScopeState::Active
    );
    assert_eq!(
        store.set_scope_state("acme", ScopeState::Revoked).unwrap(),
        ScopeState::Paused
    );
    assert_eq!(
        store.enroll_scope("acme", "w1").unwrap(),
        Enrollment::Reactivated
    );
    let scopes = store.scopes().unwrap();
    let names: Vec<_> = scopes.iter().map(|s| (s.scope.as_str(), s.state)).collect();
    assert_eq!(
        names,
        [("acme", ScopeState::Active), ("beta", ScopeState::Active)]
    );
}

// ---- checkpoints ----

#[test]
fn a_checkpoint_advances_with_its_event() {
    let mut w = World::new();
    let mut first = chat("1", "one");
    first.checkpoint = Some(checkpoint("session-a", 0, 1));
    match w.admit(&first) {
        AdmissionOutcome::Accepted { checkpoint, .. } => {
            assert_eq!(checkpoint, receipt("session-a", 1));
        }
        other => panic!("{other:?}"),
    }
    let stored = w.store.checkpoint("acme", "session-a").unwrap().unwrap();
    assert_eq!(stored.sequence, 1);
    assert_eq!(stored.hash, Some(first.checkpoint.as_ref().unwrap().hash));
    assert_eq!(w.store.checkpoint("acme", "session-b").unwrap(), None);

    // A reader that crashed before its receipt sends the same thing again.
    match w.admit(&first) {
        AdmissionOutcome::Replay { checkpoint, .. } => {
            assert_eq!(checkpoint, receipt("session-a", 1));
        }
        other => panic!("{other:?}"),
    }

    // Sequences need not be contiguous, only increasing.
    let mut second = chat("2", "two");
    second.checkpoint = Some(checkpoint("session-a", 1, 7));
    assert!(matches!(
        w.admit(&second),
        AdmissionOutcome::Accepted { checkpoint, .. } if checkpoint == receipt("session-a", 7)
    ));

    // A replayed event still moves the reader past it.
    let mut replay = chat("1", "one");
    replay.checkpoint = Some(checkpoint("session-a", 7, 8));
    assert!(matches!(
        w.admit(&replay),
        AdmissionOutcome::Replay { checkpoint, .. } if checkpoint == receipt("session-a", 8)
    ));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 2);
}

#[test]
fn a_checkpoint_that_does_not_follow_writes_nothing() {
    let mut w = World::new();
    let mut first = chat("1", "one");
    first.checkpoint = Some(checkpoint("session-a", 0, 1));
    w.accept(&first);

    // A second reader that thinks it is at the start.
    let mut stale = chat("2", "two");
    stale.checkpoint = Some(checkpoint("session-a", 0, 1));
    stale.checkpoint.as_mut().unwrap().hash = core_memory_contract::Sha256Digest::of(b"other");
    match w.admit(&stale) {
        AdmissionOutcome::CheckpointConflict {
            key,
            stored_sequence,
            stored_hash,
        } => {
            assert_eq!((key.as_str(), stored_sequence), ("session-a", 1));
            assert_eq!(stored_hash, Some(first.checkpoint.as_ref().unwrap().hash));
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.events"),
        1,
        "the event that carried the stale checkpoint was not stored"
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_text"), 1);

    // Nothing but accepted and replay moves a checkpoint.
    let mut conflict = chat("1", "different text");
    conflict.checkpoint = Some(checkpoint("session-a", 1, 2));
    assert!(matches!(
        w.admit(&conflict),
        AdmissionOutcome::IdentityConflict { .. }
    ));
    let mut malformed = chat("3", "   ");
    malformed.checkpoint = Some(checkpoint("session-a", 1, 2));
    assert!(matches!(
        w.admit(&malformed),
        AdmissionOutcome::Refused { .. }
    ));
    let mut backwards = chat("4", "four");
    backwards.checkpoint = Some(checkpoint("session-a", 5, 5));
    assert!(matches!(
        w.admit(&backwards),
        AdmissionOutcome::Refused { .. }
    ));
    assert_eq!(
        w.store
            .checkpoint("acme", "session-a")
            .unwrap()
            .unwrap()
            .sequence,
        1
    );

    // Keys and scopes are independent.
    w.store.enroll_scope("beta", "w1").unwrap();
    let mut other_key = chat("5", "five");
    other_key.checkpoint = Some(checkpoint("session-b", 0, 1));
    w.accept(&other_key);
    let mut other_scope = chat_in("beta", "1", "one");
    other_scope.checkpoint = Some(checkpoint("session-a", 0, 1));
    w.accept(&other_scope);
}

#[test]
fn readers_racing_on_one_checkpoint_are_serialized() {
    let w = World::new();
    let handles: Vec<_> = (0..6)
        .map(|i| {
            let mut store = w.db.store();
            let clock = w.clock.clone();
            std::thread::spawn(move || {
                let engine = common::engine_with(common::CONFIG, &clock);
                let mut event = chat(&format!("e{i}"), "racing");
                event.checkpoint = Some(checkpoint("session-a", 0, 1));
                event.checkpoint.as_mut().unwrap().hash =
                    core_memory_contract::Sha256Digest::of(format!("reader {i}").as_bytes());
                engine.admit(&mut store, &ScopeGrants::All, &event).unwrap()
            })
        })
        .collect();
    let outcomes: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    let won = outcomes
        .iter()
        .filter(|o| matches!(o, AdmissionOutcome::Accepted { .. }))
        .count();
    let lost = outcomes
        .iter()
        .filter(|o| matches!(o, AdmissionOutcome::CheckpointConflict { .. }))
        .count();
    assert_eq!((won, lost), (1, 5), "{outcomes:?}");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 1);
}

// ---- erasing ----

#[test]
fn erasing_removes_the_whole_message_and_leaves_its_ids() {
    let mut w = World::new();
    let text =
        "The vault code is quartz-heron.\n\nNobody else knows the vault code.\n\nDestroy this.";
    let (erased_id, erased_chunks) = w.accept(&note("secret", text));
    assert!(erased_chunks.len() >= 2);
    let (kept_id, _) = w.accept(&chat("kept", "Lunch is at noon."));

    assert_eq!(
        w.store
            .erase_event(&erased_id, "user_request", "owner")
            .unwrap(),
        EraseOutcome::Erased
    );

    // No text, facets, key fields, hashes or metadata are left.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunk_text"),
        1,
        "only the other event's text remains"
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.events
              WHERE state = 'erased' AND source_ns IS NULL AND external_id IS NULL
                AND revision IS NULL AND event_hash IS NULL AND content_hash IS NULL
                AND info IS NULL AND erased_at IS NOT NULL
                AND time_basis = 'captured' AND time_at = captured_at"
        ),
        1
    );
    // The IDs, ranges and beads stay as the tombstone.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunks") as usize,
        erased_chunks.len() + 1
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.beads WHERE state = 'erased'") as usize,
        erased_chunks.len()
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.beads WHERE state = 'live'"),
        1
    );
    // The journal names what was erased, why and by whom, and nothing else.
    let journal =
        w.db.connect()
            .query_one(
                "SELECT event_id, scope, reason, actor, chunks_erased FROM core_memory.erasures",
                &[],
            )
            .unwrap();
    assert_eq!(journal.get::<_, uuid::Uuid>(0), erased_id.0);
    assert_eq!(journal.get::<_, &str>(1), "acme");
    assert_eq!(
        (journal.get::<_, &str>(2), journal.get::<_, &str>(3)),
        ("user_request", "owner")
    );
    assert_eq!(journal.get::<_, i32>(4) as usize, erased_chunks.len());

    // Only the other event is exported, and the erased one never comes back.
    let exported = w.export();
    assert_eq!(exported.len(), 1);
    assert_eq!(exported[0].event.event_id, kept_id);
    match w.admit(&note("secret", text)) {
        AdmissionOutcome::Refused { reason, .. } => {
            assert_eq!(reason, RefusalReason::Tombstoned);
        }
        other => panic!("{other:?}"),
    }
    assert!(matches!(
        w.admit(&note("secret", "Different words under the same key.")),
        AdmissionOutcome::Refused {
            reason: RefusalReason::Tombstoned,
            ..
        }
    ));

    assert_eq!(
        w.store
            .erase_event(&erased_id, "user_request", "owner")
            .unwrap(),
        EraseOutcome::AlreadyErased
    );
    assert_eq!(
        w.store
            .erase_event(&EventId(uuid::Uuid::nil()), "user_request", "owner")
            .unwrap(),
        EraseOutcome::Unknown
    );
    let err = w.store.erase_event(&kept_id, " ", "owner").unwrap_err();
    assert!(matches!(err, StoreError::Rejected(_)), "{err:?}");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.erasures"), 1);
}

#[test]
fn an_erased_editable_record_stays_erased() {
    let mut w = World::new();
    let (event_id, _) = w.accept(&crm("opp-1", "open", 100));
    w.store
        .erase_event(&event_id, "user_request", "owner")
        .unwrap();
    // An edit would normally become a new revision. An erased key takes none.
    assert!(matches!(
        w.admit(&crm("opp-1", "closed_won", 100)),
        AdmissionOutcome::Refused {
            reason: RefusalReason::Tombstoned,
            ..
        }
    ));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 1);
}

#[test]
fn erasing_a_scope_erases_every_live_event_in_it() {
    let mut w = World::new();
    w.store.enroll_scope("beta", "w1").unwrap();
    for i in 0..4 {
        w.accept(&chat(&format!("a{i}"), "acme text"));
    }
    let (already, _) = w.accept(&chat("a-old", "erased earlier"));
    w.store
        .erase_event(&already, "user_request", "owner")
        .unwrap();
    w.accept(&chat_in("beta", "b1", "beta text"));

    assert_eq!(
        w.store
            .erase_scope("acme", "revoked", "owner", None)
            .unwrap(),
        4
    );
    assert_eq!(
        w.store
            .erase_scope("acme", "revoked", "owner", None)
            .unwrap(),
        0
    );
    // A mistyped scope is an error, not an erasure of nothing.
    let err = w
        .store
        .erase_scope("acmee", "revoked", "owner", None)
        .unwrap_err();
    assert!(matches!(err, StoreError::Rejected(_)), "{err:?}");
    let exported = w.export();
    assert_eq!(exported.len(), 1);
    assert_eq!(exported[0].event.key.scope, "beta");
    let acme = &w.store.scopes().unwrap()[0];
    assert_eq!((acme.live_events, acme.erased_events), (0, 5));
}

// ---- immutability ----

#[test]
fn stored_rows_cannot_be_changed_or_deleted() {
    let mut w = World::new();
    let (event_id, _) = w.accept(&chat("1", "fixed"));
    w.store
        .erase_event(&event_id, "user_request", "owner")
        .unwrap();
    w.accept(&chat("2", "also fixed"));
    w.engine.pin_profiles(&mut w.store).unwrap();
    // Even the database owner is refused.
    let mut owner = w.db.connect();
    for sql in [
        "UPDATE core_memory.events SET scope = 'acme'",
        "DELETE FROM core_memory.events",
        "UPDATE core_memory.chunks SET ordinal = 9",
        "DELETE FROM core_memory.chunks",
        "UPDATE core_memory.chunk_text SET text = 'rewritten'",
        "DELETE FROM core_memory.chunk_text",
        "UPDATE core_memory.beads SET state = 'live'",
        "DELETE FROM core_memory.beads",
        "UPDATE core_memory.workspaces SET id_key = decode(repeat('00', 32), 'hex')",
        "DELETE FROM core_memory.erasures",
        "UPDATE core_memory.erasures SET reason = 'other'",
        "UPDATE core_memory.pins SET rev = 2",
        "TRUNCATE core_memory.chunk_text",
        "TRUNCATE core_memory.events CASCADE",
        // A counter that went back would let a reader skip rows.
        "UPDATE core_memory.workspace_seq SET last_seq = last_seq - 1",
        "UPDATE core_memory.workspace_seq SET last_seq = last_seq",
        "DELETE FROM core_memory.workspace_seq",
        "TRUNCATE core_memory.workspace_seq",
        // A scope keeps its name and workspace, and the tables that hold
        // consent, reader positions and pinned revisions are not emptied.
        "UPDATE core_memory.scopes SET workspace_id = 'w2'",
        "UPDATE core_memory.scopes SET scope = 'renamed'",
        "DELETE FROM core_memory.scopes",
        "TRUNCATE core_memory.scopes CASCADE",
        "TRUNCATE core_memory.pins",
        "TRUNCATE core_memory.checkpoints",
        // Turning the erase flag on by hand allows an erase and nothing
        // else: not deleting a live event's text, renumbering an event,
        // or bringing an erased bead back.
        "SET LOCAL core_memory.erasing = 'on'; DELETE FROM core_memory.chunk_text",
        "SET LOCAL core_memory.erasing = 'on'; UPDATE core_memory.events SET seq = seq + 100",
        "SET LOCAL core_memory.erasing = 'on';
         UPDATE core_memory.events SET state = 'erased', source_ns = NULL, external_id = NULL,
                revision = NULL, engine_assigned_revision = NULL, event_hash = NULL,
                content_hash = NULL, info = NULL, time_at = captured_at,
                time_basis = 'captured', erased_at = now(), chunk_count = 99
          WHERE state = 'live'",
        "SET LOCAL core_memory.erasing = 'on'; UPDATE core_memory.beads SET state = 'live'",
        "SET LOCAL core_memory.erasing = 'on';
         UPDATE core_memory.beads SET state = 'erased', time_basis = 'captured',
                time_at = created_at, scope = 'elsewhere' WHERE state = 'live'",
    ] {
        let message = refused(&mut owner, sql);
        assert!(
            message.contains("cannot be")
                || message.contains("violates")
                || message.contains("only moves forward"),
            "{sql}: {message}"
        );
    }
    // Setting the flag by hand does not outlive a transaction, and erasing
    // through the function still leaves it off.
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_text"), 1);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM (SELECT 1 WHERE current_setting('core_memory.erasing', true)
                 IS DISTINCT FROM 'on') AS flag_is_off"
        ),
        1
    );
}

// ---- privileges and row security ----

static ROLE: AtomicU32 = AtomicU32::new(0);

/// A fresh cluster role for one test, dropped with its grants at the end.
struct Role {
    name: String,
    admin: Client,
}

impl Role {
    fn new(db: &TestDb, kind: &str) -> Self {
        let name = format!(
            "cm_t_{kind}_{}_{}",
            std::process::id(),
            ROLE.fetch_add(1, Ordering::SeqCst)
        );
        let mut admin = db.connect();
        admin
            .batch_execute(&format!("CREATE ROLE {name} LOGIN PASSWORD 'synthetic'"))
            .unwrap();
        Self { name, admin }
    }

    /// Runs `sql` as the database owner, with `{role}` standing for this
    /// role and `{db}` for the database.
    fn grant(&mut self, sql: &str) {
        let db: String = self
            .admin
            .query_one("SELECT quote_ident(current_database())", &[])
            .unwrap()
            .get(0);
        self.admin
            .batch_execute(&sql.replace("{role}", &self.name).replace("{db}", &db))
            .unwrap();
    }
}

impl Drop for Role {
    fn drop(&mut self) {
        let _ = self
            .admin
            .batch_execute(&format!("DROP OWNED BY {0}; DROP ROLE {0}", self.name));
    }
}

/// The test database's URL with another user.
fn url_as(db: &TestDb, role: &str) -> String {
    let (scheme, rest) = db.url.split_once("://").unwrap();
    let (_, host) = rest.split_once('@').unwrap();
    format!("{scheme}://{role}:synthetic@{host}")
}

#[test]
fn the_engine_role_can_admit_and_nothing_more() {
    let w = World::new();
    let mut role = Role::new(&w.db, "engine");
    role.grant("GRANT core_memory_engine TO {role}");

    // The whole admission path works with only the engine role.
    let mut store = PostgresStore::new(&ConnectConfig::new(url_as(&w.db, &role.name))).unwrap();
    store.check().unwrap();
    w.engine.pin_profiles(&mut store).unwrap();
    let mut event = chat("1", "written with least privilege");
    event.checkpoint = Some(checkpoint("session-a", 0, 1));
    let outcome = w
        .engine
        .admit(&mut store, &ScopeGrants::All, &event)
        .unwrap();
    let AdmissionOutcome::Accepted { event_id, .. } = outcome else {
        panic!("{outcome:?}");
    };
    assert_eq!(store.export("w1", 0, 10, None).unwrap().len(), 1);

    // It cannot govern, erase or alter what it stored.
    let err = store.erase_event(&event_id, "x", "engine").unwrap_err();
    assert!(err.to_string().contains("permission denied"), "{err}");
    assert!(store.set_scope_state("acme", ScopeState::Revoked).is_err());
    assert!(store.create_workspace("w9").is_err());
    let mut raw = Client::connect(&url_as(&w.db, &role.name), postgres::NoTls).unwrap();
    for sql in [
        "UPDATE core_memory.chunk_text SET text = 'x'",
        "DELETE FROM core_memory.chunk_text",
        "DELETE FROM core_memory.events",
        "UPDATE core_memory.scopes SET state = 'active'",
        "UPDATE core_memory.beads SET state = 'erased'",
        "INSERT INTO core_memory.erasures
             (workspace_id, seq, event_id, scope, reason, actor, chunks_erased, erased_at)
         SELECT workspace_id, 99, event_id, scope, 'x', 'x', 0, now() FROM core_memory.events",
        // The change counter is reached only through begin_write.
        "UPDATE core_memory.workspace_seq SET last_seq = last_seq + 100",
        "SELECT last_seq FROM core_memory.workspace_seq",
        "SELECT core_memory.take_seq('w1', 1000)",
        "SELECT core_memory.lock_workspace('w1')",
        "SELECT core_memory.hold_schema()",
        "SELECT core_memory.erase_live_events(ARRAY[]::uuid[], 'w1', 'x', 'x')",
        "SELECT * FROM core_memory.erase_events(ARRAY[]::uuid[], 'x', 'x')",
        "SELECT core_memory.erase_key('acme', 'slack/T01', '1', 'x', 'x')",
        "SELECT core_memory.erase_scope('acme', 'x', 'x')",
        "SELECT core_memory.enroll_scope('other', 'w1')",
    ] {
        let message = refused(&mut raw, sql);
        assert!(message.contains("permission denied"), "{sql}: {message}");
    }
}

#[test]
fn other_roles_see_nothing_without_a_policy() {
    let mut w = World::new();
    w.accept(&chat("1", "private text"));

    // A role with no grant cannot even name the schema's tables.
    let nobody = Role::new(&w.db, "nobody");
    let mut raw = Client::connect(&url_as(&w.db, &nobody.name), postgres::NoTls).unwrap();
    let message = refused(&mut raw, "SELECT count(*) FROM core_memory.chunk_text");
    assert!(message.contains("permission denied"), "{message}");

    // A table grant alone shows no rows: row security has no policy for it.
    let mut reader = Role::new(&w.db, "reader");
    reader.grant(
        "GRANT USAGE ON SCHEMA core_memory TO {role};
         GRANT SELECT ON core_memory.chunk_text, core_memory.events, core_memory.beads,
             core_memory.workspaces TO {role}",
    );
    let mut raw = Client::connect(&url_as(&w.db, &reader.name), postgres::NoTls).unwrap();
    for table in ["chunk_text", "events", "beads", "workspaces"] {
        let seen: i64 = raw
            .query_one(&format!("SELECT count(*) FROM core_memory.{table}"), &[])
            .unwrap()
            .get(0);
        assert_eq!(seen, 0, "{table}");
    }
    // A host opens what it wants its role to read with a policy of its own.
    reader.grant("CREATE POLICY host_reads ON core_memory.beads FOR SELECT TO {role} USING (true)");
    let seen: i64 = raw
        .query_one("SELECT count(*) FROM core_memory.beads", &[])
        .unwrap()
        .get(0);
    assert_eq!(seen, 1);

    // The governor role governs and reads no table.
    let mut governor = Role::new(&w.db, "governor");
    governor.grant("GRANT core_memory_governor TO {role}");
    let mut raw = Client::connect(&url_as(&w.db, &governor.name), postgres::NoTls).unwrap();
    raw.batch_execute("SELECT core_memory.set_scope_state('acme', 'paused')")
        .unwrap();
    let message = refused(&mut raw, "SELECT count(*) FROM core_memory.chunk_text");
    assert!(message.contains("permission denied"), "{message}");
    assert_eq!(
        w.store.scope("acme").unwrap().unwrap().state,
        ScopeState::Paused
    );
}

// ---- the change counter ----

/// `(table, seq)` for every numbered row of workspace `w`, in seq order.
fn numbered(world: &World, workspace: &str) -> Vec<(String, i64)> {
    world
        .db
        .connect()
        .query(
            "SELECT 'event', seq FROM core_memory.events WHERE workspace_id = $1
             UNION ALL SELECT 'bead', seq FROM core_memory.beads WHERE workspace_id = $1
             UNION ALL SELECT 'erasure', seq FROM core_memory.erasures WHERE workspace_id = $1
             ORDER BY 2",
            &[&workspace],
        )
        .unwrap()
        .iter()
        .map(|row| (row.get(0), row.get(1)))
        .collect()
}

#[test]
fn every_row_takes_the_next_number_of_its_workspace() {
    let mut w = World::new();
    w.store.create_workspace("w2").unwrap();
    w.store.enroll_scope("other", "w2").unwrap();

    // One chunk, then a note the chunker cuts into several.
    let (first, _) = w.accept(&chat("1", "one chunk"));
    let (_, chunks) = w.accept(&note(
        "n1",
        "First paragraph of the note.\n\nSecond paragraph of the note.\n\nThird one.",
    ));
    assert!(chunks.len() > 1);
    // Replays, conflicts and refusals take nothing.
    w.admit(&chat("1", "one chunk"));
    w.admit(&chat("1", "changed"));
    let mut stale = chat("2", "stale checkpoint");
    stale.checkpoint = Some(checkpoint("session-a", 7, 8));
    assert!(matches!(
        w.admit(&stale),
        AdmissionOutcome::CheckpointConflict { .. }
    ));
    w.store
        .erase_event(&first, "user_request", "owner")
        .unwrap();
    w.store
        .erase_event(&first, "user_request", "owner")
        .unwrap();
    w.accept(&chat("3", "after the erasure"));

    let mut expected = vec![("event".to_string(), 1), ("bead".to_string(), 2)];
    expected.push(("event".to_string(), 3));
    for i in 0..chunks.len() as i64 {
        expected.push(("bead".to_string(), 4 + i));
    }
    let next = 4 + chunks.len() as i64;
    expected.push(("erasure".to_string(), next));
    expected.push(("event".to_string(), next + 1));
    expected.push(("bead".to_string(), next + 2));
    assert_eq!(numbered(&w, "w1"), expected);

    // Another workspace counts on its own.
    w.accept(&chat_in("other", "1", "elsewhere"));
    assert_eq!(
        numbered(&w, "w2"),
        [("event".to_string(), 1), ("bead".to_string(), 2)]
    );
    // An event's beads follow it in chunk order.
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.beads AS b
               JOIN core_memory.chunks AS c USING (chunk_id)
               JOIN core_memory.events AS e ON e.event_id = b.event_id
              WHERE b.seq <> e.seq + 1 + c.ordinal"
        ),
        0
    );
}

#[test]
fn rows_of_one_workspace_commit_in_number_order() {
    let mut w = World::new();
    w.store.create_workspace("w2").unwrap();
    w.store.enroll_scope("other", "w2").unwrap();
    w.accept(&chat("0", "before"));

    // A writer that has taken a number and not yet committed.
    let mut slow = w.db.connect();
    let mut open = slow.transaction().unwrap();
    let held: i64 = open
        .query_one("SELECT core_memory.take_seq('w1', 1)", &[])
        .unwrap()
        .get(0);
    assert_eq!(held, 3);

    let db = &w.db;
    let engine = &w.engine;
    std::thread::scope(|s| {
        let same = s.spawn(move || {
            let mut store = db.store();
            let outcome = engine
                .admit(&mut store, &ScopeGrants::All, &chat("1", "waits its turn"))
                .unwrap();
            assert!(
                matches!(outcome, AdmissionOutcome::Accepted { .. }),
                "{outcome:?}"
            );
        });
        // Another workspace is not held up.
        let mut store = db.store();
        let outcome = engine
            .admit(
                &mut store,
                &ScopeGrants::All,
                &chat_in("other", "1", "free"),
            )
            .unwrap();
        assert!(
            matches!(outcome, AdmissionOutcome::Accepted { .. }),
            "{outcome:?}"
        );

        std::thread::sleep(std::time::Duration::from_millis(300));
        assert!(
            !same.is_finished(),
            "the same workspace waits for the open writer"
        );
        // Until the open writer ends, a reader sees nothing above its number.
        let visible: i64 = db
            .connect()
            .query_one(
                "SELECT coalesce(max(seq), 0) FROM core_memory.events WHERE workspace_id = 'w1'",
                &[],
            )
            .unwrap()
            .get(0);
        assert!(visible < held, "{visible}");
        // The open writer gives up: its number goes back to the counter.
        open.rollback().unwrap();
        same.join().unwrap();
    });
    let last = w.export().pop().unwrap();
    assert_eq!(
        (last.event.key.external_id.as_str(), last.event.seq),
        ("1", 3)
    );
}

// ---- the schema fence ----

#[test]
fn a_migration_waits_for_writes_and_then_shuts_old_engines_out() {
    let mut w = World::new();
    // A connection that has already checked the schema version.
    w.accept(&chat("0", "before the migration"));

    let mut migrator = w.db.connect();
    let mut migration = migrator.transaction().unwrap();
    migration
        .batch_execute("LOCK TABLE core_memory.schema_migrations IN ACCESS EXCLUSIVE MODE")
        .unwrap();

    let engine = &w.engine;
    let store = &mut w.store;
    let outcome = std::thread::scope(|s| {
        let writer =
            s.spawn(move || engine.admit(store, &ScopeGrants::All, &chat("1", "too late")));
        std::thread::sleep(std::time::Duration::from_millis(300));
        assert!(!writer.is_finished(), "a write waits for the migration");
        migration
            .batch_execute(
                "INSERT INTO core_memory.schema_migrations (version, name, checksum)
                 VALUES (9999, 'from the future', 'x')",
            )
            .unwrap();
        migration.commit().unwrap();
        writer.join().unwrap()
    });
    let err = outcome.unwrap_err();
    assert!(
        matches!(
            err,
            core_memory_core::EngineError::Store(StoreError::SchemaMismatch(_))
        ),
        "{err:?}"
    );
    assert!(err.to_string().contains("schema version 9999"), "{err}");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 1);
}

#[test]
fn every_call_waits_for_a_migration_holding_nothing_else() {
    let mut w = World::new();
    let mut first = chat("1", "stored before the migration");
    first.checkpoint = Some(checkpoint("session-a", 0, 1));
    let (event_id, chunk_ids) = w.accept(&first);
    let (to_erase, _) = w.accept(&chat("2", "erased behind a migration"));
    let (listed, _) = w.accept(&chat("3", "erased from a list behind a migration"));
    let db = &w.db;
    let engine = &w.engine;
    // A connection that has already checked the schema version, so that
    // what waits is the call itself.
    let ready = || {
        let mut store = db.store();
        store.check().unwrap();
        store
    };

    // What the engine reads.
    let mut store = ready();
    let scope = behind_a_migration(db, "scope", || store.scope("acme"));
    assert_eq!(scope.unwrap().unwrap().state, ScopeState::Active);
    let stored = behind_a_migration(db, "event", || store.event(&event_id));
    assert!(matches!(
        stored.unwrap(),
        Some(core_memory_core::store::StoredEvent::Live { chunk_ids: stored, .. })
            if stored == chunk_ids
    ));
    let position = behind_a_migration(db, "checkpoint", || store.checkpoint("acme", "session-a"));
    assert_eq!(position.unwrap().unwrap().sequence, 1);
    let exported = behind_a_migration(db, "export", || store.export("w1", 0, 10, None));
    assert_eq!(exported.unwrap().len(), 3);
    let asked = core_memory_core::RecallQuery {
        query: "stored before".into(),
        scopes: ScopeGrants::All,
        limit: 10,
        follow: 2,
        include_ended: false,
    };
    let recalled = behind_a_migration(db, "recall", || store.recall(&asked));
    assert_eq!(recalled.unwrap().beads.len(), 1);
    let workspaces = behind_a_migration(db, "workspaces", || store.workspaces());
    assert_eq!(workspaces.unwrap(), ["w1"]);
    let scopes = behind_a_migration(db, "scopes", || store.scopes());
    assert_eq!(scopes.unwrap()[0].live_events, 3);

    // What the engine writes.
    behind_a_migration(db, "pin", || engine.pin_profiles(&mut store)).unwrap();
    let admitted = behind_a_migration(db, "admit", || {
        engine.admit(&mut store, &ScopeGrants::All, &chat("4", "admitted after"))
    });
    assert!(
        matches!(admitted, Ok(AdmissionOutcome::Accepted { .. })),
        "{admitted:?}"
    );
    let advanced = behind_a_migration(db, "advance_checkpoint", || {
        store.advance_checkpoint("acme", &checkpoint("session-a", 1, 2), w.clock.now())
    });
    assert!(
        matches!(
            advanced,
            Ok(core_memory_core::store::CheckpointAdvance::Advanced(_))
        ),
        "{advanced:?}"
    );

    // What a host or an operator calls.
    let mut governor = ready();
    assert!(
        behind_a_migration(db, "create_workspace", || governor.create_workspace("w2")).unwrap()
    );
    assert_eq!(
        behind_a_migration(db, "enroll_scope", || governor.enroll_scope("beta", "w2")).unwrap(),
        Enrollment::Enrolled
    );
    assert_eq!(
        behind_a_migration(db, "set_scope_state", || {
            governor.set_scope_state("beta", ScopeState::Paused)
        })
        .unwrap(),
        ScopeState::Active
    );
    assert_eq!(
        behind_a_migration(db, "erase_event", || {
            governor.erase_event(&to_erase, "user_request", "owner")
        })
        .unwrap(),
        EraseOutcome::Erased
    );
    assert_eq!(
        behind_a_migration(db, "erase_events", || {
            governor.erase_events(&[listed], "user_request", "owner")
        })
        .unwrap(),
        [(listed, EraseOutcome::Erased)]
    );
    assert_eq!(
        behind_a_migration(db, "erase_key", || {
            governor.erase_key("acme", "slack/T01", "1", "user_request", "owner")
        })
        .unwrap(),
        1
    );
    assert_eq!(
        behind_a_migration(db, "erase_scope", || {
            governor.erase_scope("acme", "revoked", "owner", None)
        })
        .unwrap(),
        1
    );
}

#[test]
fn an_export_that_starts_during_a_migration_reads_what_the_migration_left() {
    let mut w = World::new();
    for i in 0..3 {
        w.accept(&chat(&format!("{i}"), "stored before the migration"));
    }
    let mut store = w.db.store();
    store.check().unwrap();

    let mut migrator = w.db.connect();
    let mut migration = migrator.transaction().unwrap();
    // A column with a volatile default rewrites the table. To a snapshot
    // taken before the rewrite, the rewritten table is empty.
    migration
        .batch_execute(
            "LOCK TABLE core_memory.schema_migrations IN ACCESS EXCLUSIVE MODE;
             ALTER TABLE core_memory.events
                 ADD COLUMN probe double precision DEFAULT pg_catalog.random();",
        )
        .unwrap();
    let exported = std::thread::scope(|s| {
        let export = s.spawn(move || store.export("w1", 0, 10, None));
        std::thread::sleep(std::time::Duration::from_millis(300));
        assert!(!export.is_finished(), "an export waits for the migration");
        migration
            .batch_execute(
                "INSERT INTO core_memory.schema_migrations (version, name, checksum)
                 VALUES (9999, 'from the future', 'x')",
            )
            .unwrap();
        migration.commit().unwrap();
        export.join().unwrap()
    });
    // Not an empty page, which would read as the end of the export.
    assert!(
        matches!(exported, Err(StoreError::SchemaMismatch(_))),
        "{exported:?}"
    );
}

#[test]
fn a_migration_waits_for_a_read_in_progress() {
    let w = World::new();
    let mut reader = w.db.connect();
    // What the store's read transactions do first.
    reader
        .batch_execute(
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
             LOCK TABLE core_memory.schema_migrations IN ACCESS SHARE MODE;
             SELECT count(*) FROM core_memory.events;",
        )
        .unwrap();
    let mut migrator = w.db.connect();
    migrator
        .batch_execute("SET lock_timeout = '300ms'")
        .unwrap();
    let message = refused(
        &mut migrator,
        "BEGIN; LOCK TABLE core_memory.schema_migrations IN ACCESS EXCLUSIVE MODE",
    );
    assert!(message.contains("lock timeout"), "{message}");
    migrator.batch_execute("ROLLBACK").unwrap();
    reader.batch_execute("COMMIT").unwrap();
    migrator
        .batch_execute(
            "BEGIN; LOCK TABLE core_memory.schema_migrations IN ACCESS EXCLUSIVE MODE; ROLLBACK",
        )
        .unwrap();
}

// ---- the governor role ----

#[test]
fn a_governor_login_governs_through_the_store_and_reads_nothing() {
    let mut w = World::new();
    let (event_id, _) = w.accept(&chat("1", "private text"));
    let (second, _) = w.accept(&chat("2", "more private text"));
    let mut role = Role::new(&w.db, "governor");
    role.grant("GRANT core_memory_governor TO {role}");

    let mut governor = PostgresStore::new(&ConnectConfig::new(url_as(&w.db, &role.name))).unwrap();
    governor.check().unwrap();
    assert!(governor.create_workspace("w2").unwrap());
    assert_eq!(
        governor.enroll_scope("beta", "w2").unwrap(),
        Enrollment::Enrolled
    );
    assert_eq!(
        governor
            .set_scope_state("acme", ScopeState::Paused)
            .unwrap(),
        ScopeState::Active
    );
    assert_eq!(
        governor
            .erase_event(&event_id, "user_request", "owner")
            .unwrap(),
        EraseOutcome::Erased
    );
    assert_eq!(
        governor
            .erase_events(&[second], "user_request", "owner")
            .unwrap(),
        [(second, EraseOutcome::Erased)]
    );
    assert_eq!(
        governor
            .erase_scope("acme", "revoked", "owner", None)
            .unwrap(),
        0
    );
    // It cannot read what it governs, or admit.
    assert!(governor.export("w1", 0, 10, None).is_err());
    assert!(governor.scope("acme").is_err());
}

#[test]
fn a_governor_cannot_run_code_as_the_schema_owner() {
    let w = World::new();
    let mut role = Role::new(&w.db, "governor");
    role.grant("GRANT core_memory_governor TO {role}; GRANT TEMPORARY ON DATABASE {db} TO {role}");
    let mut raw = Client::connect(&url_as(&w.db, &role.name), postgres::NoTls).unwrap();
    // A temporary type named like a built-in one, whose check calls a
    // function of the governor's. If a function that runs as the owner
    // resolved `text` to it, that function would run as the owner too.
    raw.batch_execute(
        "CREATE TABLE pg_temp.proof (ran_as text);
         CREATE FUNCTION pg_temp.record(v pg_catalog.text) RETURNS boolean
         LANGUAGE plpgsql AS $$
         BEGIN
             INSERT INTO pg_temp.proof VALUES (current_user);
             RETURN true;
         END $$;
         CREATE DOMAIN pg_temp.text AS pg_catalog.text CHECK (pg_temp.record(VALUE));",
    )
    .unwrap();
    for call in [
        "SELECT core_memory.set_scope_state('acme', 'paused')",
        "SELECT core_memory.set_scope_state('acme', 'active')",
        "SELECT core_memory.create_workspace('w2')",
        "SELECT core_memory.enroll_scope('beta', 'w2')",
        "SELECT core_memory.erase_scope('acme', 'x', 'owner')",
        "SELECT core_memory.erase_key('acme', 'slack/T01', '1', 'x', 'owner')",
        "SELECT core_memory.erase_event('00000000-0000-0000-0000-000000000000', 'x', 'owner')",
        "SELECT * FROM core_memory.erase_events(
             ARRAY['00000000-0000-0000-0000-000000000000']::uuid[], 'x', 'owner')",
    ] {
        raw.batch_execute(call).unwrap();
    }
    let ran: i64 = raw
        .query_one("SELECT count(*) FROM pg_temp.proof", &[])
        .unwrap()
        .get(0);
    assert_eq!(ran, 0, "no function resolved a type to the temporary one");
}

// ---- one write lock per workspace ----

#[test]
fn a_pause_is_not_starved_by_a_busy_scope() {
    use std::sync::atomic::AtomicBool;
    use std::time::{Duration, Instant};

    let w = World::new();
    let stop = AtomicBool::new(false);
    let (db, engine, stop_ref) = (&w.db, &w.engine, &stop);
    std::thread::scope(|s| {
        let writers: Vec<_> = (0..4)
            .map(|writer| {
                s.spawn(move || {
                    let mut store = db.store();
                    let (mut accepted, mut refused_after) = (0u32, 0u32);
                    for i in 0.. {
                        let event = chat(&format!("{writer}-{i}"), "one of many");
                        match engine.admit(&mut store, &ScopeGrants::All, &event).unwrap() {
                            AdmissionOutcome::Accepted { .. } => accepted += 1,
                            AdmissionOutcome::Refused { .. } => refused_after += 1,
                            other => panic!("{other:?}"),
                        }
                        if stop_ref.load(Ordering::SeqCst) && refused_after > 0 {
                            break;
                        }
                    }
                    accepted
                })
            })
            .collect();
        // The pause is made once the writers are busy, however fast the
        // machine is: a fixed wait left a slow runner short of that.
        let mut busy = db.connect();
        let deadline = Instant::now() + Duration::from_secs(30);
        while busy
            .query_one("SELECT count(*) FROM core_memory.events", &[])
            .unwrap()
            .get::<_, i64>(0)
            <= 20
        {
            assert!(Instant::now() < deadline, "the writers never got busy");
            std::thread::sleep(Duration::from_millis(20));
        }
        let mut governor = db.store();
        let started = Instant::now();
        governor
            .set_scope_state("acme", ScopeState::Paused)
            .unwrap();
        let waited = started.elapsed();
        let at_pause: i64 = db
            .connect()
            .query_one("SELECT count(*) FROM core_memory.events", &[])
            .unwrap()
            .get(0);
        stop.store(true, Ordering::SeqCst);
        let accepted: u32 = writers.into_iter().map(|w| w.join().unwrap()).sum();
        assert!(accepted > 20, "the writers were busy: {accepted}");
        assert!(
            waited < Duration::from_secs(3),
            "the pause waited {waited:?}"
        );
        // Nothing was stored once the pause had returned.
        assert_eq!(i64::from(accepted), at_pause);
    });
}

#[test]
fn a_host_may_erase_and_then_revoke_in_one_transaction() {
    let mut w = World::new();
    let (event_id, _) = w.accept(&chat("1", "to be erased"));
    let mut host = w.db.connect();
    host.batch_execute(&format!(
        "BEGIN; SELECT core_memory.erase_event('{}', 'user_request', 'owner')",
        event_id.0
    ))
    .unwrap();
    let (db, engine) = (&w.db, &w.engine);
    let outcome = std::thread::scope(|s| {
        let writer = s.spawn(move || {
            let mut store = db.store();
            engine.admit(&mut store, &ScopeGrants::All, &chat("2", "in flight"))
        });
        std::thread::sleep(std::time::Duration::from_millis(400));
        assert!(!writer.is_finished());
        // The admission is waiting for the workspace the host holds. The
        // host goes on to change the scope, and neither blocks the other
        // for good.
        host.batch_execute("SELECT core_memory.set_scope_state('acme', 'revoked'); COMMIT")
            .unwrap();
        writer.join().unwrap().unwrap()
    });
    assert!(matches!(
        outcome,
        AdmissionOutcome::Refused {
            reason: RefusalReason::ScopeNotActive,
            ..
        }
    ));
}

#[test]
fn governance_needs_read_committed_isolation() {
    let mut w = World::new();
    let (event_id, _) = w.accept(&chat("1", "stored"));
    let mut host = w.db.connect();
    let calls = [
        "SELECT core_memory.create_workspace('w2')".to_string(),
        "SELECT core_memory.enroll_scope('other', 'w1')".to_string(),
        "SELECT core_memory.set_scope_state('acme', 'paused')".to_string(),
        format!("SELECT core_memory.erase_event('{}', 'x', 'x')", event_id.0),
        format!(
            "SELECT * FROM core_memory.erase_events(ARRAY['{}']::uuid[], 'x', 'x')",
            event_id.0
        ),
        "SELECT core_memory.erase_key('acme', 'slack/T01', '1', 'x', 'x')".to_string(),
        "SELECT core_memory.erase_scope('acme', 'x', 'x')".to_string(),
    ];
    for level in ["REPEATABLE READ", "SERIALIZABLE"] {
        for call in &calls {
            let message = refused(
                &mut host,
                &format!("BEGIN ISOLATION LEVEL {level}; {call};"),
            );
            host.batch_execute("ROLLBACK").unwrap();
            assert!(
                message.contains("READ COMMITTED"),
                "{level}: {call}: {message}"
            );
        }
    }
    assert_eq!(
        w.db.store().scope("acme").unwrap().unwrap().state,
        ScopeState::Active
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.events WHERE state = 'live'"),
        1
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.workspaces"), 1);

    // An event admitted after the transaction took its snapshot is not
    // reported as unknown: the call is refused before it looks.
    host.batch_execute(
        "BEGIN ISOLATION LEVEL REPEATABLE READ; SELECT count(*) FROM core_memory.events",
    )
    .unwrap();
    let (late, _) = w.accept(&chat("2", "admitted after the snapshot"));
    let message = refused(
        &mut host,
        &format!("SELECT core_memory.erase_event('{}', 'x', 'x')", late.0),
    );
    host.batch_execute("ROLLBACK").unwrap();
    assert!(message.contains("READ COMMITTED"), "{message}");

    // PostgreSQL runs READ UNCOMMITTED as READ COMMITTED.
    host.batch_execute(
        "BEGIN ISOLATION LEVEL READ UNCOMMITTED;
         SELECT core_memory.set_scope_state('acme', 'paused');
         COMMIT",
    )
    .unwrap();
    assert_eq!(
        w.db.store().scope("acme").unwrap().unwrap().state,
        ScopeState::Paused
    );
}

#[test]
fn the_store_works_whatever_isolation_level_the_database_defaults_to() {
    let mut w = World::new();
    w.db.connect()
        .batch_execute(
            "DO $$ BEGIN EXECUTE format(
                 'ALTER DATABASE %I SET default_transaction_isolation = ''serializable''',
                 current_database()); END $$",
        )
        .unwrap();
    // New connections take the database's default.
    let mut store = w.db.store();
    let outcome = w
        .engine
        .admit(&mut store, &ScopeGrants::All, &chat("1", "stored"))
        .unwrap();
    assert!(
        matches!(outcome, AdmissionOutcome::Accepted { .. }),
        "{outcome:?}"
    );
    assert_eq!(
        store.set_scope_state("acme", ScopeState::Paused).unwrap(),
        ScopeState::Active
    );
    assert_eq!(store.erase_scope("acme", "x", "owner", None).unwrap(), 1);
    assert_eq!(w.export().len(), 0);
}

#[test]
fn enrolling_one_new_scope_from_many_sessions_enrolls_it_once() {
    let w = World::new();
    let db = &w.db;
    let results: Vec<Enrollment> = std::thread::scope(|s| {
        let calls: Vec<_> = (0..8)
            .map(|_| s.spawn(move || db.store().enroll_scope("fresh", "w1").unwrap()))
            .collect();
        calls.into_iter().map(|c| c.join().unwrap()).collect()
    });
    assert_eq!(
        results
            .iter()
            .filter(|r| **r == Enrollment::Enrolled)
            .count(),
        1,
        "{results:?}"
    );
    assert_eq!(
        results
            .iter()
            .filter(|r| **r == Enrollment::Unchanged)
            .count(),
        7,
        "{results:?}"
    );
}

// ---- erasing more than one event ----

#[test]
fn erasing_a_key_erases_every_revision_of_it() {
    let mut w = World::new();
    w.accept(&crm("opp-1", "open", 100));
    w.accept(&crm("opp-1", "negotiation", 100));
    w.accept(&crm("opp-1", "closed_won", 100));
    w.accept(&crm("opp-2", "open", 5));
    assert_eq!(
        w.store
            .erase_key("acme", "salesforce/prod", "opp-1", "user_request", "owner")
            .unwrap(),
        3
    );
    assert_eq!(
        w.store
            .erase_key("acme", "salesforce/prod", "opp-1", "user_request", "owner")
            .unwrap(),
        0
    );
    let left = w.export();
    assert_eq!(left.len(), 1);
    assert_eq!(left[0].event.key.external_id, "opp-2");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.erasures"), 3);
    // No version of the erased record comes back, old or new.
    for stage in ["open", "negotiation", "closed_lost"] {
        assert!(matches!(
            w.admit(&crm("opp-1", stage, 100)),
            AdmissionOutcome::Refused {
                reason: RefusalReason::Tombstoned,
                ..
            }
        ));
    }
    let err = w
        .store
        .erase_key("nowhere", "salesforce/prod", "opp-1", "x", "owner")
        .unwrap_err();
    assert!(matches!(err, StoreError::Rejected(_)), "{err:?}");
}

#[test]
fn a_list_of_events_is_erased_in_one_call() {
    let mut w = World::new();
    w.store.create_workspace("w2").unwrap();
    w.store.enroll_scope("globex", "w2").unwrap();
    let (a, _) = w.accept(&chat("a", "the first secret"));
    let (b, _) = w.accept(&chat("b", "the second secret"));
    let (kept, kept_chunks) = w.accept(&chat("kept", "not on the list"));
    let (c, _) = w.accept(&chat_in("globex", "c", "a secret in another workspace"));
    let (gone, _) = w.accept(&chat("gone", "erased earlier"));
    w.store.erase_event(&gone, "user_request", "owner").unwrap();
    let unknown: EventId = "00000000-0000-8000-8000-000000000007".parse().unwrap();
    let before = w.count("SELECT sum(last_seq)::bigint FROM core_memory.workspace_seq");

    // One row for each distinct ID, in ID order.
    let outcomes = w
        .store
        .erase_events(&[b, a, c, gone, unknown, a], "user_request", "owner")
        .unwrap();
    let mut expected = vec![
        (a, EraseOutcome::Erased),
        (b, EraseOutcome::Erased),
        (c, EraseOutcome::Erased),
        (gone, EraseOutcome::AlreadyErased),
        (unknown, EraseOutcome::Unknown),
    ];
    expected.sort_by_key(|(id, _)| *id);
    assert_eq!(outcomes, expected);

    // Only the event that was not listed is left, with its text.
    let live: Vec<EventId> =
        w.db.connect()
            .query(
                "SELECT event_id FROM core_memory.events WHERE state = 'live'",
                &[],
            )
            .unwrap()
            .iter()
            .map(|row| EventId(row.get(0)))
            .collect();
    assert_eq!(live, [kept]);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunk_text"),
        kept_chunks.len() as i64
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.beads WHERE state = 'live'"),
        kept_chunks.len() as i64
    );
    // One journal row and one number for each event erased, taken from
    // its own workspace's counter in the order the events were admitted.
    assert_eq!(
        w.count("SELECT sum(last_seq)::bigint FROM core_memory.workspace_seq"),
        before + 3
    );
    let journal: Vec<(String, EventId)> =
        w.db.connect()
            .query(
                "SELECT workspace_id, event_id FROM core_memory.erasures
                  WHERE reason = 'user_request' AND event_id <> $1
                  ORDER BY workspace_id, seq",
                &[&gone.0],
            )
            .unwrap()
            .iter()
            .map(|row| (row.get(0), EventId(row.get(1))))
            .collect();
    assert_eq!(
        journal,
        [
            ("w1".to_string(), a),
            ("w1".to_string(), b),
            ("w2".to_string(), c)
        ]
    );

    // Asking again erases nothing more.
    let again = w.store.erase_events(&[a, b, c], "again", "owner").unwrap();
    assert!(
        again
            .iter()
            .all(|(_, outcome)| *outcome == EraseOutcome::AlreadyErased),
        "{again:?}"
    );
    assert!(w.store.erase_events(&[], "x", "owner").unwrap().is_empty());
    let err = w.store.erase_events(&[kept], " ", "owner").unwrap_err();
    assert!(matches!(err, StoreError::Rejected(_)), "{err:?}");
    let mut host = w.db.connect();
    for sql in [
        "SELECT * FROM core_memory.erase_events(NULL, 'x', 'owner')",
        "SELECT * FROM core_memory.erase_events(ARRAY[NULL]::uuid[], 'x', 'owner')",
        "SELECT * FROM core_memory.erase_events(ARRAY[]::uuid[], 'x', NULL)",
    ] {
        refused(&mut host, sql);
    }
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.events WHERE state = 'live'"),
        1
    );
}

#[test]
fn a_long_list_is_erased_in_the_order_its_events_were_admitted() {
    let mut w = World::new();
    let mut ids: Vec<EventId> = (0..600)
        .map(|i| w.accept(&chat(&format!("m{i}"), "one of many")).0)
        .collect();
    let before = w.count("SELECT last_seq FROM core_memory.workspace_seq");
    // The order of the list does not matter.
    ids.reverse();
    let outcomes = w.store.erase_events(&ids, "user_request", "owner").unwrap();
    assert_eq!(outcomes.len(), 600);
    assert!(
        outcomes
            .iter()
            .all(|(_, outcome)| *outcome == EraseOutcome::Erased)
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_text"), 0);
    // Each erasure took one number, in the order the events were admitted.
    assert_eq!(
        w.count("SELECT last_seq FROM core_memory.workspace_seq"),
        before + 600
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM (
                 SELECT j.seq, lag(j.seq) OVER (ORDER BY e.seq) AS previous
                   FROM core_memory.erasures AS j JOIN core_memory.events AS e USING (event_id)
             ) AS ordered WHERE previous IS NOT NULL AND seq <> previous + 1"
        ),
        0
    );
}

#[test]
fn a_large_scope_is_erased_in_batches() {
    let mut w = World::new();
    w.store.enroll_scope("beta", "w1").unwrap();
    for i in 0..1_200 {
        w.accept(&chat(&format!("a{i}"), "acme text"));
    }
    w.accept(&chat_in("beta", "b1", "beta text"));
    let before = w.count("SELECT last_seq FROM core_memory.workspace_seq");

    let started = std::time::Instant::now();
    let mut batches = Vec::new();
    loop {
        let erased = w
            .store
            .erase_scope("acme", "revoked", "owner", Some(500))
            .unwrap();
        if erased == 0 {
            break;
        }
        batches.push(erased);
    }
    assert_eq!(batches, [500, 500, 200]);
    assert!(started.elapsed() < std::time::Duration::from_secs(20));
    // Each erasure took one number, in the order the events were admitted.
    assert_eq!(
        w.count("SELECT last_seq FROM core_memory.workspace_seq"),
        before + 1_200
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM (
                 SELECT j.seq, lag(j.seq) OVER (ORDER BY e.seq) AS previous
                   FROM core_memory.erasures AS j JOIN core_memory.events AS e USING (event_id)
             ) AS ordered WHERE previous IS NOT NULL AND seq <> previous + 1"
        ),
        0
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_text"), 1);
    let err = w
        .store
        .erase_scope("acme", "x", "owner", Some(0))
        .unwrap_err();
    assert!(matches!(err, StoreError::Rejected(_)), "{err:?}");
}

#[test]
fn the_planner_keeps_no_sample_of_what_sources_said() {
    let mut w = World::new();
    for i in 0..150 {
        let mut event = chat(&format!("m{i}"), &format!("a private remark, number {i}"));
        // One reader position for each event, so that table is sampled too.
        event.checkpoint = Some(checkpoint(&format!("session-{i}"), 0, 1));
        w.accept(&event);
    }
    w.db.connect()
        .batch_execute(
            "ANALYZE core_memory.events; ANALYZE core_memory.chunk_text;
             ANALYZE core_memory.beads; ANALYZE core_memory.checkpoints",
        )
        .unwrap();
    let sampled = |columns: &str| {
        w.count(&format!(
            "SELECT count(*) FROM pg_stats
              WHERE schemaname = 'core_memory'
                AND tablename IN ('events', 'chunk_text', 'beads', 'checkpoints')
                AND attname IN ({columns})"
        ))
    };
    assert!(
        sampled("'scope', 'seq', 'chunk_id', 'captured_at', 'created_at', 'sequence'") >= 9,
        "ANALYZE collected statistics"
    );
    // time_at is the source's own time until the event is erased, and a
    // checkpoint's hash may be a hash of what its reader last read.
    assert_eq!(
        sampled(
            "'text', 'facets', 'content_hash', 'event_hash', 'info',
             'source_ns', 'external_id', 'revision', 'time_at', 'hash'"
        ),
        0
    );
}

// ---- what another role can plant ----

#[test]
fn functions_planted_in_public_are_never_called() {
    const PLANTED: &str = "
        CREATE FUNCTION public.convert_to(text, text) RETURNS bytea LANGUAGE plpgsql
            AS $$ BEGIN RAISE EXCEPTION 'a planted function ran'; END $$;
        CREATE FUNCTION public.unnest(uuid[]) RETURNS SETOF uuid LANGUAGE plpgsql
            AS $$ BEGIN RAISE EXCEPTION 'a planted function ran'; END $$;
        CREATE FUNCTION public.sha256(text) RETURNS bytea LANGUAGE plpgsql
            AS $$ BEGIN RAISE EXCEPTION 'a planted function ran'; END $$;
        CREATE FUNCTION public.clock_timestamp() RETURNS timestamptz LANGUAGE plpgsql
            AS $$ BEGIN RAISE EXCEPTION 'a planted function ran'; END $$;";

    // Planted before the schema exists, so the migration would bind them
    // into the table's check for good.
    let fresh = TestDb::create_empty();
    fresh.connect().batch_execute(PLANTED).unwrap();
    let mut store = fresh.store();
    store.migrate().unwrap();
    store.create_workspace("w1").unwrap();
    store.enroll_scope("acme", "w1").unwrap();
    let clock = std::sync::Arc::new(core_memory_core::ManualClock::at_unix_millis(common::T0));
    let engine = common::engine_with(common::CONFIG, &clock);
    let mut event = chat("1", "stored with the built-in functions");
    event.checkpoint = Some(checkpoint("session-a", 0, 1));
    let outcome = engine.admit(&mut store, &ScopeGrants::All, &event).unwrap();
    assert!(
        matches!(outcome, AdmissionOutcome::Accepted { .. }),
        "{outcome:?}"
    );
    assert_eq!(store.erase_scope("acme", "x", "owner", None).unwrap(), 1);

    // Planted after, in a database that was migrated cleanly.
    let mut w = World::new();
    w.db.connect().batch_execute(PLANTED).unwrap();
    let mut store = w.db.store();
    w.accept(&chat("0", "before"));
    let outcome = w
        .engine
        .admit(&mut store, &ScopeGrants::All, &chat("1", "after"))
        .unwrap();
    assert!(
        matches!(outcome, AdmissionOutcome::Accepted { .. }),
        "{outcome:?}"
    );
    assert_eq!(store.scopes().unwrap()[0].live_events, 2);
}

#[test]
fn a_schema_someone_else_created_is_not_migrated() {
    let db = TestDb::create_empty();
    let mut squatter = Role::new(&db, "squatter");
    squatter.grant("GRANT CREATE ON DATABASE {db} TO {role}");
    Client::connect(&url_as(&db, &squatter.name), postgres::NoTls)
        .unwrap()
        .batch_execute(
            "CREATE SCHEMA core_memory;
             CREATE TABLE core_memory.schema_migrations (
                 version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT clock_timestamp());
             CREATE TABLE core_memory.proof (ran_as text);
             CREATE FUNCTION core_memory.record() RETURNS trigger LANGUAGE plpgsql AS $$
             BEGIN
                 INSERT INTO core_memory.proof VALUES (current_user);
                 RETURN NEW;
             END $$;
             CREATE TRIGGER record BEFORE INSERT ON core_memory.schema_migrations
                 FOR EACH ROW EXECUTE FUNCTION core_memory.record();",
        )
        .unwrap();
    let err = db.store().migrate().unwrap_err();
    assert!(matches!(err, StoreError::Rejected(_)), "{err:?}");
    assert!(err.to_string().contains("was not created by"), "{err}");
    let ran: i64 = db
        .connect()
        .query_one("SELECT count(*) FROM core_memory.proof", &[])
        .unwrap()
        .get(0);
    assert_eq!(
        ran, 0,
        "nothing of the other role's ran as the migrating role"
    );
    squatter.grant("DROP SCHEMA core_memory CASCADE");
}

#[test]
fn a_database_owner_without_createrole_can_migrate_once_the_roles_exist() {
    // Any migrated database in the cluster means the two roles exist.
    let _existing = TestDb::create();
    let db = TestDb::create_empty();
    let mut owner = Role::new(&db, "owner");
    owner.grant("ALTER DATABASE {db} OWNER TO {role}");
    let mut store = PostgresStore::new(&ConnectConfig::new(url_as(&db, &owner.name))).unwrap();
    let report = store.migrate().unwrap();
    assert_eq!((report.from, report.to), (0, SCHEMA_VERSION));
    assert!(store.create_workspace("w1").unwrap());
    // Hand the database back so the role can be dropped.
    owner.grant("REASSIGN OWNED BY {role} TO CURRENT_USER");
}

// ---- errors that should and should not be retried ----

#[test]
fn a_newer_store_is_not_migrated_backwards() {
    let db = TestDb::create();
    db.connect()
        .batch_execute(&format!(
            "INSERT INTO core_memory.schema_migrations (version, name, checksum)
             VALUES ({}, 'from the future', 'x')",
            SCHEMA_VERSION + 1
        ))
        .unwrap();
    let err = db.store().migrate().unwrap_err();
    assert!(matches!(err, StoreError::SchemaMismatch(_)), "{err:?}");
    assert!(err.to_string().contains("newer than this build"), "{err}");
}

#[test]
fn reads_are_refused_too_once_the_schema_has_moved_on() {
    let mut w = World::new();
    let mut event = chat("1", "stored");
    event.checkpoint = Some(checkpoint("session-a", 0, 1));
    w.accept(&event);
    w.db.connect()
        .batch_execute(
            "INSERT INTO core_memory.schema_migrations (version, name, checksum)
             VALUES (9999, 'from the future', 'x')",
        )
        .unwrap();
    // The same connection, which checked the version when it opened.
    let replay = w.engine.admit(&mut w.store, &ScopeGrants::All, &event);
    assert!(
        matches!(
            replay,
            Err(core_memory_core::EngineError::Store(
                StoreError::SchemaMismatch(_)
            ))
        ),
        "{replay:?}"
    );
    let asked = core_memory_core::RecallQuery {
        query: "stored".into(),
        scopes: ScopeGrants::All,
        limit: 10,
        follow: 1,
        include_ended: false,
    };
    for result in [
        w.store.checkpoint("acme", "session-a").map(|_| ()),
        w.store.export("w1", 0, 10, None).map(|_| ()),
        w.store.recall(&asked).map(|_| ()),
    ] {
        assert!(
            matches!(result, Err(StoreError::SchemaMismatch(_))),
            "{result:?}"
        );
    }
}

#[test]
fn a_read_that_a_migration_left_unable_to_run_reports_the_new_version() {
    let mut w = World::new();
    let mut event = chat("1", "stored");
    event.checkpoint = Some(checkpoint("session-a", 0, 1));
    let (event_id, _) = w.accept(&event);
    // A connection that checked the version when it opened.
    let mut store = w.db.store();
    store.check().unwrap();
    let mut owner = w.db.connect();

    // A statement that fails against this build's own version is the
    // store's failure, and is reported as one.
    owner
        .batch_execute("ALTER TABLE core_memory.scopes RENAME COLUMN state TO state_was")
        .unwrap();
    let err = store.scope("acme").unwrap_err();
    assert!(
        matches!(&err, StoreError::Backend(why) if why.contains("SQLSTATE 42703")),
        "{err:?}"
    );
    owner
        .batch_execute("ALTER TABLE core_memory.scopes RENAME COLUMN state_was TO state")
        .unwrap();
    assert!(store.scope("acme").unwrap().is_some());

    // A migration that renames a column each one-statement read uses. The
    // statements no longer run, so none of them shows the version.
    let next = SCHEMA_VERSION + 1;
    owner
        .batch_execute(&format!(
            "BEGIN;
             LOCK TABLE core_memory.schema_migrations IN ACCESS EXCLUSIVE MODE;
             ALTER TABLE core_memory.scopes RENAME COLUMN state TO state_was;
             ALTER TABLE core_memory.events RENAME COLUMN event_hash TO event_hash_was;
             ALTER TABLE core_memory.checkpoints RENAME COLUMN sequence TO sequence_was;
             INSERT INTO core_memory.schema_migrations (version, name, checksum)
             VALUES ({next}, 'renames', 'x');
             COMMIT;"
        ))
        .unwrap();
    for (what, result) in [
        ("scope", store.scope("acme").map(|_| ())),
        ("event", store.event(&event_id).map(|_| ())),
        (
            "checkpoint",
            store.checkpoint("acme", "session-a").map(|_| ()),
        ),
    ] {
        assert!(
            matches!(&result, Err(StoreError::SchemaMismatch(why))
                if why.contains(&format!("schema version {next}"))),
            "{what}: {result:?}"
        );
    }
    // Every admission starts with one of them.
    let replay = w.engine.admit(&mut store, &ScopeGrants::All, &event);
    assert!(
        matches!(
            replay,
            Err(core_memory_core::EngineError::Store(
                StoreError::SchemaMismatch(_)
            ))
        ),
        "{replay:?}"
    );
}

#[test]
fn a_database_that_takes_no_writes_is_unavailable_not_broken() {
    let mut w = World::new();
    w.accept(&chat("0", "before the failover"));
    w.db.connect()
        .batch_execute(
            "DO $$ BEGIN EXECUTE format(
                 'ALTER DATABASE %I SET default_transaction_read_only = on',
                 current_database()); END $$",
        )
        .unwrap();
    // A new connection, as after a failover to a standby.
    let mut store = w.db.store();
    let err = store.check().unwrap_err();
    assert!(
        matches!(&err, StoreError::Unavailable(why) if why.contains("read-only")),
        "{err:?}"
    );
    let err = w
        .engine
        .admit(&mut store, &ScopeGrants::All, &chat("1", "not stored"))
        .unwrap_err();
    assert!(
        matches!(
            &err,
            core_memory_core::EngineError::Store(StoreError::Unavailable(why))
                if why.contains("read-only")
        ),
        "{err:?}"
    );
    // Reads still work, and a replay needs no write.
    assert!(matches!(
        w.engine
            .admit(
                &mut store,
                &ScopeGrants::All,
                &chat("0", "before the failover")
            )
            .unwrap(),
        AdmissionOutcome::Replay { .. }
    ));
}

#[test]
fn a_stored_value_that_does_not_read_back_is_corruption_not_a_retry() {
    let mut w = World::new();
    w.accept(&chat("1", "fine"));
    // Only the owner, with the trigger off, can do this.
    w.db.connect()
        .batch_execute(
            "ALTER TABLE core_memory.events DISABLE TRIGGER events_only_erase;
             UPDATE core_memory.events SET info = '{}';
             ALTER TABLE core_memory.events ENABLE TRIGGER events_only_erase;",
        )
        .unwrap();
    let err = w.store.export("w1", 0, 10, None).unwrap_err();
    assert!(matches!(err, StoreError::Corrupt(_)), "{err:?}");
}

#[test]
fn a_login_the_server_refuses_is_not_a_store_to_wait_for() {
    let db = TestDb::create();
    let (scheme, rest) = db.url.split_once("://").unwrap();
    let (_, address) = rest.split_once('@').unwrap();
    let (host, _) = address.split_once('/').unwrap();
    let role = Role::new(&db, "login");
    for (what, url, state) in [
        (
            "a wrong password",
            format!("{scheme}://{}:not-the-password@{address}", role.name),
            "28P01",
        ),
        (
            "a role that does not exist",
            format!("{scheme}://cm_t_nobody:synthetic@{address}"),
            "28P01",
        ),
        (
            "a database that does not exist",
            format!(
                "{scheme}://{}:synthetic@{host}/cm_t_no_such_database",
                role.name
            ),
            "3D000",
        ),
    ] {
        let err = PostgresStore::new(&ConnectConfig::new(url))
            .unwrap()
            .check()
            .unwrap_err();
        // Not `Unavailable`: nothing will change by waiting.
        let StoreError::AccessRefused(message) = &err else {
            panic!("{what}: {err:?}");
        };
        assert!(
            message.contains(&format!("SQLSTATE {state}")),
            "{what}: {message}"
        );
        assert!(!message.contains("not-the-password"), "{message}");
    }
}

#[test]
fn a_permanent_database_error_says_what_it_was() {
    let w = World::new();
    let mut role = Role::new(&w.db, "reader");
    role.grant(
        "GRANT USAGE ON SCHEMA core_memory TO {role};
                GRANT SELECT ON core_memory.schema_migrations TO {role}",
    );
    // A login with no table privilege: the store's query is refused.
    let mut store = PostgresStore::new(&ConnectConfig::new(url_as(&w.db, &role.name))).unwrap();
    let err = store.scopes().unwrap_err();
    let StoreError::Backend(message) = &err else {
        panic!("{err:?}");
    };
    assert!(message.contains("permission denied"), "{message}");
    assert!(message.contains("SQLSTATE 42501"), "{message}");
}

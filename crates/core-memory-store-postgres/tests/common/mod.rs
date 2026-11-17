//! Shared setup for tests that run the engine against PostgreSQL.

#![allow(dead_code, clippy::unwrap_used)]

use std::sync::Arc;

use core_memory_contract::{
    AdmissionOutcome, Checkpoint, ChunkId, EventId, Sha256Digest, SourceEvent,
};
use core_memory_core::store::{Change, ExportedEvent, Store};
use core_memory_core::{Engine, EngineConfig, ManualClock, ScopeGrants};
use core_memory_store_postgres::PostgresStore;
use core_memory_store_postgres::testing::TestDb;
use serde_json::json;

pub const T0: i64 = 1_793_000_000_000;

pub const CONFIG: &str = r#"
[vocabulary]
id = "acme.types"
types = [
  { key = "decision", rev = 1 },
  { key = "outcome", rev = 2 },
]

[[profile]]
id = "acme.crm"
rev = 1
source_type = "relational"
source_system = "salesforce"
content = "json"
editable = true
types = ["outcome"]
chunker = { id = "json.whole", rev = 1, max = 4096 }
identifiers = [{ kind = "opportunity", pattern = "006[A-Za-z0-9]{12}" }]

[[profile]]
id = "acme.notes"
rev = 1
source_type = "document"
content = "text"
max_event_bytes = 200
chunker = { id = "text.structural", rev = 1, max = 40 }
"#;

/// A migrated database with workspace `w1`, whose ID key is fixed so IDs
/// are the same on every run, and the active scope `acme`.
pub struct World {
    pub db: TestDb,
    pub store: PostgresStore,
    pub engine: Engine,
    pub clock: Arc<ManualClock>,
}

pub fn engine_with(config: &str, clock: &Arc<ManualClock>) -> Engine {
    let config: EngineConfig = toml::from_str(config).unwrap();
    Engine::new(config, clock.clone()).unwrap()
}

impl World {
    pub fn new() -> Self {
        Self::with_config(CONFIG)
    }

    pub fn with_config(config: &str) -> Self {
        let db = TestDb::create();
        db.connect()
            .batch_execute(
                "INSERT INTO core_memory.workspaces (workspace_id, id_key, created_at)
                 VALUES ('w1', decode(repeat('07', 32), 'hex'), clock_timestamp());
                 INSERT INTO core_memory.workspace_seq (workspace_id, last_seq) VALUES ('w1', 0);
                 SELECT core_memory.enroll_scope('acme', 'w1');",
            )
            .unwrap();
        let clock = Arc::new(ManualClock::at_unix_millis(T0));
        let engine = engine_with(config, &clock);
        let store = db.store();
        Self {
            db,
            store,
            engine,
            clock,
        }
    }

    pub fn admit(&mut self, event: &SourceEvent) -> AdmissionOutcome {
        self.engine
            .admit(&mut self.store, &ScopeGrants::All, event)
            .unwrap()
    }

    pub fn accept(&mut self, event: &SourceEvent) -> (EventId, Vec<ChunkId>) {
        match self.admit(event) {
            AdmissionOutcome::Accepted {
                event_id,
                chunk_ids,
                ..
            } => (event_id, chunk_ids),
            other => panic!("not accepted: {other:?}"),
        }
    }

    /// Every live event of workspace `w1`, in admission order.
    pub fn export(&mut self) -> Vec<ExportedEvent> {
        events(self.changes())
    }

    /// Every change to workspace `w1`, in `seq` order.
    pub fn changes(&mut self) -> Vec<Change> {
        self.store.export("w1", 0, 10_000, None).unwrap()
    }

    /// One value from a query run as the database owner.
    pub fn count(&self, sql: &str) -> i64 {
        self.db.connect().query_one(sql, &[]).unwrap().get(0)
    }
}

pub fn chat(external_id: &str, text: &str) -> SourceEvent {
    chat_in("acme", external_id, text)
}

pub fn chat_in(scope: &str, external_id: &str, text: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": scope,
        "source_ns": "slack/T01",
        "external_id": external_id,
        "profile_id": "core.chat_transcript",
        "occurred_at": {"at": "2026-10-26T14:00:00Z", "precision": "second"},
        "actor": {"id": "U1", "kind": "human", "role": "user"},
        "media_type": "text/plain",
        "content": {"text": text},
        "structure": {"thread": "C1/1"},
        "parser_contract_version": "slack-export/1"
    }))
    .unwrap()
}

pub fn crm(external_id: &str, action: &str, amount: u64) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": "acme",
        "source_ns": "salesforce/prod",
        "external_id": external_id,
        "profile_id": "acme.crm",
        "media_type": "application/json",
        "content": {"json": {"Id": "006AAAAAAAAAAAA", "StageName": action, "Amount": amount}},
        "structure": {"record": {
            "system": "salesforce", "object_type": "Opportunity",
            "object_key": "006AAAAAAAAAAAA", "action": action
        }},
        "parser_contract_version": "sf-cdc/1"
    }))
    .unwrap()
}

pub fn note(external_id: &str, text: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": "acme",
        "source_ns": "notes",
        "external_id": external_id,
        "profile_id": "acme.notes",
        "media_type": "text/plain",
        "content": {"text": text},
        "parser_contract_version": "1"
    }))
    .unwrap()
}

pub fn checkpoint(key: &str, expected: u64, next: u64) -> Checkpoint {
    Checkpoint {
        key: key.into(),
        expected_sequence: expected,
        next_sequence: next,
        hash: Sha256Digest::of(format!("{key}@{next}").as_bytes()),
    }
}

/// The events among `changes`.
pub fn events(changes: Vec<Change>) -> Vec<ExportedEvent> {
    changes
        .into_iter()
        .filter_map(|change| match change {
            Change::Event(event) => Some(*event),
            Change::Read { .. } | Change::Erasure { .. } => None,
        })
        .collect()
}

/// Runs `call` while a migration holds the schema, and returns what it
/// returned once the migration has gone on to change tables and then
/// rolled back.
///
/// While the call waits it must hold no other lock on the schema's tables.
/// If it did, a migration that changed one of them would wait for the
/// call, the call for the migration, and PostgreSQL would end one of them.
pub fn behind_a_migration<T: Send>(db: &TestDb, what: &str, call: impl FnOnce() -> T + Send) -> T {
    use std::time::{Duration, Instant};

    let mut migrator = db.connect();
    let mut observer = db.connect();
    let mut migration = migrator.transaction().unwrap();
    migration
        .batch_execute("LOCK TABLE core_memory.schema_migrations IN ACCESS EXCLUSIVE MODE")
        .unwrap();
    std::thread::scope(|s| {
        let waiting = s.spawn(call);
        let deadline = Instant::now() + Duration::from_secs(4);
        let pid: i32 = loop {
            let waiter = observer
                .query_opt(
                    "SELECT l.pid FROM pg_locks AS l
                      WHERE NOT l.granted AND l.locktype = 'relation'
                        AND l.relation = 'core_memory.schema_migrations'::regclass
                        AND l.database = (SELECT oid FROM pg_database
                                           WHERE datname = current_database())",
                    &[],
                )
                .unwrap();
            if let Some(row) = waiter {
                break row.get(0);
            }
            assert!(
                !waiting.is_finished(),
                "{what} did not wait for the migration"
            );
            assert!(
                Instant::now() < deadline,
                "{what} never asked for the schema"
            );
            std::thread::sleep(Duration::from_millis(5));
        };
        let held: Vec<String> = observer
            .query(
                "SELECT c.relname || ' in ' || l.mode
                   FROM pg_locks AS l JOIN pg_class AS c ON c.oid = l.relation
                  WHERE l.pid = $1 AND l.granted AND l.locktype = 'relation'
                    AND c.relnamespace = 'core_memory'::regnamespace",
                &[&pid],
            )
            .unwrap()
            .iter()
            .map(|row| row.get(0))
            .collect();
        assert!(
            held.is_empty(),
            "{what} waited for the schema holding {held:?}"
        );
        // The migration goes on to change every table. With the call
        // holding one of them, this is where the two would deadlock.
        migration
            .batch_execute(
                "DO $$
                 DECLARE name text;
                 BEGIN
                     FOR name IN SELECT tablename FROM pg_catalog.pg_tables
                                  WHERE schemaname = 'core_memory'
                                    AND tablename <> 'schema_migrations'
                                  ORDER BY tablename
                     LOOP
                         EXECUTE pg_catalog.format(
                             'ALTER TABLE core_memory.%I ADD COLUMN probe integer', name);
                     END LOOP;
                 END $$",
            )
            .unwrap_or_else(|e| panic!("the migration could not go on past {what}: {e}"));
        migration.rollback().unwrap();
        waiting.join().unwrap()
    })
}

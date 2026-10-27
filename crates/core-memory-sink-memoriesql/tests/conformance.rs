//! The memoriesQL sink against the sink conformance suite, delivering into
//! a real memoriesQL database (CMP-02).
//!
//! Needs the `postgres-tests` feature and two variables:
//! `CORE_MEMORY_TEST_MEMORIESQL_URL`, an administrator URL for the server,
//! and `CORE_MEMORY_TEST_MEMORIESQL_TEMPLATE`, a database memoriesQL has
//! migrated to schema 55. Each harness copies the template, enrolls the
//! engine scopes `acme` and `beta`, and pairs an engine service credential
//! with write grants on both, and read access unless a test leaves it out.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use core_memory_conformance::{Harness, Scenario, checks};
use core_memory_core::sink::{Sink, WriteOutcome};
use core_memory_sink_memoriesql::{MemoriesqlConfig, MemoriesqlSink};
use postgres::{Client, NoTls};
use serde_json::Value;

const WORKSPACE: &str = "00000000-0000-4000-8000-0000000000a5";
const OWNER_SECRET: &str = "fictional conformance owner credential";
const ENGINE_SECRET: &str = "fictional conformance engine credential";
const SCOPES: [&str; 2] = ["acme", "beta"];

static NEXT: AtomicU32 = AtomicU32::new(0);

fn sha(text: &str) -> String {
    core_memory_contract::Sha256Digest::of(text.as_bytes()).to_hex()
}

fn uuid(n: u32) -> String {
    format!("00000000-0000-4000-8000-{n:012x}")
}

struct MemoriesqlHarness {
    admin: String,
    database: String,
    url: String,
}

impl MemoriesqlHarness {
    fn new() -> Self {
        Self::with_read_access(true)
    }

    fn with_read_access(reader: bool) -> Self {
        let admin = std::env::var("CORE_MEMORY_TEST_MEMORIESQL_URL")
            .expect("set CORE_MEMORY_TEST_MEMORIESQL_URL to an administrator URL");
        let template = std::env::var("CORE_MEMORY_TEST_MEMORIESQL_TEMPLATE")
            .expect("set CORE_MEMORY_TEST_MEMORIESQL_TEMPLATE to a schema-55 database");
        let database = format!(
            "cm_conformance_{}_{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::SeqCst)
        );
        let mut client = Client::connect(&admin, NoTls).unwrap();
        client
            .batch_execute(&format!(
                "CREATE DATABASE {database} TEMPLATE \"{}\"",
                template.replace('"', "")
            ))
            .unwrap();
        let mut config: postgres::Config = admin.parse().unwrap();
        config.dbname(&database);
        let url = format!(
            "{}{}dbname={database}",
            admin_keywords(&admin),
            if admin_keywords(&admin).is_empty() {
                ""
            } else {
                " "
            }
        );
        let harness = Self {
            admin,
            database,
            url,
        };
        harness.setup(&mut config.connect(NoTls).unwrap(), reader);
        harness
    }

    /// The owner bootstraps, enrolls both engine scopes, pairs the engine
    /// and grants it write on each, all through memoriesQL's functions.
    fn setup(&self, db: &mut Client, reader: bool) {
        let owner = sha(OWNER_SECRET);
        db.batch_execute(&format!(
            "BEGIN; SET LOCAL ROLE memoriesql_application;
             SELECT memoriesql.bootstrap_personal_local(
                 '{t}','{u}','{i}','{p}','{w}','{s}','{m}','{c}','{x}',
                 'memoriesql.local','{i}','Fictional conformance','{owner}',
                 now(), now() + interval '1 hour');
             COMMIT;",
            t = uuid(1),
            u = uuid(2),
            i = uuid(3),
            p = uuid(4),
            w = WORKSPACE,
            s = uuid(6),
            m = uuid(7),
            c = uuid(8),
            x = uuid(9),
        ))
        .unwrap();
        let as_owner = |db: &mut Client, statement: &str| {
            db.batch_execute(&format!(
                "BEGIN; SET LOCAL ROLE memoriesql_application;
                 SELECT memoriesql.begin_authorization_context('{owner}', '{WORKSPACE}');
                 {statement};
                 COMMIT;"
            ))
            .unwrap();
        };
        let mut sources = Vec::new();
        for (n, scope) in SCOPES.iter().enumerate() {
            let source = uuid(100 + n as u32);
            as_owner(
                db,
                &format!(
                    "SELECT * FROM memoriesql.enroll_exact_source_v1('{source}', 'core-memory', \
                     NULL, 'engine_scope', '{scope}', 1, true)"
                ),
            );
            sources.push(source);
        }
        let scopes: Vec<String> = sources
            .iter()
            .map(|source| {
                db.query_one(
                    "SELECT access_scope_id::text FROM memoriesql.source_objects \
                     WHERE source_object_id = $1::text::uuid",
                    &[source],
                )
                .unwrap()
                .get(0)
            })
            .collect();
        // Read access lets a conflict carry the stored record (REC-07).
        let (capabilities, grants) = if reader {
            ("'engine.records.deliver', 'source.read'", "'read', 'write'")
        } else {
            ("'engine.records.deliver'", "'write'")
        };
        let engine = uuid(200);
        as_owner(
            db,
            &format!(
                "SELECT memoriesql.pair_local_client('{engine}', '{}', '{}', '{}', 'service', \
                 'background_service', ARRAY[{capabilities}], \
                 ARRAY['{}', '{}']::uuid[], '{}', now(), now() + interval '1 hour')",
                uuid(201),
                uuid(202),
                uuid(203),
                scopes[0],
                scopes[1],
                sha(ENGINE_SECRET)
            ),
        );
        for (n, source) in sources.iter().enumerate() {
            as_owner(
                db,
                &format!(
                    "SELECT * FROM memoriesql.grant_exact_source_v1('{}', '{source}', '{engine}', \
                     ARRAY[{grants}], now(), now() + interval '1 hour')",
                    uuid(300 + n as u32)
                ),
            );
        }
    }
}

/// The administrator URL's keyword form without its database, so the
/// harness can name its own.
fn admin_keywords(admin: &str) -> String {
    let config: postgres::Config = admin.parse().unwrap();
    let mut parts = Vec::new();
    for host in config.get_hosts() {
        if let postgres::config::Host::Tcp(h) = host {
            parts.push(format!("host={h}"));
        }
    }
    if let Some(port) = config.get_ports().first() {
        parts.push(format!("port={port}"));
    }
    if let Some(user) = config.get_user() {
        parts.push(format!("user={user}"));
    }
    if let Some(password) = config.get_password() {
        parts.push(format!("password={}", String::from_utf8_lossy(password)));
    }
    parts.join(" ")
}

impl Drop for MemoriesqlHarness {
    fn drop(&mut self) {
        if let Ok(mut client) = Client::connect(&self.admin, NoTls) {
            let _ = client.batch_execute(&format!(
                "DROP DATABASE IF EXISTS {} WITH (FORCE)",
                self.database
            ));
        }
    }
}

impl Harness for MemoriesqlHarness {
    fn open(&mut self, max_record_bytes: u64) -> Box<dyn Sink> {
        let config = MemoriesqlConfig {
            url: self.url.clone(),
            credential: ENGINE_SECRET.into(),
            workspace_id: WORKSPACE.into(),
            max_record_bytes,
            timeout: Duration::from_secs(10),
            ca_file: None,
        };
        Box::new(MemoriesqlSink::new("memoriesql", config).unwrap())
    }

    /// Both scopes' records, each in memoriesQL's acceptance order, read as
    /// the owner through `read_engine_records_v1`.
    fn read(&mut self) -> Vec<Value> {
        let mut db = Client::connect(&self.url, NoTls).unwrap();
        let mut records = Vec::new();
        for scope in SCOPES {
            let mut after = 0_i64;
            loop {
                let mut tx = db.transaction().unwrap();
                tx.batch_execute(&format!(
                    "SET LOCAL ROLE memoriesql_application;
                     SELECT memoriesql.begin_authorization_context('{}', '{WORKSPACE}')",
                    sha(OWNER_SECRET)
                ))
                .unwrap();
                let request = serde_json::json!({
                    "contract_version": 1, "engine_scope": scope, "after": after, "limit": 64
                })
                .to_string();
                let page: String = tx
                    .query_one(
                        "SELECT memoriesql.read_engine_records_v1($1::text::jsonb)::text",
                        &[&request],
                    )
                    .unwrap()
                    .get(0);
                tx.commit().unwrap();
                let page: Value = serde_json::from_str(&page).unwrap();
                let batch = page["records"].as_array().unwrap().clone();
                if batch.is_empty() {
                    break;
                }
                after = page["next"].as_i64().unwrap();
                records.extend(batch);
            }
        }
        records
    }
}

core_memory_conformance::sink_conformance!(MemoriesqlHarness::new());

/// An engine paired without read access learns only that a different record
/// holds the key; memoriesQL still keeps the first one.
#[test]
fn a_writer_without_read_access_is_not_shown_the_stored_record() {
    let mut harness = MemoriesqlHarness::with_read_access(false);
    checks::a_system_of_record_keeps_the_first_writer(&mut harness);

    let first = Scenario::standard().outbox().swap_remove(0);
    let mut other: Value = serde_json::from_str(&first.record_json).unwrap();
    other["memory"]["chunk"]["text"] = Value::String("Altered again.".into());
    let mut second = first;
    second.record_json = other.to_string();
    assert_eq!(
        harness.open(u64::MAX).write(&[second]).unwrap(),
        vec![WriteOutcome::Conflict { stored: None }]
    );
}

/// A record for a scope memoriesQL has not enrolled waits, and so does every
/// record after it. The sink passes on memoriesQL's reason, naming the scope.
#[test]
fn an_unenrolled_scope_holds_back_delivery_and_is_named() {
    let mut harness = MemoriesqlHarness::new();
    let mut entries = Scenario::standard().outbox();
    let mut first: Value = serde_json::from_str(&entries[0].record_json).unwrap();
    first["scope"] = Value::String("gamma".into());
    entries[0].record_json = first.to_string();
    let outcomes = harness.open(u64::MAX).write(&entries[..2]).unwrap();
    for outcome in &outcomes {
        match outcome {
            WriteOutcome::Retryable {
                reason: Some(reason),
                ..
            } => assert!(reason.contains("gamma"), "{reason}"),
            other => panic!("{other:?}"),
        }
    }
    assert!(harness.read().is_empty());
}

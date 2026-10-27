//! The engine against the SQLite store: the same admission and delivery
//! guarantees as the in-memory store, across reopening the file.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use core_memory_contract::{AdmissionOutcome, SourceEvent};
use core_memory_core::store::OperationalStore;
use core_memory_core::store::OutboxEntry;
use core_memory_core::{
    Engine, EngineConfig, ManualClock, ScopeGrants, Sink, SinkCaps, SinkError, WriteOutcome,
    deliver_all,
};
use core_memory_store_sqlite::SqliteStore;

fn engine() -> Engine {
    Engine::new(
        EngineConfig::default(),
        vec!["collect".into()],
        Arc::new(ManualClock::at_unix_millis(0)),
    )
    .unwrap()
}

fn event(id: &str, text: &str) -> SourceEvent {
    serde_json::from_value(serde_json::json!({
        "scope": "acme",
        "source_ns": "notes",
        "external_id": id,
        "profile_id": "core.plain_text",
        "media_type": "text/plain",
        "content": {"text": text},
        "parser_contract_version": "1"
    }))
    .unwrap()
}

#[derive(Default, Debug)]
struct Collect(Vec<String>);

impl Sink for Collect {
    fn id(&self) -> &str {
        "collect"
    }
    fn caps(&self) -> SinkCaps {
        SinkCaps::accepts_all_v1(1 << 20)
    }
    fn write(&mut self, batch: &[OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError> {
        self.0.extend(batch.iter().map(|e| e.record_key.clone()));
        Ok(vec![WriteOutcome::Accepted; batch.len()])
    }
}

#[test]
fn identity_survives_reopening_the_store() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("store.sqlite");
    let engine = engine();
    {
        let mut store = SqliteStore::open(&path).unwrap();
        engine.pin_profiles(&mut store).unwrap();
        let out = engine
            .admit(&mut store, &ScopeGrants::All, &event("n1", "first"))
            .unwrap();
        assert!(matches!(out, AdmissionOutcome::Accepted { .. }));
    }
    let mut store = SqliteStore::open(&path).unwrap();
    engine.pin_profiles(&mut store).unwrap();
    let replay = engine
        .admit(&mut store, &ScopeGrants::All, &event("n1", "first"))
        .unwrap();
    assert!(matches!(replay, AdmissionOutcome::Replay { .. }));
    let conflict = engine
        .admit(&mut store, &ScopeGrants::All, &event("n1", "changed"))
        .unwrap();
    assert!(matches!(
        conflict,
        AdmissionOutcome::IdentityConflict { .. }
    ));
}

#[test]
fn records_are_delivered_once_and_pruned() {
    let mut store = SqliteStore::open_in_memory().unwrap();
    let engine = engine();
    for i in 0..3 {
        engine
            .admit(
                &mut store,
                &ScopeGrants::All,
                &event(&format!("n{i}"), "text"),
            )
            .unwrap();
    }
    let mut sink = Collect::default();
    let report = deliver_all(&mut store, &mut sink, 2).unwrap();
    assert_eq!(report.accepted, 3);
    assert_eq!(sink.0.len(), 3);
    assert_eq!(store.prune_outbox(&["collect".into()]).unwrap(), 3);
    assert_eq!(deliver_all(&mut store, &mut sink, 2).unwrap().accepted, 0);
    assert_eq!(store.outbox_backlog(&["collect".into()]).unwrap(), 0);
}

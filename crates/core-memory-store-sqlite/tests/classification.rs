//! The classification queue and attempt journal on SQLite.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use core_memory_contract::{PendingReason, Record, RecordBody, SourceEvent, TypeOutcome};
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_core::store::OperationalStore;
use core_memory_core::{Engine, EngineConfig, ManualClock, ScopeGrants};
use core_memory_store_sqlite::SqliteStore;
use serde_json::{Value, json};

const T0: i64 = 1_793_000_000_000;

fn engine(clock: &Arc<ManualClock>, max_requests: u64) -> Engine {
    let config: EngineConfig = toml::from_str(&format!(
        r#"
[vocabulary]
id = "acme.types"
types = [{{ key = "decision", rev = 1 }}]

[classifier]
binding = "test"
deadline_ms = 10000
retry_backoff_ms = 1000

[[binding]]
id = "test"
provider = "scripted"
endpoint = "local"
model = "test-model-2026-01-01"
max_output_tokens = 256
scopes = ["*"]
budget = {{ max_requests_per_day = {max_requests}, max_tokens_per_day = 10000000 }}
"#
    ))
    .unwrap();
    Engine::new(config, vec![], clock.clone()).unwrap()
}

fn chat(id: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": "acme", "source_ns": "slack", "external_id": id,
        "profile_id": "core.chat_transcript", "media_type": "text/plain",
        "content": {"text": format!("We decided item {id}.")}, "parser_contract_version": "1"
    }))
    .unwrap()
}

fn decide_all(request: &ModelRequest) -> Result<ModelResponse, TransportError> {
    let user: Value = serde_json::from_str(&request.user).unwrap();
    let items: Vec<Value> = user["chunks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| json!({"chunk": c["chunk"], "type": "decision"}))
        .collect();
    Ok(ModelResponse {
        text: json!({"items": items}).to_string(),
        usage: Usage {
            input_tokens: 10,
            output_tokens: 5,
        },
    })
}

fn records(store: &SqliteStore) -> Vec<Record> {
    store
        .outbox_after(0, usize::MAX)
        .unwrap()
        .into_iter()
        .map(|e| serde_json::from_str(&e.record_json).unwrap())
        .collect()
}

#[test]
fn held_records_leave_in_arrival_order_with_their_types() {
    let clock = Arc::new(ManualClock::at_unix_millis(T0));
    let engine = engine(&clock, 100);
    let mut store = SqliteStore::open_in_memory().unwrap();
    for id in ["a", "b", "c"] {
        engine
            .admit(&mut store, &ScopeGrants::All, &chat(id))
            .unwrap();
    }
    assert_eq!(store.outbox_head().unwrap(), 0);
    let report = engine
        .classify_due(&mut store, &FnTransport(decide_all), 10)
        .unwrap();
    assert_eq!(report.decided, 3);
    let ids: Vec<String> = records(&store)
        .iter()
        .map(|r| match &r.body {
            RecordBody::Chunk(c) => {
                assert!(matches!(
                    c.memory.type_outcome,
                    TypeOutcome::Assigned { .. }
                ));
                c.event.key.external_id.clone()
            }
            RecordBody::TypeDecision(_) => panic!("expected chunk records"),
        })
        .collect();
    assert_eq!(ids, vec!["a", "b", "c"]);
}

#[test]
fn late_decisions_follow_released_records() {
    let clock = Arc::new(ManualClock::at_unix_millis(T0));
    let engine = engine(&clock, 100);
    let mut store = SqliteStore::open_in_memory().unwrap();
    engine
        .admit(&mut store, &ScopeGrants::All, &chat("a"))
        .unwrap();
    clock.advance_millis(10_000);
    let failing = FnTransport(|_: &ModelRequest| {
        Err(TransportError::InDoubt {
            message: "timed out after sending".into(),
        })
    });
    let report = engine.classify_due(&mut store, &failing, 10).unwrap();
    assert_eq!((report.released, report.retried), (1, 1));
    clock.advance_millis(60_000);
    engine
        .classify_due(&mut store, &FnTransport(decide_all), 10)
        .unwrap();
    let recs = records(&store);
    assert_eq!(recs.len(), 2);
    match (&recs[0].body, &recs[1].body) {
        (RecordBody::Chunk(c), RecordBody::TypeDecision(d)) => {
            assert_eq!(
                c.memory.type_outcome,
                TypeOutcome::Pending {
                    reason: PendingReason::Timeout
                }
            );
            assert_eq!(d.chunk_id, c.memory.chunk.chunk_id);
        }
        other => panic!("unexpected records {other:?}"),
    }
    assert_eq!(store.classification_queue_len().unwrap(), 0);
}

#[test]
fn budgets_survive_reopening_the_store() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("store.sqlite");
    let clock = Arc::new(ManualClock::at_unix_millis(T0));
    let engine = engine(&clock, 1);
    {
        let mut store = SqliteStore::open(&path).unwrap();
        engine
            .admit(&mut store, &ScopeGrants::All, &chat("a"))
            .unwrap();
        assert_eq!(
            engine
                .classify_due(&mut store, &FnTransport(decide_all), 10)
                .unwrap()
                .decided,
            1
        );
    }
    let mut store = SqliteStore::open(&path).unwrap();
    engine
        .admit(&mut store, &ScopeGrants::All, &chat("b"))
        .unwrap();
    let report = engine
        .classify_due(&mut store, &FnTransport(decide_all), 10)
        .unwrap();
    assert!(report.budget_exhausted);
    assert_eq!(report.decided, 0);
}

#[test]
fn claimed_batches_are_leased() {
    let clock = Arc::new(ManualClock::at_unix_millis(T0));
    let engine = engine(&clock, 100);
    let mut store = SqliteStore::open_in_memory().unwrap();
    engine
        .admit(&mut store, &ScopeGrants::All, &chat("a"))
        .unwrap();
    let now = core_memory_contract::Timestamp::from_unix_millis(T0);
    let first = store
        .claim_classification(now, now.plus_millis(60_000), 10)
        .unwrap();
    assert_eq!(first.len(), 1);
    assert!(
        store
            .claim_classification(now, now.plus_millis(60_000), 10)
            .unwrap()
            .is_empty()
    );
    let later = now.plus_millis(60_000);
    assert_eq!(
        store
            .claim_classification(later, later.plus_millis(1), 10)
            .unwrap()
            .len(),
        1
    );
}

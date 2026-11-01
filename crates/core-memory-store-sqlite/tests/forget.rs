//! Forgetting chunks on SQLite: it survives reopening the store, and the
//! forgotten text is not left anywhere in the store's files.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use core_memory_contract::{
    AdmissionOutcome, ChunkId, ForgetOutcome, ForgetRequest, RefusalReason, SourceEvent, Timestamp,
};
use core_memory_core::store::{OperationalStore, QueueAction, QueueUpdate};
use core_memory_core::{Engine, EngineConfig, ManualClock, ScopeGrants};
use core_memory_store_sqlite::SqliteStore;
use serde_json::json;

const T0: i64 = 1_793_000_000_000;
const SECRET: &str = "quartz-heron-5521";

const CLASSIFIER: &str = r#"
[vocabulary]
id = "acme.types"
types = [{ key = "decision", rev = 1 }]

[classifier]
binding = "test"
deadline_ms = 10000

[[binding]]
id = "test"
provider = "scripted"
endpoint = "local"
model = "test-model-2026-01-01"
max_output_tokens = 256
scopes = ["*"]
budget = { max_requests_per_day = 100, max_tokens_per_day = 10000000 }
"#;

fn engine(config: &str) -> Engine {
    let config: EngineConfig = toml::from_str(config).unwrap();
    let clock = Arc::new(ManualClock::at_unix_millis(T0));
    Engine::new(config, vec!["out".into()], clock).unwrap()
}

fn event(id: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": "acme", "source_ns": "slack", "external_id": id,
        "profile_id": "core.chat_transcript", "media_type": "text/plain",
        "content": {"text": format!("The vault code is {SECRET}.")},
        "parser_contract_version": "1"
    }))
    .unwrap()
}

fn admit(engine: &Engine, store: &mut SqliteStore, id: &str) -> Vec<ChunkId> {
    match engine.admit(store, &ScopeGrants::All, &event(id)).unwrap() {
        AdmissionOutcome::Accepted { chunk_ids, .. } => chunk_ids,
        other => panic!("not admitted: {other:?}"),
    }
}

fn forget(engine: &Engine, store: &mut SqliteStore, chunk_ids: &[ChunkId]) -> u32 {
    let request = ForgetRequest {
        chunk_ids: chunk_ids.to_vec(),
    };
    match engine.forget(store, &ScopeGrants::All, &request).unwrap() {
        ForgetOutcome::Forgotten { forgotten, .. } => forgotten,
        other => panic!("refused: {other:?}"),
    }
}

/// Whether `needle` appears in the store file or its write-ahead log.
fn on_disk(path: &Path, needle: &str) -> bool {
    let mut wal = path.as_os_str().to_owned();
    wal.push("-wal");
    [path.to_path_buf(), wal.into()].iter().any(|p| {
        std::fs::read(p)
            .map(|bytes| bytes.windows(needle.len()).any(|w| w == needle.as_bytes()))
            .unwrap_or(false)
    })
}

#[test]
fn forgetting_survives_reopening_the_store() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("store.sqlite");
    let engine = engine(CLASSIFIER);
    let ids = {
        let mut store = SqliteStore::open(&path).unwrap();
        let ids = admit(&engine, &mut store, "e1");
        assert_eq!(forget(&engine, &mut store, &ids), 1);
        ids
    };
    let mut store = SqliteStore::open(&path).unwrap();
    assert_eq!(store.classification_queue_len().unwrap(), 0);
    assert!(store.outbox_after(0, usize::MAX).unwrap().is_empty());
    assert!(store.chunk_home(&ids[0]).unwrap().unwrap().forgotten);
    assert_eq!(forget(&engine, &mut store, &ids), 0, "already forgotten");
    assert_eq!(
        engine
            .admit(&mut store, &ScopeGrants::All, &event("e1"))
            .unwrap(),
        AdmissionOutcome::refused(RefusalReason::Tombstoned)
    );
}

#[test]
fn text_waiting_for_a_type_leaves_the_files() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("store.sqlite");
    let engine = engine(CLASSIFIER);
    let mut store = SqliteStore::open(&path).unwrap();
    let ids = admit(&engine, &mut store, "erased-event-7781");
    assert!(on_disk(&path, SECRET), "the test looks in the right place");
    forget(&engine, &mut store, &ids);
    assert!(!on_disk(&path, SECRET));
    assert!(!on_disk(&path, "erased-event-7781"), "nor its plain key");
}

#[test]
fn text_pruned_before_the_forget_left_the_files_already() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("store.sqlite");
    let engine = engine("");
    let mut store = SqliteStore::open(&path).unwrap();
    let ids = admit(&engine, &mut store, "e1");
    // Delivered to every sink, then pruned, before memoriesQL erases it.
    store.advance_sink_cursor("out", 1, &[]).unwrap();
    store.prune_outbox(&["out".into()]).unwrap();
    forget(&engine, &mut store, &ids);
    assert!(!on_disk(&path, SECRET));
}

#[test]
fn a_model_answer_arriving_after_the_forget_writes_nothing() {
    let engine = engine(CLASSIFIER);
    let mut store = SqliteStore::open_in_memory().unwrap();
    let ids = admit(&engine, &mut store, "e1");
    let now = Timestamp::from_unix_millis(T0);
    // The classifier thread has the chunk out to the model ...
    let claimed = store
        .claim_classification(now, now.plus_millis(60_000), 4)
        .unwrap();
    assert_eq!(claimed.len(), 1);
    // ... when memoriesQL erases it.
    forget(&engine, &mut store, &ids);
    let held = claimed[0].held_record.clone().unwrap();
    store
        .apply_classification(vec![QueueUpdate {
            chunk_id: ids[0],
            action: QueueAction::Complete {
                if_held: Some(held.clone()),
                if_released: held,
            },
        }])
        .unwrap();
    assert!(store.outbox_after(0, usize::MAX).unwrap().is_empty());
    assert_eq!(store.classification_queue_len().unwrap(), 0);
}

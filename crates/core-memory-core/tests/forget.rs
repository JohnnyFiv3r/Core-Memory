#![allow(clippy::unwrap_used)]

use std::collections::BTreeSet;
use std::sync::Arc;

use core_memory_contract::{
    AdmissionOutcome, ChunkId, ForgetOutcome, ForgetRefusal, ForgetRequest, RefusalReason,
    SourceEvent,
};
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_core::store::{DeliveryFailure, OperationalStore, QueueAction, QueueUpdate};
use core_memory_core::{Clock, Engine, EngineConfig, ManualClock, MemoryStore, ScopeGrants};
use serde_json::json;

const T0: i64 = 1_793_000_000_000;

const NOTES: &str = r#"
[[profile]]
id = "acme.notes"
rev = 1
source_type = "document"
content = "text"
chunker = { id = "text.structural", rev = 1, max = 40 }
"#;

const CLASSIFIER: &str = r#"
[vocabulary]
id = "acme.types"
types = [{ key = "decision", rev = 1, definition = "A choice someone made." }]

[classifier]
binding = "test"
deadline_ms = 10000

[[binding]]
id = "test"
provider = "scripted"
endpoint = "local"
model = "test-model-2026-01-01"
max_output_tokens = 512
scopes = ["acme"]
budget = { max_requests_per_day = 100, max_tokens_per_day = 10000000 }
"#;

struct World {
    clock: Arc<ManualClock>,
    engine: Engine,
    store: MemoryStore,
}

impl World {
    fn new(config: &str) -> Self {
        let clock = Arc::new(ManualClock::at_unix_millis(T0));
        let config: EngineConfig = toml::from_str(config).unwrap();
        let engine = Engine::new(config, vec!["out".into()], clock.clone()).unwrap();
        Self {
            clock,
            engine,
            store: MemoryStore::new(),
        }
    }

    fn admit(&mut self, event: &SourceEvent) -> Vec<ChunkId> {
        match self
            .engine
            .admit(&mut self.store, &ScopeGrants::All, event)
            .unwrap()
        {
            AdmissionOutcome::Accepted { chunk_ids, .. } => chunk_ids,
            other => panic!("not admitted: {other:?}"),
        }
    }

    fn forget(&mut self, chunk_ids: &[ChunkId]) -> ForgetOutcome {
        self.forget_as(&ScopeGrants::All, chunk_ids)
    }

    fn forget_as(&mut self, grants: &ScopeGrants, chunk_ids: &[ChunkId]) -> ForgetOutcome {
        let request = ForgetRequest {
            chunk_ids: chunk_ids.to_vec(),
        };
        self.engine
            .forget(&mut self.store, grants, &request)
            .unwrap()
    }

    fn outbox_keys(&self) -> Vec<String> {
        self.store
            .outbox_after(0, usize::MAX)
            .unwrap()
            .into_iter()
            .map(|e| e.record_key)
            .collect()
    }
}

fn chat(id: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": "acme",
        "source_ns": "slack/T01",
        "external_id": id,
        "profile_id": "core.chat_transcript",
        "media_type": "text/plain",
        "content": {"text": format!("We decided to ship release {id}.")},
        "parser_contract_version": "1"
    }))
    .unwrap()
}

fn note(id: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": "acme",
        "source_ns": "notes",
        "external_id": id,
        "profile_id": "acme.notes",
        "media_type": "text/plain",
        "content": {"text": "First paragraph, short.\n\nSecond paragraph, also short.\n\nThird."},
        "parser_contract_version": "1"
    }))
    .unwrap()
}

fn forgotten(outcome: ForgetOutcome) -> (u32, u32, u32, u32, u32) {
    match outcome {
        ForgetOutcome::Forgotten {
            forgotten,
            already_forgotten,
            queued_dropped,
            records_dropped,
            events_tombstoned,
            ..
        } => (
            forgotten,
            already_forgotten,
            queued_dropped,
            records_dropped,
            events_tombstoned,
        ),
        other => panic!("refused: {other:?}"),
    }
}

fn refusal(outcome: ForgetOutcome) -> ForgetRefusal {
    match outcome {
        ForgetOutcome::Refused { reason, .. } => reason,
        other => panic!("not refused: {other:?}"),
    }
}

fn decide(request: &ModelRequest) -> Result<ModelResponse, TransportError> {
    let user: serde_json::Value =
        serde_json::from_str(request.user.split("\n\n").next().unwrap()).unwrap();
    let items: Vec<_> = user["chunks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| json!({"chunk": c["chunk"], "type": "decision"}))
        .collect();
    Ok(ModelResponse {
        text: json!({"items": items}).to_string(),
        usage: Usage {
            input_tokens: 10,
            output_tokens: 10,
        },
    })
}

#[test]
fn an_undelivered_record_is_dropped_and_the_event_tombstoned() {
    let mut w = World::new("");
    let ids = w.admit(&chat("1"));
    assert_eq!(w.outbox_keys().len(), 1);
    assert_eq!(forgotten(w.forget(&ids)), (1, 0, 0, 1, 1));
    assert!(
        w.outbox_keys().is_empty(),
        "the record never reaches a sink"
    );
    let again = w
        .engine
        .admit(&mut w.store, &ScopeGrants::All, &chat("1"))
        .unwrap();
    assert_eq!(again, AdmissionOutcome::refused(RefusalReason::Tombstoned));
}

#[test]
fn a_delivered_chunk_leaves_only_its_tombstone() {
    let mut w = World::new("");
    let ids = w.admit(&chat("1"));
    w.store.advance_sink_cursor("out", 1, &[]).unwrap();
    w.store.prune_outbox(&["out".into()]).unwrap();
    assert_eq!(forgotten(w.forget(&ids)), (1, 0, 0, 0, 1));
}

#[test]
fn text_waiting_for_a_type_never_reaches_the_model() {
    let mut w = World::new(CLASSIFIER);
    let ids = w.admit(&chat("1"));
    assert!(w.outbox_keys().is_empty(), "held for its type");
    assert_eq!(forgotten(w.forget(&ids)), (1, 0, 1, 0, 1));
    assert_eq!(w.store.classification_queue_len().unwrap(), 0);
    let report = w
        .engine
        .classify_due(&mut w.store, &FnTransport(decide), 10)
        .unwrap();
    assert_eq!(report.calls, 0);
    assert!(w.outbox_keys().is_empty());
}

#[test]
fn a_classification_finishing_after_a_forget_is_ignored() {
    let mut w = World::new(CLASSIFIER);
    let ids = w.admit(&chat("1"));
    let now = w.clock.now();
    let claimed = w
        .store
        .claim_classification(now, now.plus_millis(60_000), 4)
        .unwrap();
    assert_eq!(claimed.len(), 1);
    // memoriesQL erases the chunk while its model call is in flight.
    w.forget(&ids);
    let held = claimed[0].held_record.clone().unwrap();
    w.store
        .apply_classification(vec![QueueUpdate {
            chunk_id: ids[0],
            action: QueueAction::Complete {
                if_held: Some(held.clone()),
                if_released: held,
            },
        }])
        .unwrap();
    assert!(w.outbox_keys().is_empty());
}

#[test]
fn an_event_is_tombstoned_only_when_all_its_chunks_are_forgotten() {
    let mut w = World::new(NOTES);
    let ids = w.admit(&note("n1"));
    assert!(ids.len() >= 2, "{ids:?}");
    assert_eq!(forgotten(w.forget(&ids[..1])), (1, 0, 0, 1, 0));
    assert_eq!(w.outbox_keys().len(), ids.len() - 1);
    let (n, ..) = forgotten(w.forget(&ids[1..]));
    assert_eq!(n as usize, ids.len() - 1);
    assert!(w.outbox_keys().is_empty());
    assert!(matches!(
        w.engine
            .admit(&mut w.store, &ScopeGrants::All, &note("n1"))
            .unwrap(),
        AdmissionOutcome::Refused {
            reason: RefusalReason::Tombstoned,
            ..
        }
    ));
}

#[test]
fn forgetting_again_and_unknown_chunks_change_nothing() {
    let mut w = World::new("");
    let ids = w.admit(&chat("1"));
    w.forget(&ids);
    let stranger = ChunkId::derive(
        &core_memory_contract::EventKey {
            scope: "acme".into(),
            source_ns: "elsewhere".into(),
            external_id: "x".into(),
            revision: None,
        },
        "c",
        1,
        0,
        1,
    );
    let outcome = w.forget(&[ids[0], stranger]);
    let ForgetOutcome::Forgotten {
        forgotten,
        already_forgotten,
        unknown,
        events_tombstoned,
        ..
    } = outcome
    else {
        panic!("refused");
    };
    assert_eq!((forgotten, already_forgotten, events_tombstoned), (0, 1, 0));
    assert_eq!(unknown, [stranger]);
}

#[test]
fn bad_requests_forget_nothing() {
    let mut w = World::new("");
    let ids = w.admit(&chat("1"));
    assert_eq!(refusal(w.forget(&[])), ForgetRefusal::Malformed);
    assert_eq!(
        refusal(w.forget(&[ids[0], ids[0]])),
        ForgetRefusal::Malformed
    );
    let many = vec![ids[0]; 10_001];
    assert_eq!(refusal(w.forget(&many)), ForgetRefusal::TooManyChunks);
    let beta = ScopeGrants::Only(BTreeSet::from(["beta".to_string()]));
    assert_eq!(
        refusal(w.forget_as(&beta, &ids)),
        ForgetRefusal::ScopeDenied
    );
    assert_eq!(w.outbox_keys().len(), 1, "nothing was forgotten");
    assert!(!w.store.chunk_home(&ids[0]).unwrap().unwrap().forgotten);
}

#[test]
fn failure_details_that_could_quote_a_chunk_are_cleared() {
    let mut w = World::new("");
    let ids = w.admit(&chat("1"));
    let key = w.outbox_keys()[0].clone();
    w.store
        .advance_sink_cursor(
            "out",
            1,
            &[DeliveryFailure {
                seq: 1,
                record_key: key,
                detail: "conflict: sink holds We decided to ship release 1.".into(),
            }],
        )
        .unwrap();
    w.forget(&ids);
    assert_eq!(w.store.delivery_failures()[0].1.detail, "forgotten");
}

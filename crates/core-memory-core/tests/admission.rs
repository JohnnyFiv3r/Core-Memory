#![allow(clippy::unwrap_used)]

use std::collections::BTreeSet;
use std::sync::Arc;

use core_memory_contract::{
    AdmissionOutcome, PendingReason, Record, RecordBody, RefusalReason, SourceEvent, TypeOutcome,
};
use core_memory_core::store::OperationalStore;
use core_memory_core::{Engine, EngineConfig, ManualClock, MemoryStore, ScopeGrants};

const CONFIG: &str = r#"
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

[[profile.rules]]
id = "closed"
assign = "outcome"
when = { "record.object_type" = "Opportunity", "record.action" = ["closed_won", "closed_lost"] }

[[profile]]
id = "acme.notes"
rev = 1
source_type = "document"
content = "text"
max_event_bytes = 200
chunker = { id = "text.structural", rev = 1, max = 40 }

[limits]
max_outbox_backlog = 6
"#;

fn engine() -> Engine {
    let config: EngineConfig = toml::from_str(CONFIG).unwrap();
    Engine::new(
        config,
        vec!["primary".into()],
        Arc::new(ManualClock::at_unix_millis(1_793_000_000_000)),
    )
    .unwrap()
}

fn chat(external_id: &str, text: &str) -> SourceEvent {
    serde_json::from_value(serde_json::json!({
        "scope": "acme",
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

fn crm(external_id: &str, action: &str, amount: u64) -> SourceEvent {
    serde_json::from_value(serde_json::json!({
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

fn all() -> ScopeGrants {
    ScopeGrants::All
}

fn records(store: &MemoryStore) -> Vec<Record> {
    store
        .outbox_after(0, usize::MAX)
        .unwrap()
        .into_iter()
        .map(|e| serde_json::from_str(&e.record_json).unwrap())
        .collect()
}

fn chunk(record: &Record) -> &core_memory_contract::ChunkRecord {
    match &record.body {
        RecordBody::Chunk(c) => c,
        RecordBody::TypeDecision(_) => panic!("expected a chunk record"),
    }
}

#[test]
fn accepts_then_replays_without_new_records() {
    let engine = engine();
    let mut store = MemoryStore::new();
    let event = chat("C1/2", "We decided to ship on Friday.");
    let first = engine.admit(&mut store, &all(), &event).unwrap();
    let AdmissionOutcome::Accepted { chunk_ids, .. } = &first else {
        panic!("{first:?}");
    };
    assert_eq!(chunk_ids.len(), 1);
    assert_eq!(store.outbox_head().unwrap(), 1);

    let again = engine.admit(&mut store, &all(), &event).unwrap();
    assert!(
        matches!(again, AdmissionOutcome::Replay { ref chunk_ids, .. } if chunk_ids.len() == 1)
    );
    assert_eq!(store.outbox_head().unwrap(), 1, "a replay writes nothing");
}

#[test]
fn chunk_record_carries_exact_text_and_pending_type() {
    let engine = engine();
    let mut store = MemoryStore::new();
    let text = "  Exact text, kept as sent.\r\n";
    engine
        .admit(&mut store, &all(), &chat("C1/3", text))
        .unwrap();
    let recs = records(&store);
    let c = chunk(&recs[0]);
    assert_eq!(c.memory.chunk.text, text);
    assert_eq!(
        c.memory.chunk.content_hash,
        core_memory_contract::Sha256Digest::of(text.as_bytes())
    );
    assert_eq!(
        c.memory.type_outcome,
        TypeOutcome::Pending {
            reason: PendingReason::NoClassifier
        }
    );
    assert!(c.decision.is_none());
    assert_eq!(c.event.source_system, "slack");
    assert_eq!(c.event.chunk_count, 1);
    assert_eq!(
        recs[0].record_key,
        format!("chunk:{}", c.memory.chunk.chunk_id)
    );
}

#[test]
fn changed_content_under_the_same_key_is_a_conflict() {
    let engine = engine();
    let mut store = MemoryStore::new();
    engine
        .admit(&mut store, &all(), &chat("C1/4", "first"))
        .unwrap();
    let out = engine
        .admit(&mut store, &all(), &chat("C1/4", "second"))
        .unwrap();
    assert!(
        matches!(out, AdmissionOutcome::IdentityConflict { .. }),
        "{out:?}"
    );
}

#[test]
fn metadata_changes_are_covered_by_the_identity_hash() {
    let engine = engine();
    let mut store = MemoryStore::new();
    let event = chat("C1/5", "same text");
    engine.admit(&mut store, &all(), &event).unwrap();
    let mut moved = event.clone();
    moved.structure.thread = Some("C9/9".into());
    let out = engine.admit(&mut store, &all(), &moved).unwrap();
    assert!(
        matches!(out, AdmissionOutcome::IdentityConflict { .. }),
        "{out:?}"
    );
}

#[test]
fn editable_sources_get_a_content_derived_revision() {
    let engine = engine();
    let mut store = MemoryStore::new();
    engine
        .admit(
            &mut store,
            &all(),
            &crm("006AAAAAAAAAAAA", "prospecting", 10),
        )
        .unwrap();
    let edited = crm("006AAAAAAAAAAAA", "closed_won", 10);
    let out = engine.admit(&mut store, &all(), &edited).unwrap();
    let AdmissionOutcome::Accepted {
        assigned_revision: Some(rev),
        ..
    } = &out
    else {
        panic!("{out:?}");
    };
    assert!(rev.starts_with("cm-"));
    // Offering the same edit again is a replay of the assigned revision.
    let again = engine.admit(&mut store, &all(), &edited).unwrap();
    assert!(
        matches!(again, AdmissionOutcome::Replay { .. }),
        "{again:?}"
    );
    let recs = records(&store);
    assert!(chunk(&recs[1]).event.engine_assigned_revision);
    assert_eq!(
        chunk(&recs[1]).event.key.revision.as_deref(),
        Some(rev.as_str())
    );
}

#[test]
fn rules_assign_types_with_provenance() {
    let engine = engine();
    let mut store = MemoryStore::new();
    engine
        .admit(&mut store, &all(), &crm("o-1", "closed_lost", 5))
        .unwrap();
    engine
        .admit(&mut store, &all(), &crm("o-2", "prospecting", 5))
        .unwrap();
    let recs = records(&store);
    let closed = chunk(&recs[0]);
    match &closed.memory.type_outcome {
        TypeOutcome::Assigned { type_ref } => {
            assert_eq!((type_ref.key.as_str(), type_ref.rev), ("outcome", 2))
        }
        other => panic!("{other:?}"),
    }
    let decision = closed.decision.as_ref().unwrap();
    assert_eq!(
        serde_json::to_value(decision).unwrap()["rule_id"],
        serde_json::json!("closed")
    );
    assert_eq!(
        closed.catalogs.type_catalog.as_ref().unwrap().id,
        "acme.types"
    );
    assert!(matches!(
        chunk(&recs[1]).memory.type_outcome,
        TypeOutcome::Pending { .. }
    ));
    // JSON content is chunked in canonical form, and identifiers are found.
    assert_eq!(
        closed.memory.chunk.text,
        r#"{"Amount":5,"Id":"006AAAAAAAAAAAA","StageName":"closed_lost"}"#
    );
    assert_eq!(closed.facets.identifiers[0].value, "006AAAAAAAAAAAA");
}

#[test]
fn tombstoned_keys_are_refused() {
    let engine = engine();
    let mut store = MemoryStore::new();
    let event = chat("C1/6", "to be erased");
    store.tombstone(&event.key());
    let out = engine.admit(&mut store, &all(), &event).unwrap();
    assert_eq!(out, AdmissionOutcome::refused(RefusalReason::Tombstoned));
}

#[test]
fn scope_grants_are_checked_first() {
    let engine = engine();
    let mut store = MemoryStore::new();
    let grants = ScopeGrants::Only(BTreeSet::from(["other".to_string()]));
    let out = engine
        .admit(&mut store, &grants, &chat("C1/7", "hi"))
        .unwrap();
    assert_eq!(out, AdmissionOutcome::refused(RefusalReason::ScopeDenied));
}

#[test]
fn profile_and_content_problems_are_typed() {
    let engine = engine();
    let mut store = MemoryStore::new();

    let mut unknown = chat("C1/8", "hi");
    unknown.profile_id = "nope".into();
    assert!(matches!(
        engine.admit(&mut store, &all(), &unknown).unwrap(),
        AdmissionOutcome::Refused {
            reason: RefusalReason::InvalidProfile,
            ..
        }
    ));

    let mut wrong_kind = crm("o-3", "x", 1);
    wrong_kind.profile_id = "core.chat_transcript".into();
    assert!(matches!(
        engine.admit(&mut store, &all(), &wrong_kind).unwrap(),
        AdmissionOutcome::Refused {
            reason: RefusalReason::Malformed,
            ..
        }
    ));

    let blank = chat("C1/9", " \n ");
    assert!(matches!(
        engine.admit(&mut store, &all(), &blank).unwrap(),
        AdmissionOutcome::Refused {
            reason: RefusalReason::Malformed,
            ..
        }
    ));

    let dup: SourceEvent = serde_json::from_str(
        r#"{"scope":"acme","source_ns":"crm","external_id":"d","profile_id":"core.json_record",
            "media_type":"application/json","parser_contract_version":"1",
            "content":{"json":{"a":1,"a":2}}}"#,
    )
    .unwrap();
    let out = engine.admit(&mut store, &all(), &dup).unwrap();
    assert!(
        matches!(&out, AdmissionOutcome::Refused { reason: RefusalReason::Malformed, problems } if problems[0].problem.contains("duplicate key")),
        "{out:?}"
    );
    assert_eq!(store.outbox_head().unwrap(), 0);
}

#[test]
fn oversize_events_are_refused() {
    let engine = engine();
    let mut store = MemoryStore::new();
    let mut event = chat("N1", &"word ".repeat(50));
    event.profile_id = "acme.notes".into();
    let out = engine.admit(&mut store, &all(), &event).unwrap();
    assert!(
        matches!(
            out,
            AdmissionOutcome::Refused {
                reason: RefusalReason::Oversize,
                ..
            }
        ),
        "{out:?}"
    );
}

#[test]
fn multi_chunk_events_partition_the_content() {
    let engine = engine();
    let mut store = MemoryStore::new();
    let text = "Kickoff notes for the billing migration.\n\nRisks: the ledger export is slow.\n\nNext: ship Friday.";
    let mut event = chat("N2", text);
    event.profile_id = "acme.notes".into();
    let out = engine.admit(&mut store, &all(), &event).unwrap();
    let AdmissionOutcome::Accepted { chunk_ids, .. } = out else {
        panic!("{out:?}");
    };
    assert_eq!(chunk_ids.len(), 3);
    let recs = records(&store);
    let mut rebuilt = String::new();
    let mut char_pos = 0;
    for (i, r) in recs.iter().enumerate() {
        let c = chunk(r);
        assert_eq!(c.memory.chunk.ordinal as usize, i);
        assert_eq!(c.memory.chunk.char_start, char_pos);
        char_pos = c.memory.chunk.char_end;
        assert_eq!(c.event.chunk_count, 3);
        rebuilt.push_str(&c.memory.chunk.text);
    }
    assert_eq!(rebuilt, text);
}

#[test]
fn backlog_applies_backpressure_but_replays_still_answer() {
    let engine = engine();
    let mut store = MemoryStore::new();
    for i in 0..6 {
        let out = engine
            .admit(&mut store, &all(), &chat(&format!("B{i}"), "x"))
            .unwrap();
        assert!(matches!(out, AdmissionOutcome::Accepted { .. }));
    }
    let busy = engine.admit(&mut store, &all(), &chat("B6", "x")).unwrap();
    assert!(matches!(busy, AdmissionOutcome::Busy { .. }), "{busy:?}");
    let replay = engine.admit(&mut store, &all(), &chat("B0", "x")).unwrap();
    assert!(matches!(replay, AdmissionOutcome::Replay { .. }));

    store.advance_sink_cursor("primary", 6, &[]).unwrap();
    let after = engine.admit(&mut store, &all(), &chat("B6", "x")).unwrap();
    assert!(matches!(after, AdmissionOutcome::Accepted { .. }));
}

#[test]
fn changed_profile_contents_need_a_new_revision() {
    let mut store = MemoryStore::new();
    engine().pin_profiles(&mut store).unwrap();
    engine().pin_profiles(&mut store).unwrap();

    let changed = CONFIG.replace("max = 40 }", "max = 80 }");
    let config: EngineConfig = toml::from_str(&changed).unwrap();
    let engine = Engine::new(config, vec![], Arc::new(ManualClock::at_unix_millis(0))).unwrap();
    let err = engine.pin_profiles(&mut store).unwrap_err();
    assert!(err.to_string().contains("acme.notes"), "{err}");
}

#[test]
fn admission_is_deterministic() {
    let run = || {
        let engine = engine();
        let mut store = MemoryStore::new();
        engine
            .admit(&mut store, &all(), &chat("D1", "Same input, same output."))
            .unwrap();
        engine
            .admit(&mut store, &all(), &crm("o-9", "closed_won", 7))
            .unwrap();
        store
            .outbox_after(0, usize::MAX)
            .unwrap()
            .into_iter()
            .map(|e| e.record_json)
            .collect::<Vec<_>>()
    };
    assert_eq!(run(), run());
}

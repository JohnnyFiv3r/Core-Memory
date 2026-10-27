//! The checks. Each one drives the engine through a sink from a
//! [`Harness`] and panics with a message naming what the sink got wrong.

use std::collections::{BTreeMap, BTreeSet};

use core_memory_contract::AdmissionOutcome;
use core_memory_core::sink::WriteOutcome;
use serde_json::Value;

use crate::{BATCH, Harness, NO_LIMIT, Scenario, fixture};

fn key(r: &Value) -> &str {
    r["record_key"]
        .as_str()
        .expect("every record carries a record_key")
}

fn scopes(records: &[Value]) -> BTreeSet<String> {
    records
        .iter()
        .filter_map(|r| r["scope"].as_str().map(str::to_string))
        .collect()
}

/// Asserts that a sink holding `held` holds exactly `sent` as a consumer
/// sees it: every record present, every copy identical to what was sent,
/// and the records of each scope in the order they were sent.
pub fn assert_holds(held: &[Value], sent: &[Value]) {
    let by_key: BTreeMap<&str, &Value> = sent.iter().map(|r| (key(r), r)).collect();
    let mut seen = BTreeSet::new();
    let mut first = Vec::new();
    for r in held {
        let k = key(r);
        let Some(expected) = by_key.get(k) else {
            panic!("the sink holds {k}, which the engine never sent");
        };
        assert_eq!(r, *expected, "the sink altered {k}");
        if seen.insert(k) {
            first.push(r);
        }
    }
    let missing: Vec<&&str> = by_key.keys().filter(|k| !seen.contains(**k)).collect();
    assert!(missing.is_empty(), "the sink lost {missing:?}");
    for scope in scopes(sent) {
        let in_scope = |records: &mut dyn Iterator<Item = &Value>| -> Vec<String> {
            records
                .filter(|r| r["scope"] == scope.as_str())
                .map(|r| key(r).to_string())
                .collect()
        };
        assert_eq!(
            in_scope(&mut first.iter().copied()),
            in_scope(&mut sent.iter()),
            "the sink reordered the records of scope {scope}"
        );
    }
}

/// The standard run, delivered. The sink must hold every record once as
/// a consumer sees it, unchanged and in order within each scope.
pub fn delivers_every_record_once_in_order(h: &mut dyn Harness) {
    let mut s = Scenario::standard();
    let sent = s.records();
    let mut sink = h.open(NO_LIMIT);
    let report = s.deliver(sink.as_mut());
    drop(sink);
    assert!(report.idle && report.failed == 0, "{report:?}");
    assert_eq!(
        report.accepted + report.already_accepted,
        sent.len() as u64,
        "{report:?}"
    );
    assert_holds(&h.read(), &sent);
}

/// The chunks of each event, in ordinal order, must reproduce its text
/// byte for byte, and each chunk's byte range must select its text.
pub fn keeps_text_exact(h: &mut dyn Harness) {
    let mut s = Scenario::standard();
    let mut sink = h.open(NO_LIMIT);
    s.deliver(sink.as_mut());
    drop(sink);

    let mut chunks: BTreeMap<(String, String), BTreeMap<u64, &Value>> = BTreeMap::new();
    let held = h.read();
    for r in held.iter().filter(|r| r["record_kind"] == "chunk") {
        let event = (
            r["event"]["key"]["scope"]
                .as_str()
                .unwrap_or_default()
                .to_string(),
            r["event"]["key"]["external_id"]
                .as_str()
                .unwrap_or_default()
                .to_string(),
        );
        let ordinal = r["memory"]["chunk"]["ordinal"].as_u64().unwrap_or(u64::MAX);
        chunks.entry(event).or_default().insert(ordinal, r);
    }
    for event in fixture::events() {
        let id = (event.scope.clone(), event.external_id.clone());
        let parts = chunks
            .get(&id)
            .unwrap_or_else(|| panic!("the sink holds no chunk of {id:?}"));
        let texts: Vec<&str> = parts
            .values()
            .map(|r| r["memory"]["chunk"]["text"].as_str().unwrap_or_default())
            .collect();
        match fixture::source_text(&event) {
            Value::String(source) => {
                assert_eq!(
                    texts.concat(),
                    source,
                    "the chunks of {id:?} do not reproduce it"
                );
                for r in parts.values() {
                    let chunk = &r["memory"]["chunk"];
                    let range = chunk["byte_start"].as_u64().unwrap_or_default() as usize
                        ..chunk["byte_end"].as_u64().unwrap_or_default() as usize;
                    assert_eq!(
                        source.get(range.clone()),
                        chunk["text"].as_str(),
                        "{id:?}: byte range {range:?} does not select its chunk"
                    );
                }
            }
            json => {
                assert_eq!(texts.len(), 1, "{id:?} is one JSON chunk");
                let parsed: Value = serde_json::from_str(texts[0])
                    .unwrap_or_else(|e| panic!("{id:?}: the chunk is not JSON: {e}"));
                assert_eq!(parsed, json, "{id:?}: the chunk is not the source document");
            }
        }
    }
}

/// After the standard run, replaying every event, an identity conflict,
/// and events refused for scope, profile and size must deliver nothing.
pub fn replays_and_refusals_add_nothing(h: &mut dyn Harness) {
    let mut s = Scenario::standard();
    let mut sink = h.open(NO_LIMIT);
    s.deliver(sink.as_mut());
    drop(sink);
    let before = h.read();

    for event in fixture::events_that_add_nothing() {
        let outcome = s.admit(&event);
        assert!(
            !matches!(outcome, AdmissionOutcome::Accepted { .. }),
            "{}/{} was accepted: {outcome:?}",
            event.scope,
            event.external_id
        );
    }
    s.classify(&fixture::keywords());
    let mut sink = h.open(NO_LIMIT);
    let report = s.deliver(sink.as_mut());
    drop(sink);
    assert_eq!(report.accepted + report.already_accepted, 0, "{report:?}");
    assert_eq!(h.read(), before, "the sink changed without new records");
}

/// A chunk released as pending while its provider is down, then decided:
/// the sink must hold the pending chunk record, then the type decision
/// record for that chunk.
pub fn a_late_type_follows_its_pending_chunk(h: &mut dyn Harness) {
    let mut sink = h.open(NO_LIMIT);
    let s = Scenario::late_type(|s| {
        s.deliver(sink.as_mut());
    });
    drop(sink);

    let sent = s.records();
    assert_eq!(sent.len(), 2, "the scenario makes a chunk and its decision");
    assert_eq!(sent[0]["memory"]["type"]["status"], "pending");
    assert_eq!(sent[1]["record_kind"], "type_decision");
    assert_eq!(sent[1]["chunk_id"], sent[0]["memory"]["chunk"]["chunk_id"]);
    assert_eq!(sent[1]["outcome"]["status"], "assigned");
    assert_holds(&h.read(), &sent);
}

/// Every batch of the standard run written twice to fresh sink instances,
/// as when the engine crashes after a write and before its cursor moves.
/// Each write must accept or recognize every record, a system of record
/// must recognize them the second time, and a consumer must see each
/// record once.
pub fn redelivery_after_a_crash_is_harmless(h: &mut dyn Harness) {
    let s = Scenario::standard();
    let entries = s.outbox();
    for round in 0..2 {
        let mut sink = h.open(NO_LIMIT);
        let system_of_record = sink.caps().system_of_record;
        for batch in entries.chunks(BATCH) {
            let outcomes = sink
                .write(batch)
                .unwrap_or_else(|e| panic!("round {round}: {e}"));
            assert_eq!(outcomes.len(), batch.len(), "one outcome per record");
            for (entry, outcome) in batch.iter().zip(outcomes) {
                let ok = match outcome {
                    WriteOutcome::AlreadyAccepted => true,
                    WriteOutcome::Accepted => round == 0 || !system_of_record,
                    _ => false,
                };
                assert!(ok, "round {round}: {}: {outcome:?}", entry.record_key);
            }
        }
    }
    assert_holds(&h.read(), &s.records());
}

/// A record larger than the sink's limit is withheld whole and reported,
/// never cut, and every other record is delivered.
pub fn oversized_records_are_withheld_whole(h: &mut dyn Harness) {
    let mut s = Scenario::standard();
    let large = fixture::large_event();
    s.admit(&large);
    s.classify(&fixture::keywords());

    let entries = s.outbox();
    let (big, rest): (Vec<_>, Vec<_>) = entries
        .iter()
        .partition(|e| e.record_json.contains(&large.external_id));
    assert_eq!(big.len(), 1, "the large event is one record");
    let largest_rest = rest.iter().map(|e| e.record_json.len()).max().unwrap_or(0);
    let limit = (largest_rest + big[0].record_json.len()) / 2;
    assert!(largest_rest < limit && limit < big[0].record_json.len());

    let mut sink = h.open(limit as u64);
    assert_eq!(
        sink.caps().max_record_bytes,
        limit as u64,
        "the sink must report the limit it was opened with"
    );
    let report = s.deliver(sink.as_mut());
    drop(sink);
    assert_eq!(report.failed, 1, "{report:?}");
    let sent: Vec<Value> = s
        .records()
        .into_iter()
        .filter(|r| key(r) != big[0].record_key)
        .collect();
    assert_holds(&h.read(), &sent);
}

/// Every record keeps the scope of the event it came from, and records
/// of both fixture scopes arrive.
pub fn records_keep_their_scope(h: &mut dyn Harness) {
    let mut s = Scenario::standard();
    let mut sink = h.open(NO_LIMIT);
    s.deliver(sink.as_mut());
    drop(sink);
    let held = h.read();

    let mut chunk_scope = BTreeMap::new();
    for r in held.iter().filter(|r| r["record_kind"] == "chunk") {
        assert_eq!(r["scope"], r["event"]["key"]["scope"], "{}", key(r));
        chunk_scope.insert(r["memory"]["chunk"]["chunk_id"].to_string(), &r["scope"]);
    }
    for r in held.iter().filter(|r| r["record_kind"] == "type_decision") {
        assert_eq!(
            Some(&&r["scope"]),
            chunk_scope.get(&r["chunk_id"].to_string()),
            "{}",
            key(r)
        );
    }
    assert_eq!(
        scopes(&held),
        BTreeSet::from(["acme".to_string(), "beta".to_string()])
    );
}

/// A system of record keeps the first record written under a key: a
/// different record under the same key gets `Conflict` with the stored
/// record, and the sink still holds the first. Other sinks skip this.
pub fn a_system_of_record_keeps_the_first_writer(h: &mut dyn Harness) {
    let s = Scenario::standard();
    let mut sink = h.open(NO_LIMIT);
    if !sink.caps().system_of_record {
        return;
    }
    let first = s.outbox().swap_remove(0);
    let first_value: Value = serde_json::from_str(&first.record_json).expect("records are JSON");
    let mut altered = first_value.clone();
    altered["memory"]["chunk"]["text"] = Value::String("Altered.".into());
    let mut second = first.clone();
    second.record_json = altered.to_string();

    let accepted = sink
        .write(std::slice::from_ref(&first))
        .expect("first write");
    assert_eq!(accepted, vec![WriteOutcome::Accepted]);
    let outcomes = sink.write(&[second]).expect("second write");
    match outcomes.as_slice() {
        // A sink may withhold the stored record from a writer that cannot
        // read it. What it holds is checked below either way.
        [WriteOutcome::Conflict { stored }] => {
            if let Some(stored) = stored {
                let stored: Value = serde_json::from_str(stored)
                    .unwrap_or_else(|e| panic!("Conflict must carry the stored record: {e}"));
                assert_eq!(stored, first_value, "Conflict carried another record");
            }
        }
        other => panic!("a second record under one key got {other:?}"),
    }
    drop(sink);
    assert_holds(&h.read(), &[first_value]);
}

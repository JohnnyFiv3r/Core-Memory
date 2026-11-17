//! The records exported for a fixed set of events, compared with a
//! checked-in file.
//!
//! A difference here is a change to what consumers read: an ID, a hash, a
//! chunk boundary, a facet, the prompt a model is given or the JSON shape.
//! If the change is intended, bump the revision it belongs to (chunker,
//! facet, prompt, record contract) and regenerate the file:
//!
//! ```bash
//! CORE_MEMORY_UPDATE_GOLDEN=1 cargo test -p core-memory-store-postgres \
//!     --features postgres-tests --test golden
//! ```

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use common::{CONFIG, World, crm, note};
use core_memory_contract::{Record, RecordBody, SourceEvent, Timestamp};
use core_memory_core::ReadRound;
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_core::store::Change;
use serde_json::{Value, json};

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden/export.jsonl");

/// The golden world's reader. Its binding is part of what the file pins.
const READER: &str = r#"
[reader]
binding = "golden"
batch_size = 8

[[binding]]
id = "golden"
provider = "stand-in"
endpoint = "https://models.example.com/v1"
model = "stand-in-2026-10-01"
max_output_tokens = 1024
"#;

fn event(value: serde_json::Value) -> SourceEvent {
    serde_json::from_value(value).unwrap()
}

/// A stand-in model with fixed answers: a type the request allows for
/// most chunks, quotes for the chunk about OPS-412, and an abstention for
/// the chunk that says too little.
fn stand_in(request: &ModelRequest) -> Result<ModelResponse, TransportError> {
    let shown: Value = serde_json::from_str(request.user.lines().next().unwrap()).unwrap();
    let allowed = &shown["types"];
    let items: Vec<Value> = shown["chunks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|chunk| {
            let text = chunk["text"].as_str().unwrap();
            if text.contains("I'll take OPS-412") {
                json!({
                    "chunk": chunk["chunk"],
                    "type": "decision",
                    "entities": [{"text": "OPS-412", "kind": "artifact"}],
                    "actions": [{"text": "I'll take OPS-412"}],
                    "outcomes": [],
                })
            } else if text.starts_with("Owner:") {
                json!({"chunk": chunk["chunk"], "abstain": "insufficient_evidence"})
            } else {
                json!({"chunk": chunk["chunk"], "type": allowed[0]["key"]})
            }
        })
        .collect();
    Ok(ModelResponse {
        text: json!({"items": items}).to_string(),
        usage: Usage {
            input_tokens: 900,
            output_tokens: 120,
        },
    })
}

#[test]
fn exported_records_match_the_golden_file() {
    // The workspace's ID key and the clock are fixed, so every ID, hash and
    // time below is the same on every run, but for the two that the
    // database gives: a model call's ID and the time of an erasure.
    let mut w = World::with_config(&format!("{CONFIG}{READER}"));

    // A message and a reply, with a link and identifiers for the facets.
    w.accept(&event(json!({
        "scope": "acme",
        "source_ns": "slack/T01",
        "external_id": "C042/1730000001.000100",
        "profile_id": "core.chat_transcript",
        "occurred_at": {"at": "2026-10-20T15:02:11Z", "precision": "second"},
        "actor": {"id": "U17", "kind": "human", "role": "user"},
        "media_type": "text/plain",
        "content": {"text": "Billing migration: we're going with the phased cutover. Big bang is too risky with the ledger export as slow as it is."},
        "structure": {"thread": "C042/1730000001.000100"},
        "parser_contract_version": "slack-export/1"
    })));
    w.clock.advance_millis(1_500);
    w.accept(&event(json!({
        "scope": "acme",
        "source_ns": "slack/T01",
        "external_id": "C042/1730000002.000200",
        "profile_id": "core.chat_transcript",
        "occurred_at": {"at": "2026-10-20T15:04:40.250Z", "precision": "instant"},
        "actor": {"id": "U22", "kind": "human", "role": "user"},
        "media_type": "text/plain",
        "content": {"text": "Agreed. I'll take OPS-412 and get the export under an hour before phase one.\n\nTracking doc: https://wiki.example.com/billing/cutover. Ça marche pour l'équipe 🎉"},
        "structure": {
            "thread": "C042/1730000001.000100",
            "reply_to": {"external_id": "C042/1730000001.000100"}
        },
        "attributes": {"channel": "billing"},
        "parser_contract_version": "slack-export/1"
    })));

    // A record that is edited in place: the second version becomes a
    // revision the engine names from its content.
    w.accept(&crm("006Ak000002XyZaAAB", "negotiation", 48_000));
    w.clock.advance_millis(60_000);
    w.accept(&crm("006Ak000002XyZaAAB", "closed_won", 48_000));

    // A note the chunker cuts into several chunks, dated only to the day.
    let mut dated = note(
        "cutover-plan",
        "Phase one moves read traffic on November 3.\n\nPhase two moves writes once the export runs in under an hour.\n\nOwner: OPS-412.",
    );
    dated.occurred_at =
        serde_json::from_value(json!({"at": "2026-10-21T00:00:00Z", "precision": "day"})).unwrap();
    w.accept(&dated);

    // A note with no time of its own, ordered by when it was captured.
    w.accept(&note("undated", "No date on this one."));

    // A note that is erased after it was read. Its event, its chunk and
    // what was read in it leave the export, and an erasure record enters.
    w.clock.advance_millis(1_000);
    let (erased, _) = w.accept(&note("mistake", "Admitted by mistake."));

    // A model reads every chunk, a batch for each source profile.
    w.store
        .grant_destination("acme", "models.example.com")
        .unwrap();
    w.clock.advance_millis(5_000);
    let model = FnTransport(stand_in);
    while let ReadRound::Handled(batch) = w.engine.read_once(&mut w.store, &model).unwrap() {
        assert_eq!((batch.problem, batch.stored), (None, batch.chunks));
    }
    w.store
        .erase_event(&erased, "user_request", "owner")
        .unwrap();

    let mut lines = Vec::new();
    for change in w.changes() {
        let records = match change {
            Change::Event(exported) => {
                let exported = *exported;
                let scope = exported.event.key.scope.clone();
                let mut records = vec![Record::event(exported.event)];
                records.extend(
                    exported
                        .chunks
                        .into_iter()
                        .map(|chunk| Record::chunk(scope.clone(), chunk)),
                );
                records
            }
            Change::Read { scope, read } => vec![Record::chunk_read(scope, *read)],
            Change::Erasure { scope, erasure } => vec![Record::erasure(scope, erasure)],
        };
        for mut record in records {
            // The two values the database gives are not pinned.
            match &mut record.body {
                RecordBody::ChunkRead(read) => {
                    read.call_id = "00000000-0000-0000-0000-000000000000".parse().unwrap();
                }
                RecordBody::Erasure(erasure) => {
                    erasure.erased_at = Timestamp::parse("2026-01-01T00:00:00Z").unwrap();
                }
                RecordBody::Event(_) | RecordBody::Chunk(_) => {}
            }
            lines.push(serde_json::to_string(&record).unwrap());
        }
    }
    // Every line reads back as the record it was written from.
    for line in &lines {
        let record: Record = serde_json::from_str(line).unwrap();
        assert_eq!(&serde_json::to_string(&record).unwrap(), line);
    }

    if std::env::var_os("CORE_MEMORY_UPDATE_GOLDEN").is_some() {
        std::fs::write(GOLDEN, lines.join("\n") + "\n").unwrap();
    }
    let golden = std::fs::read_to_string(GOLDEN).unwrap();
    let expected: Vec<&str> = golden.lines().collect();
    for (i, (got, want)) in lines.iter().zip(&expected).enumerate() {
        assert_eq!(got, want, "record {} differs from {GOLDEN}", i + 1);
    }
    assert_eq!(lines.len(), expected.len(), "the number of records changed");
}

//! The records exported for a fixed set of events, compared with a
//! checked-in file.
//!
//! A difference here is a change to what consumers read: an ID, a hash, a
//! chunk boundary, a facet or the JSON shape. If the change is intended,
//! bump the revision it belongs to (chunker, facet, record contract) and
//! regenerate the file:
//!
//! ```bash
//! CORE_MEMORY_UPDATE_GOLDEN=1 cargo test -p core-memory-store-postgres \
//!     --features postgres-tests --test golden
//! ```

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use common::{World, crm, note};
use core_memory_contract::{Record, SourceEvent};
use serde_json::json;

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden/export.jsonl");

fn event(value: serde_json::Value) -> SourceEvent {
    serde_json::from_value(value).unwrap()
}

#[test]
fn exported_records_match_the_golden_file() {
    // The workspace's ID key and the clock are fixed, so every ID, hash and
    // time below is the same on every run.
    let mut w = World::new();

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

    let mut lines = Vec::new();
    for exported in w.export() {
        let scope = exported.event.key.scope.clone();
        lines.push(serde_json::to_string(&Record::event(exported.event)).unwrap());
        for chunk in exported.chunks {
            lines.push(serde_json::to_string(&Record::chunk(scope.clone(), chunk)).unwrap());
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

//! The records exported for a fixed set of events, compared with a
//! checked-in file.
//!
//! A difference here is a change to what consumers read: an ID, a hash, a
//! chunk boundary, a facet, the prompt a model is given, what is computed
//! about a pair of beads or the JSON shape. If the change is intended, bump
//! the revision it belongs to (chunker, facet, prompt, rubric, pair
//! columns, record contract) and regenerate the file:
//!
//! ```bash
//! CORE_MEMORY_UPDATE_GOLDEN=1 cargo test -p core-memory-store-postgres \
//!     --features postgres-tests --test golden
//! ```

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use common::{CONFIG, World, crm, note};
use core_memory_contract::{CallId, Record, RecordBody, SourceEvent, Timestamp};
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_core::store::Change;
use core_memory_core::{JudgeRound, ReadRound};
use serde_json::{Value, json};

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden/export.jsonl");

/// The golden world's readers and its judge. Their binding is part of what
/// the file pins. The judge compares beads that lie within a day of each
/// other, which here is two sets of them.
const READER: &str = r#"
[reader]
binding = "golden"
batch_size = 8

[tool_reader]
binding = "golden"

[judge]
binding = "golden"
debounce_ms = 0
window_ms = 86400000

[[binding]]
id = "golden"
provider = "stand-in"
endpoint = "https://models.example.com/v1"
model = "stand-in-2026-10-01"
max_output_tokens = 1024
"#;

/// A call's ID is given by the database, so it is not pinned.
fn no_call() -> CallId {
    "00000000-0000-0000-0000-000000000000".parse().unwrap()
}

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

/// A stand-in judge with fixed answers. It goes by what two memories say,
/// whichever of them is the new one:
///
/// - the pieces of one message are parts of one memory;
/// - the record that says the deal closed takes the place of the one that
///   says it is being negotiated;
/// - the decision led to the reply that takes the work, and that reply to
///   the run of the tests;
/// - the run of the tests describes the export the decision is about;
/// - the plan's second phase and the decision call each other up;
/// - whether the plan's owner line and the reply are related it cannot tell;
/// - "the export" in the reply is the decision's "ledger export".
fn judge(request: &ModelRequest) -> Result<ModelResponse, TransportError> {
    let shown: Value = serde_json::from_str(
        request
            .user
            .split("\n\nYour previous reply")
            .next()
            .unwrap(),
    )
    .unwrap();
    let listed = shown["listed"].as_array().unwrap();
    let new = shown["new"].as_array().unwrap();
    // A tool call shows no text. What a model wrote of it stands in.
    let said = |memory: &Value| {
        memory["text"]
            .as_str()
            .or(memory["action"].as_str())
            .unwrap()
            .to_string()
    };
    let mut names = Vec::new();
    let mut entries = Vec::new();
    for (n, memory) in new.iter().enumerate() {
        let mine = said(memory);
        let (mut related, mut unsure) = (Vec::new(), Vec::new());
        for other in listed.iter().chain(&new[..n]) {
            let theirs = said(other);
            // The new memory says `a` and the other says `b`.
            let is = |a: &str, b: &str| mine.contains(a) && theirs.contains(b);
            let current = memory["current"] != false && other["current"] != false;
            // `<` runs from the new memory to the other, `>` the other way.
            let rel = if memory["message"].is_string() && memory["message"] == other["message"] {
                Some(("CONTAINS", None))
            } else if is("closed_won", "negotiation") && current {
                Some(("SUPERSEDES<", Some("supersede")))
            } else if is("negotiation", "closed_won") && current {
                Some(("SUPERSEDES>", Some("supersede")))
            } else if is("Agreed", "phased cutover") || is("ledger_export", "Agreed") {
                Some(("LEADSTO>", None))
            } else if is("phased cutover", "Agreed") || is("Agreed", "ledger_export") {
                Some(("LEADSTO<", None))
            } else if is("ledger_export", "phased cutover") {
                Some(("EXPRESS<", None))
            } else if is("phased cutover", "ledger_export") {
                Some(("EXPRESS>", None))
            } else if is("Phase two", "phased cutover") || is("phased cutover", "Phase two") {
                Some(("NEAR", None))
            } else {
                None
            };
            match rel {
                Some((rel, Some(label))) => {
                    related.push(json!({"to": other["id"], "rel": rel, "label": label}));
                }
                Some((rel, None)) => related.push(json!({"to": other["id"], "rel": rel})),
                None => {}
            }
            if is("Owner:", "Agreed") || is("Agreed", "Owner:") {
                unsure.push(other["id"].clone());
            }
            // The decision and the reply that takes its work, and no other
            // pair that happens to hold the same words.
            let decided = is("Agreed", "phased cutover") || is("phased cutover", "Agreed");
            for (of_mine, of_theirs) in [
                ("the export", "ledger export"),
                ("ledger export", "the export"),
            ] {
                if decided && is(of_mine, of_theirs) {
                    names.push(json!({
                        "a": memory["id"], "a_name": of_mine,
                        "b": other["id"], "b_name": of_theirs,
                    }));
                }
            }
        }
        entries.push(json!({"id": memory["id"], "related": related, "unsure": unsure}));
    }
    Ok(ModelResponse {
        text: json!({"new": entries, "names": names}).to_string(),
        usage: Usage {
            input_tokens: 1_500,
            output_tokens: 200,
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

    // A call of a tool by an agent: its arguments and what came back, kept
    // whole. A model is shown it with the repetition taken out, and what
    // the model writes about it is the record that follows.
    w.clock.advance_millis(2_000);
    let passing: Vec<String> = (1..=48)
        .map(|n| format!("test ledger_export::batch_{n:02} ... ok"))
        .collect();
    w.accept(&event(json!({
        "scope": "acme",
        "source_ns": "agent/session-7f3a",
        "external_id": "toolu_01HqPz",
        "profile_id": "core.tool_call",
        "occurred_at": {"at": "2026-10-21T09:14:03Z", "precision": "second"},
        "actor": {"id": "agent-billing", "kind": "agent", "role": "assistant"},
        "media_type": "application/json",
        "content": {"tool_call": {
            "tool": "Bash",
            "input": {"timeout": 600, "command": "cargo test -p ledger_export"},
            "output": format!(
                "running 51 tests\n{}\ntest ledger_export::resume_after_timeout ... FAILED\n\
                 test ledger_export::large_ledger ... FAILED\n\
                 test ledger_export::retry_budget ... FAILED\n\n\
                 failures:\n    ledger_export::large_ledger\n    ledger_export::resume_after_timeout\n    ledger_export::retry_budget\n\n\
                 test result: FAILED. 48 passed; 3 failed; finished in 61.20s\n\
                 error: test failed, to rerun pass `-p ledger_export --lib`",
                passing.join("\n")
            ),
            "is_error": true
        }},
        "structure": {"session": "session-7f3a", "parent": {"external_id": "msg_0142"}},
        "parser_contract_version": "agent-log/1"
    })));
    w.clock.advance_millis(3_000);
    let tools = FnTransport(|request: &ModelRequest| {
        // What it is shown is the call as code shortened it.
        let shown: Value = serde_json::from_str(&request.user).unwrap();
        assert_eq!(shown["tool"], "Bash");
        assert!(
            shown["output"]
                .as_str()
                .unwrap()
                .contains("more lines like the one above]")
        );
        Ok(ModelResponse {
            text: json!({
                "action": "Ran the tests of the ledger_export package",
                "outcome": "48 tests passed and 3 failed: large_ledger, resume_after_timeout and retry_budget",
                "status": "failed",
            })
            .to_string(),
            usage: Usage {
                input_tokens: 700,
                output_tokens: 60,
            },
        })
    });
    let ReadRound::Handled(call) = w.engine.read_tool_once(&mut w.store, &tools).unwrap() else {
        panic!("the tool call was not read");
    };
    assert_eq!((call.problem, call.stored), (None, 1));

    // A judge reads how the beads relate: the messages and the plan of one
    // day together, then the records and the note stored days later, then
    // the tool call beside the first. The label it gives the two records
    // changes what is current, so it is read a second time before it is
    // written. Every pair it was shown is a verdict record, whatever it
    // said, and what it made of them follows: a group, a label, their
    // relationships and one pair of names.
    w.clock.advance_millis(4_000);
    let judging = FnTransport(judge);
    let mut reads = Vec::new();
    while let JudgeRound::Handled(read) = w.engine.judge_once(&mut w.store, &judging).unwrap() {
        assert_eq!(read.problem, None, "{read:?}");
        reads.push((
            read.second_read,
            read.new_beads,
            read.window_beads,
            read.verdicts,
        ));
        w.clock.advance_millis(1_000);
    }
    assert_eq!(
        reads,
        [
            (false, 7, 0, 21),
            (false, 3, 0, 3),
            (true, 1, 1, 1),
            (false, 1, 7, 7)
        ]
    );

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
            Change::ToolOutcome { scope, outcome } => {
                vec![Record::tool_outcome(scope, *outcome)]
            }
            Change::Bead(bead) => vec![Record::bead(*bead)],
            Change::Edge(edge) => vec![Record::edge(*edge)],
            Change::Verdict(verdict) => vec![Record::verdict(*verdict)],
            Change::NameLink(link) => vec![Record::name_link(*link)],
            Change::Erasure { scope, erasure } => vec![Record::erasure(scope, erasure)],
        };
        for mut record in records {
            // The two values the database gives are not pinned.
            match &mut record.body {
                RecordBody::ChunkRead(read) => read.call_id = no_call(),
                RecordBody::ToolOutcome(outcome) => outcome.call_id = no_call(),
                RecordBody::Erasure(erasure) => {
                    erasure.erased_at = Timestamp::parse("2026-01-01T00:00:00Z").unwrap();
                }
                RecordBody::Bead(bead) => bead.call_id = no_call(),
                RecordBody::Edge(edge) => edge.call_id = no_call(),
                RecordBody::Verdict(verdict) => verdict.call_id = no_call(),
                RecordBody::NameLink(link) => link.call_id = no_call(),
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

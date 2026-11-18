//! The goal/decision cases, read end to end with a stand-in model.
//!
//! The cases test one distinction: a goal is not an outcome, and a
//! decision is not an action. Which answer a real model gives is measured
//! with a real model, at a cost, and not here. This test is the scorer for
//! that measurement, and a check that a label and its evidence pass through
//! the engine unchanged. A stand-in that answers with each case's label
//! and quotes its evidence must score perfectly, with every quote stored
//! as the byte range the case gives. A stand-in that makes the two
//! confusions must be caught, and one that abstains must be counted as
//! neither right nor wrong.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use std::collections::BTreeMap;

use common::World;
use core_memory_contract::{ChunkId, Content, ReadDecision, SourceEvent, SpanKind};
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_core::store::Change;
use core_memory_core::{ReadRound, Store};
use serde::Deserialize;
use serde_json::{Value, json};

const FIXTURE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../fixtures/goal-decision-v1"
);
const DESTINATION: &str = "models.example.com";
const READER: &str = r#"
[reader]
binding = "stand-in"
batch_size = 8

[[binding]]
id = "stand-in"
provider = "stand-in"
endpoint = "https://models.example.com/v1"
model = "stand-in-1"
max_output_tokens = 2048
"#;

#[derive(Debug, Deserialize)]
struct Case {
    case: String,
    label: String,
    acceptable: Vec<String>,
    forbidden: Vec<String>,
    evidence: Vec<Evidence>,
    event: SourceEvent,
}

#[derive(Debug, Deserialize)]
struct Evidence {
    byte_start: u64,
    byte_end: u64,
    quote: String,
}

impl Case {
    fn text(&self) -> &str {
        match &self.event.content {
            Content::Text(text) => text,
            other => panic!("{} is not text: {:?}", self.case, other.kind()),
        }
    }
}

fn cases() -> Vec<Case> {
    std::fs::read_to_string(format!("{FIXTURE}/cases.jsonl"))
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

/// How a model's reads compare with the cases' labels.
#[derive(Debug, Default, PartialEq, Eq)]
struct Score {
    /// The preferred label.
    preferred: usize,
    /// Another label the case accepts.
    acceptable: usize,
    /// One of the confusions the case names.
    forbidden: usize,
    /// Any other type.
    other: usize,
    /// The model gave no type. Not a wrong answer.
    abstained: usize,
    /// No read is stored.
    unread: usize,
}

/// What a stand-in says about one case.
enum Answer {
    Type(String),
    Abstain,
}

/// A world with the fixture's vocabulary, its scope enrolled and consenting,
/// every case admitted, and every chunk read by a stand-in that answers
/// each case with `answer` and quotes its evidence as actions.
fn read_with(cases: &[Case], answer: impl Fn(&Case) -> Answer + Send + Sync) -> World {
    let vocabulary = std::fs::read_to_string(format!("{FIXTURE}/vocabulary.toml")).unwrap();
    let mut w = World::with_config(&format!("{vocabulary}{READER}"));
    w.store.enroll_scope("fixtures", "w1").unwrap();
    w.store.grant_destination("fixtures", DESTINATION).unwrap();
    for case in cases {
        w.accept(&case.event);
    }
    let by_text: BTreeMap<&str, &Case> = cases.iter().map(|c| (c.text(), c)).collect();
    let model = FnTransport(
        |request: &ModelRequest| -> Result<ModelResponse, TransportError> {
            let shown: Value = serde_json::from_str(request.user.lines().next().unwrap()).unwrap();
            // The request offers the fixture's types with their definitions.
            let offered = shown["types"].as_array().unwrap();
            assert_eq!(offered.len(), 5);
            let goal = offered.iter().find(|t| t["key"] == "goal").unwrap();
            assert!(
                goal["definition"]
                    .as_str()
                    .unwrap()
                    .contains("Stating a goal is not evidence that it was reached")
            );
            let items: Vec<Value> = shown["chunks"]
                .as_array()
                .unwrap()
                .iter()
                .map(|chunk| {
                    let case = by_text[chunk["text"].as_str().unwrap()];
                    let actions: Vec<Value> = case
                        .evidence
                        .iter()
                        .map(|e| json!({"text": e.quote}))
                        .collect();
                    match answer(case) {
                        Answer::Type(key) => {
                            json!({"chunk": chunk["chunk"], "type": key, "actions": actions})
                        }
                        Answer::Abstain => {
                            json!({"chunk": chunk["chunk"], "abstain": "ambiguous"})
                        }
                    }
                })
                .collect();
            Ok(ModelResponse {
                text: json!({"items": items}).to_string(),
                usage: Usage {
                    input_tokens: 1_000,
                    output_tokens: 200,
                },
            })
        },
    );
    while let ReadRound::Handled(batch) = w.engine.read_once(&mut w.store, &model).unwrap() {
        assert_eq!((batch.problem, batch.stored), (None, batch.chunks));
    }
    w
}

/// Scores what is stored against the cases, and checks every stored span
/// against the case's evidence.
fn score(cases: &[Case], w: &mut World) -> Score {
    let by_id: BTreeMap<&str, &Case> = cases
        .iter()
        .map(|c| (c.event.external_id.as_str(), c))
        .collect();
    let mut chunk_case: BTreeMap<ChunkId, &Case> = BTreeMap::new();
    let mut score = Score::default();
    let mut read = 0;
    for change in w.store.export("w1", 0, 10_000, Some("fixtures")).unwrap() {
        match change {
            Change::Event(exported) => {
                let case = by_id[exported.event.key.external_id.as_str()];
                // Each case is short enough to be one chunk.
                assert_eq!(exported.chunks.len(), 1, "{}", case.case);
                assert_eq!(exported.chunks[0].text, case.text());
                chunk_case.insert(exported.chunks[0].chunk_id, case);
            }
            Change::Read { read: record, .. } => {
                let case = chunk_case[&record.chunk_id];
                read += 1;
                match &record.decision {
                    ReadDecision::Abstained { .. } => {
                        score.abstained += 1;
                        assert!(record.spans.is_empty());
                    }
                    ReadDecision::Typed { type_ref } => {
                        let key = &type_ref.key;
                        if *key == case.label {
                            score.preferred += 1;
                        } else if case.acceptable.contains(key) {
                            score.acceptable += 1;
                        } else if case.forbidden.contains(key) {
                            score.forbidden += 1;
                        } else {
                            score.other += 1;
                        }
                        // The quotes came back as the ranges the case gives.
                        let stored: Vec<(u64, u64)> = record
                            .spans
                            .iter()
                            .inspect(|span| assert_eq!(span.kind, SpanKind::Action))
                            .map(|span| (span.byte_start, span.byte_end))
                            .collect();
                        let expected: Vec<(u64, u64)> = case
                            .evidence
                            .iter()
                            .map(|e| (e.byte_start, e.byte_end))
                            .collect();
                        assert_eq!(stored, expected, "{}", case.case);
                        assert_eq!(record.spans_rejected, 0);
                    }
                }
            }
            Change::ToolOutcome { .. } => panic!("no case is a tool call"),
            Change::Bead(_) | Change::Edge(_) | Change::Verdict(_) | Change::NameLink(_) => {
                panic!("nothing was judged")
            }
            Change::Erasure { .. } => panic!("nothing was erased"),
        }
    }
    score.unread = cases.len() - read;
    score
}

#[test]
fn the_labels_and_evidence_of_every_case_pass_through_unchanged() {
    let cases = cases();
    assert_eq!(cases.len(), 25);
    let mut w = read_with(&cases, |case| Answer::Type(case.label.clone()));
    assert_eq!(
        score(&cases, &mut w),
        Score {
            preferred: 25,
            ..Score::default()
        }
    );
    // One read for each case, and no text of the model's or the source's
    // outside chunk_text.
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_reads"), 25);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunk_read_spans"),
        cases.iter().map(|c| c.evidence.len() as i64).sum::<i64>()
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
}

#[test]
fn the_two_confusions_are_caught_and_an_abstention_is_not_one() {
    let cases = cases();
    // A goal called an outcome, a decision called an action.
    let mut confused = read_with(&cases, |case| Answer::Type(case.forbidden[0].clone()));
    assert_eq!(
        score(&cases, &mut confused),
        Score {
            forbidden: 25,
            ..Score::default()
        }
    );

    // The two reported cases may also be called statements. Every other
    // case called a statement is wrong, though not one of the confusions.
    let mut reported = read_with(&cases, |_| Answer::Type("statement".into()));
    assert_eq!(
        score(&cases, &mut reported),
        Score {
            acceptable: 2,
            other: 23,
            ..Score::default()
        }
    );

    // Saying nothing is not saying something wrong.
    let mut silent = read_with(&cases, |case| {
        if case.label == "goal" {
            Answer::Abstain
        } else {
            Answer::Type(case.label.clone())
        }
    });
    assert_eq!(
        score(&cases, &mut silent),
        Score {
            preferred: 12,
            abstained: 13,
            ..Score::default()
        }
    );
}

//! The goal/decision cases in `fixtures/goal-decision-v1`, run through the
//! model tier.
//!
//! The default tests check the fixture and drive every case through the
//! classifier with the gold answers. `live_model_keeps_goals_and_decisions_apart`
//! scores a real binding instead. It calls a paid API, so it only runs when
//! asked:
//!
//! ```text
//! CORE_MEMORY_EVAL_BINDING=binding.toml \
//!     cargo test -p core-memory-sidecar --test goal_decision -- --ignored --nocapture
//! ```

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use core_memory_contract::{AdmissionOutcome, Record, RecordBody, SourceEvent, TypeOutcome};
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, Usage};
use core_memory_core::store::OperationalStore;
use core_memory_core::{MemoryStore, ModelTransport, ScopeGrants};
use core_memory_sidecar::config::Loaded;
use core_memory_sidecar::runtime::{build_engine, build_transport};
use serde::Deserialize;
use serde_json::{Value, json};

const SCOPE: &str = "fixtures";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Case {
    case: String,
    buckets: Vec<String>,
    /// The preferred type.
    label: String,
    /// Types that also count as correct.
    acceptable: Vec<String>,
    /// A goal typed as a result, or a decision typed as the work it chose.
    forbidden: Vec<String>,
    rationale: String,
    evidence: Vec<Evidence>,
    event: Value,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Evidence {
    id: String,
    byte_start: usize,
    byte_end: usize,
    quote: String,
}

impl Case {
    fn text(&self) -> &str {
        self.event["content"]["text"].as_str().unwrap()
    }
}

fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/goal-decision-v1")
}

fn cases() -> Vec<Case> {
    std::fs::read_to_string(fixture_dir().join("cases.jsonl"))
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

/// A config with the fixture vocabulary and the given classifier tables.
fn load(dir: &Path, classifier: &str) -> Loaded {
    let vocabulary = std::fs::read_to_string(fixture_dir().join("vocabulary.toml")).unwrap();
    let config = format!(
        r#"
[store]
path = "store.sqlite"

[grants]
scopes = ["{SCOPE}"]

[[sink]]
id = "records"
kind = "jsonl"
path = "records.jsonl"

{vocabulary}
{classifier}
"#
    );
    let path = dir.join("core-memory.toml");
    std::fs::write(&path, config).unwrap();
    Loaded::read(&path).unwrap()
}

/// Admits every case, classifies what is due, and returns each case's last
/// delivered type outcome. A case missing from the map was never delivered.
fn classify(
    loaded: &Loaded,
    transport: &dyn ModelTransport,
    cases: &[Case],
) -> BTreeMap<String, TypeOutcome> {
    let engine = build_engine(loaded).unwrap();
    let mut store = MemoryStore::new();
    for case in cases {
        let event: SourceEvent = serde_json::from_value(case.event.clone()).unwrap();
        let outcome = engine.admit(&mut store, &ScopeGrants::All, &event).unwrap();
        assert!(
            matches!(outcome, AdmissionOutcome::Accepted { .. }),
            "{}: {outcome:?}",
            case.case
        );
    }
    // Retries back off for longer than this test waits, so a few passes
    // are enough to tell what the binding decided on its first tries.
    for _ in 0..3 {
        if store.classification_queue_len().unwrap() == 0 {
            break;
        }
        engine
            .classify_due(&mut store, transport, usize::MAX)
            .unwrap();
    }

    let by_text: BTreeMap<&str, &str> = cases.iter().map(|c| (c.text(), c.case.as_str())).collect();
    let mut case_of_chunk = BTreeMap::new();
    let mut outcomes = BTreeMap::new();
    for entry in store.outbox_after(0, usize::MAX).unwrap() {
        let record: Record = serde_json::from_str(&entry.record_json).unwrap();
        match record.body {
            RecordBody::Chunk(c) => {
                // Each case is one chunk, so the case label is the chunk's.
                let case = by_text[c.memory.chunk.text.as_str()];
                assert_eq!(c.event.chunk_count, 1, "{case}");
                case_of_chunk.insert(c.memory.chunk.chunk_id, case.to_string());
                outcomes.insert(case.to_string(), c.memory.type_outcome);
            }
            RecordBody::TypeDecision(d) => {
                // A decision that arrived after its chunk was released.
                outcomes.insert(case_of_chunk[&d.chunk_id].clone(), d.outcome);
            }
        }
    }
    outcomes
}

#[derive(Debug, Default)]
struct Score {
    preferred: usize,
    acceptable: usize,
    forbidden: Vec<String>,
    misses: Vec<String>,
}

fn score(cases: &[Case], outcomes: &BTreeMap<String, TypeOutcome>) -> Score {
    let mut s = Score::default();
    for case in cases {
        let got = match outcomes.get(&case.case) {
            Some(TypeOutcome::Assigned { type_ref }) => type_ref.key.clone(),
            Some(other) => serde_json::to_string(other).unwrap(),
            None => "not delivered".into(),
        };
        let line = format!(
            "{} [{}] expected {}, got {got}. {}",
            case.case,
            case.buckets.join(", "),
            case.label,
            case.rationale
        );
        if got == case.label {
            s.preferred += 1;
        }
        if case.acceptable.contains(&got) {
            s.acceptable += 1;
        } else if case.forbidden.contains(&got) {
            s.forbidden.push(line);
        } else {
            s.misses.push(line);
        }
    }
    s
}

#[test]
fn the_fixture_quotes_its_source_exactly() {
    let cases = cases();
    let dir = tempfile::tempdir().unwrap();
    let loaded = load(dir.path(), "");
    let types: BTreeSet<String> = loaded
        .config
        .vocabulary
        .unwrap()
        .types
        .into_iter()
        .map(|t| t.key)
        .collect();

    assert_eq!(cases.len(), 25);
    let goals = cases.iter().filter(|c| c.label == "goal").count();
    let decisions = cases.iter().filter(|c| c.label == "decision").count();
    assert_eq!((goals, decisions), (13, 12));
    let ids: BTreeSet<&str> = cases.iter().map(|c| c.case.as_str()).collect();
    assert_eq!(ids.len(), cases.len(), "case ids repeat");

    for case in &cases {
        let c = &case.case;
        assert_eq!(case.event["external_id"], c.as_str());
        assert!(case.acceptable.contains(&case.label), "{c}");
        for t in case.acceptable.iter().chain(&case.forbidden) {
            assert!(types.contains(t), "{c}: `{t}` is not in the vocabulary");
        }
        assert!(
            case.forbidden.iter().all(|t| !case.acceptable.contains(t)),
            "{c}"
        );
        assert!(!case.evidence.is_empty(), "{c}");
        for e in &case.evidence {
            assert_eq!(
                case.text().get(e.byte_start..e.byte_end),
                Some(e.quote.as_str()),
                "{c} {}",
                e.id
            );
        }
    }
}

#[test]
fn gold_answers_type_every_case() {
    let cases = cases();
    let labels: BTreeMap<String, String> = cases
        .iter()
        .map(|c| (c.text().to_string(), c.label.clone()))
        .collect();
    let gold = FnTransport(move |request: &ModelRequest| {
        let user: Value = serde_json::from_str(&request.user).unwrap();
        let items: Vec<Value> = user["chunks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| json!({"chunk": c["chunk"], "type": labels[c["text"].as_str().unwrap()]}))
            .collect();
        Ok(ModelResponse {
            text: json!({"items": items}).to_string(),
            usage: Usage {
                input_tokens: 1000,
                output_tokens: 100,
            },
        })
    });
    let dir = tempfile::tempdir().unwrap();
    let loaded = load(
        dir.path(),
        &format!(
            r#"
[classifier]
binding = "gold"

[[binding]]
id = "gold"
provider = "scripted"
endpoint = "local"
model = "gold-2026-01-01"
max_output_tokens = 1024
scopes = ["{SCOPE}"]
budget = {{ max_requests_per_day = 100, max_tokens_per_day = 1000000 }}
"#
        ),
    );

    let s = score(&cases, &classify(&loaded, &gold, &cases));
    assert!(s.forbidden.is_empty() && s.misses.is_empty(), "{s:#?}");
    assert_eq!((s.preferred, s.acceptable), (25, 25));
}

#[test]
#[ignore = "calls a paid model API; set CORE_MEMORY_EVAL_BINDING to run it"]
fn live_model_keeps_goals_and_decisions_apart() {
    let path = std::env::var("CORE_MEMORY_EVAL_BINDING")
        .expect("set CORE_MEMORY_EVAL_BINDING to a TOML file with [classifier] and [[binding]]");
    let tables = std::fs::read_to_string(&path).unwrap();
    let dir = tempfile::tempdir().unwrap();
    let mut loaded = load(dir.path(), &tables);
    for binding in &mut loaded.config.bindings {
        binding.scopes = vec![SCOPE.into()];
    }
    let transport = build_transport(&loaded)
        .unwrap()
        .expect("the binding file configures no [classifier]");

    let cases = cases();
    let outcomes = classify(&loaded, transport.as_ref(), &cases);
    let decided = outcomes.values().filter(|o| o.is_final()).count();
    let s = score(&cases, &outcomes);
    println!(
        "goal/decision v1: {} of {} preferred, {} acceptable, {} forbidden",
        s.preferred,
        cases.len(),
        s.acceptable,
        s.forbidden.len()
    );
    for line in s.forbidden.iter().chain(&s.misses) {
        println!("  {line}");
    }
    assert_eq!(
        decided,
        cases.len(),
        "the binding did not decide every case, so the score is incomplete"
    );
    assert!(
        s.forbidden.is_empty(),
        "goals typed as results or decisions typed as the work they chose"
    );
}

//! The suite against reference in-memory sinks, the checks against broken
//! ones, and the standard run against its golden records.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use core_memory_conformance::{Harness, Scenario, checks};
use core_memory_core::sink::{Sink, SinkCaps, SinkError, WriteOutcome};
use core_memory_core::store::OutboxEntry;
use serde_json::Value;

/// How a test sink behaves.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Behavior {
    /// Appends every write, like the JSONL sink.
    Append,
    /// Keeps the first record per key and reports conflicts.
    SystemOfRecord,
    /// Drops type decision records.
    DropsTypeDecisions,
    /// Replaces non-ASCII characters in the text it stores.
    Ascii,
}

#[derive(Debug)]
struct MemorySink {
    behavior: Behavior,
    max_record_bytes: u64,
    lines: Arc<Mutex<Vec<String>>>,
}

impl Sink for MemorySink {
    fn id(&self) -> &str {
        "memory"
    }

    fn caps(&self) -> SinkCaps {
        SinkCaps {
            system_of_record: self.behavior == Behavior::SystemOfRecord,
            ..SinkCaps::accepts_all_v1(self.max_record_bytes)
        }
    }

    fn write(&mut self, batch: &[OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError> {
        let mut lines = self.lines.lock().unwrap();
        let mut outcomes = Vec::new();
        for entry in batch {
            let stored = lines
                .iter()
                .find(|l| l.contains(&format!("\"record_key\":\"{}\"", entry.record_key)))
                .cloned();
            let outcome = match (self.behavior, stored) {
                (Behavior::SystemOfRecord, Some(stored)) if stored == entry.record_json => {
                    WriteOutcome::AlreadyAccepted
                }
                (Behavior::SystemOfRecord, Some(stored)) => WriteOutcome::Conflict { stored },
                (Behavior::DropsTypeDecisions, _)
                    if entry
                        .record_json
                        .contains("\"record_kind\":\"type_decision\"") =>
                {
                    WriteOutcome::Accepted
                }
                (Behavior::Ascii, _) => {
                    let ascii: String = entry
                        .record_json
                        .chars()
                        .map(|c| if c.is_ascii() { c } else { '?' })
                        .collect();
                    lines.push(ascii);
                    WriteOutcome::Accepted
                }
                _ => {
                    lines.push(entry.record_json.clone());
                    WriteOutcome::Accepted
                }
            };
            outcomes.push(outcome);
        }
        Ok(outcomes)
    }
}

#[derive(Debug)]
struct MemoryHarness {
    behavior: Behavior,
    lines: Arc<Mutex<Vec<String>>>,
}

impl MemoryHarness {
    fn new(behavior: Behavior) -> Self {
        Self {
            behavior,
            lines: Arc::default(),
        }
    }
}

impl Harness for MemoryHarness {
    fn open(&mut self, max_record_bytes: u64) -> Box<dyn Sink> {
        Box::new(MemorySink {
            behavior: self.behavior,
            max_record_bytes,
            lines: self.lines.clone(),
        })
    }

    fn read(&mut self) -> Vec<Value> {
        self.lines
            .lock()
            .unwrap()
            .iter()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
}

mod append {
    use super::*;
    core_memory_conformance::sink_conformance!(MemoryHarness::new(Behavior::Append));
}

mod system_of_record {
    use super::*;
    core_memory_conformance::sink_conformance!(MemoryHarness::new(Behavior::SystemOfRecord));
}

#[test]
#[should_panic(expected = "the sink lost")]
fn a_sink_that_drops_records_fails() {
    checks::a_late_type_follows_its_pending_chunk(&mut MemoryHarness::new(
        Behavior::DropsTypeDecisions,
    ));
}

#[test]
#[should_panic(expected = "the sink altered")]
fn a_sink_that_alters_text_fails() {
    checks::delivers_every_record_once_in_order(&mut MemoryHarness::new(Behavior::Ascii));
}

/// Checks the standard and late-type runs against `golden/`. Run with
/// `CORE_MEMORY_BLESS=1` to rewrite the files after a deliberate change.
#[test]
fn records_match_the_golden_files() {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("golden");
    let runs: BTreeMap<&str, Scenario> = BTreeMap::from([
        ("standard.jsonl", Scenario::standard()),
        ("late-type.jsonl", Scenario::late_type(|_| {})),
    ]);
    let bless = std::env::var_os("CORE_MEMORY_BLESS").is_some();
    for (file, scenario) in runs {
        let mut actual = String::new();
        for entry in scenario.outbox() {
            actual.push_str(&entry.record_json);
            actual.push('\n');
        }
        let path = dir.join(file);
        if bless {
            std::fs::write(&path, &actual).unwrap();
            continue;
        }
        let expected = std::fs::read_to_string(&path).unwrap_or_default();
        assert!(
            actual == expected,
            "{file} differs from what the engine now produces. If the change is \
             intended, rerun with CORE_MEMORY_BLESS=1 and review the diff as a \
             contract change."
        );
    }
}

//! The JSONL sink against the sink conformance suite.

#![allow(clippy::unwrap_used)]

use core_memory_conformance::Harness;
use core_memory_core::sink::Sink;
use core_memory_sink_jsonl::JsonlSink;
use serde_json::Value;

struct JsonlHarness {
    dir: tempfile::TempDir,
}

impl JsonlHarness {
    fn new() -> Self {
        Self {
            dir: tempfile::tempdir().unwrap(),
        }
    }
}

impl Harness for JsonlHarness {
    fn open(&mut self, max_record_bytes: u64) -> Box<dyn Sink> {
        let path = self.dir.path().join("records.jsonl");
        Box::new(JsonlSink::open("records", path, max_record_bytes).unwrap())
    }

    /// Every line, as a reader of the file sees it.
    fn read(&mut self) -> Vec<Value> {
        std::fs::read_to_string(self.dir.path().join("records.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
}

core_memory_conformance::sink_conformance!(JsonlHarness::new());

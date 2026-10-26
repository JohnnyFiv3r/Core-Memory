//! The `core-memory` binary with a model classifier, against a mock
//! OpenAI-compatible server.

#![allow(clippy::unwrap_used)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use core_memory_model_http::testing::{Received, Reply, serve};
use serde_json::{Value, json};

fn example_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../examples/quickstart")
}

/// The quickstart with a classifier pointed at `endpoint`.
fn setup(binding: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    std::fs::copy(
        example_dir().join("events.jsonl"),
        dir.path().join("events.jsonl"),
    )
    .unwrap();
    let mut config = std::fs::read_to_string(example_dir().join("core-memory.toml")).unwrap();
    config.push_str(binding);
    std::fs::write(dir.path().join("core-memory.toml"), config).unwrap();
    dir
}

fn compatible_binding(endpoint: &str) -> String {
    format!(
        r#"
[classifier]
binding = "local"
deadline_ms = 60000

[[binding]]
id = "local"
provider = "openai-compatible"
endpoint = "{endpoint}/v1"
model = "qwen3-8b-2026-01-01"
temperature = 0.0
max_output_tokens = 1024
scopes = ["acme"]
budget = {{ max_requests_per_day = 100, max_tokens_per_day = 1000000 }}
"#
    )
}

fn run(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_core-memory"))
        .args(args)
        .current_dir(dir)
        .env_remove("ANTHROPIC_API_KEY")
        .output()
        .unwrap()
}

fn records(dir: &Path) -> Vec<Value> {
    std::fs::read_to_string(dir.join("records.jsonl"))
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

/// Answers every chunk handle with `decision`.
fn decide_all(r: &Received) -> Reply {
    let body: Value = serde_json::from_str(&r.body).unwrap();
    let user: Value =
        serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
    let items: Vec<Value> = user["chunks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| json!({"chunk": c["chunk"], "type": "decision"}))
        .collect();
    let content = json!({"items": items}).to_string();
    Reply::json(
        200,
        json!({
            "choices": [{"message": {"role": "assistant", "content": content, "refusal": null}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 10}
        })
        .to_string(),
    )
}

#[test]
fn ingest_classifies_chunks_no_rule_typed() {
    // Two chat turns and one document need the model, in two batches
    // (one per profile). The CRM change is typed by its rule.
    let (url, server) = serve(2, decide_all);
    let dir = setup(&compatible_binding(&url));
    let out = run(dir.path(), &["ingest", "--input", "events.jsonl"]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let recs = records(dir.path());
    assert_eq!(recs.len(), 4);
    let methods: Vec<&str> = recs
        .iter()
        .map(|r| r["decision"]["method"].as_str().unwrap())
        .collect();
    assert_eq!(
        methods.iter().filter(|m| **m == "model").count(),
        3,
        "{methods:?}"
    );
    assert_eq!(methods.iter().filter(|m| **m == "rule").count(), 1);
    for r in recs.iter().filter(|r| r["decision"]["method"] == "model") {
        assert_eq!(r["memory"]["type"]["type_ref"]["key"], "decision");
        assert_eq!(r["decision"]["binding_ref"], "local");
    }
    let requests = server.join().unwrap();
    assert!(
        requests
            .iter()
            .all(|r| r.request_line == "POST /v1/chat/completions HTTP/1.1")
    );
}

#[test]
fn an_outage_releases_pending_and_classify_completes_later() {
    let (down, _s) = serve(2, |_| Reply::json(503, r#"{"error": "down"}"#));
    // A 1 ms backoff so the retry is due by the time `classify` runs.
    let binding = compatible_binding(&down).replace(
        "deadline_ms = 60000",
        "deadline_ms = 60000\nretry_backoff_ms = 1",
    );
    let dir = setup(&binding);
    let out = run(dir.path(), &["ingest", "--input", "events.jsonl"]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let recs = records(dir.path());
    let pending: Vec<&Value> = recs
        .iter()
        .filter(|r| r["memory"]["type"]["status"] == "pending")
        .collect();
    assert_eq!(pending.len(), 3);
    assert!(
        pending
            .iter()
            .all(|r| r["memory"]["type"]["reason"] == "provider_error")
    );

    // The provider recovers. (The endpoint is part of the binding's
    // revision, so decisions record which endpoint made them.)
    let (up, _s) = serve(2, decide_all);
    let config_path = dir.path().join("core-memory.toml");
    let config = std::fs::read_to_string(&config_path).unwrap();
    std::fs::write(&config_path, config.replace(&down, &up)).unwrap();
    std::thread::sleep(std::time::Duration::from_millis(50));
    let out = run(dir.path(), &["classify"]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let recs = records(dir.path());
    let decisions: Vec<&Value> = recs
        .iter()
        .filter(|r| r["record_kind"] == "type_decision")
        .collect();
    assert_eq!(
        decisions.len(),
        3,
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        decisions
            .iter()
            .all(|d| d["outcome"]["type_ref"]["key"] == "decision")
    );
}

#[test]
fn a_classifier_without_credentials_refuses_to_start() {
    let dir = setup(
        r#"
[classifier]
binding = "claude"

[[binding]]
id = "claude"
provider = "anthropic"
endpoint = "https://api.anthropic.com"
model = "claude-opus-5-5"
reasoning_effort = "low"
max_output_tokens = 4096
scopes = ["acme"]
budget = { max_requests_per_day = 100, max_tokens_per_day = 1000000 }
"#,
    );
    let out = run(dir.path(), &["ingest", "--input", "events.jsonl"]);
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("ANTHROPIC_API_KEY"));
    assert!(records(dir.path()).is_empty());
}

#[test]
fn an_anthropic_binding_with_temperature_refuses_to_start() {
    let dir = setup(
        r#"
[classifier]
binding = "claude"

[[binding]]
id = "claude"
provider = "anthropic"
endpoint = "https://api.anthropic.com"
model = "claude-opus-5-5"
temperature = 0.0
max_output_tokens = 4096
scopes = ["acme"]
budget = { max_requests_per_day = 100, max_tokens_per_day = 1000000 }
"#,
    );
    let out = Command::new(env!("CARGO_BIN_EXE_core-memory"))
        .args(["check"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    // `check` validates without opening the store or reaching a provider.
    assert!(out.status.success());
    let out = Command::new(env!("CARGO_BIN_EXE_core-memory"))
        .args(["ingest", "--input", "events.jsonl"])
        .current_dir(dir.path())
        .env("ANTHROPIC_API_KEY", "sk-test")
        .output()
        .unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("temperature"));
}

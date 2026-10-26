//! Runs the `core-memory` binary on the quickstart example.

#![allow(clippy::unwrap_used)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn example_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../examples/quickstart")
}

fn setup() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    for file in ["core-memory.toml", "events.jsonl"] {
        std::fs::copy(example_dir().join(file), dir.path().join(file)).unwrap();
    }
    dir
}

fn run(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_core-memory"))
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap()
}

fn results(output: &Output) -> Vec<serde_json::Value> {
    String::from_utf8(output.stdout.clone())
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

#[test]
fn ingest_delivers_once_and_replays_after() {
    let dir = setup();
    let first = run(dir.path(), &["ingest", "--input", "events.jsonl"]);
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    let outcomes = results(&first);
    assert_eq!(outcomes.len(), 4);
    assert!(
        outcomes
            .iter()
            .all(|o| o["result"]["outcome"] == "accepted")
    );

    let records = std::fs::read_to_string(dir.path().join("records.jsonl")).unwrap();
    let records: Vec<serde_json::Value> = records
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(records.len(), 4);
    let crm = records
        .iter()
        .find(|r| r["event"]["source_system"] == "salesforce")
        .unwrap();
    assert_eq!(crm["memory"]["type"]["type_ref"]["key"], "outcome");
    assert_eq!(crm["decision"]["rule_id"], "opportunity-closed");

    let second = run(dir.path(), &["ingest", "--input", "events.jsonl"]);
    assert!(second.status.success());
    assert!(
        results(&second)
            .iter()
            .all(|o| o["result"]["outcome"] == "replay")
    );
    let after = std::fs::read_to_string(dir.path().join("records.jsonl")).unwrap();
    assert_eq!(after.lines().count(), 4, "replays deliver nothing");
}

#[test]
fn bad_lines_are_reported_and_the_rest_admitted() {
    let dir = setup();
    let events = std::fs::read_to_string(dir.path().join("events.jsonl")).unwrap();
    std::fs::write(
        dir.path().join("events.jsonl"),
        format!("not json\n{events}"),
    )
    .unwrap();
    let out = run(dir.path(), &["ingest", "--input", "events.jsonl"]);
    assert!(out.status.success());
    let outcomes = results(&out);
    assert_eq!(outcomes[0]["result"]["outcome"], "refused");
    assert_eq!(outcomes[0]["result"]["reason"], "malformed");
    assert_eq!(outcomes.len(), 5);
}

#[test]
fn queued_records_wait_for_deliver() {
    let dir = setup();
    let out = run(
        dir.path(),
        &["ingest", "--input", "events.jsonl", "--no-deliver"],
    );
    assert!(out.status.success());
    assert!(
        !dir.path().join("records.jsonl").exists()
            || std::fs::read_to_string(dir.path().join("records.jsonl"))
                .unwrap()
                .is_empty()
    );
    let deliver = run(dir.path(), &["deliver"]);
    assert!(deliver.status.success());
    let records = std::fs::read_to_string(dir.path().join("records.jsonl")).unwrap();
    assert_eq!(records.lines().count(), 4);
}

#[test]
fn changing_a_profile_without_a_new_rev_refuses_to_start() {
    let dir = setup();
    assert!(
        run(dir.path(), &["ingest", "--input", "events.jsonl"])
            .status
            .success()
    );
    let config_path = dir.path().join("core-memory.toml");
    let config = std::fs::read_to_string(&config_path).unwrap();
    std::fs::write(&config_path, config.replace("max = 65536", "max = 32768")).unwrap();
    let out = run(dir.path(), &["ingest", "--input", "events.jsonl"]);
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("bump its rev"));
}

#[test]
fn check_and_schema_commands() {
    let dir = setup();
    let check = run(dir.path(), &["check"]);
    assert!(
        check.status.success(),
        "{}",
        String::from_utf8_lossy(&check.stderr)
    );
    assert!(results(&check).iter().any(|p| p["profile"] == "acme.crm"));

    let list = run(dir.path(), &["schema"]);
    assert!(String::from_utf8_lossy(&list.stdout).contains("record\t"));
    let record = run(dir.path(), &["schema", "record"]);
    let schema: serde_json::Value = serde_json::from_slice(&record.stdout).unwrap();
    assert!(schema.is_object());
}

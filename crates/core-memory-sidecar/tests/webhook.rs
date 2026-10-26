//! The `core-memory` binary delivering to a webhook receiver.

#![allow(clippy::unwrap_used)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

use core_memory_model_http::testing::{Reply, serve};
use core_memory_sink_webhook::{SIGNATURE_HEADER, verify};
use serde_json::Value;

const SECRET: &str = "a webhook secret of 32 bytes ...";

fn example_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../examples/quickstart")
}

/// The quickstart with `sink` appended to its config.
fn setup(sink: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    std::fs::copy(
        example_dir().join("events.jsonl"),
        dir.path().join("events.jsonl"),
    )
    .unwrap();
    let mut config = std::fs::read_to_string(example_dir().join("core-memory.toml")).unwrap();
    config.push_str(sink);
    std::fs::write(dir.path().join("core-memory.toml"), config).unwrap();
    dir
}

fn webhook(url: &str) -> String {
    format!(
        r#"
[[sink]]
id = "host"
kind = "webhook"
url = "{url}/core-memory"
secret_env = "CORE_MEMORY_TEST_WEBHOOK_SECRET"
"#
    )
}

fn run(dir: &Path, args: &[&str], secret: Option<&str>) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_core-memory"));
    command
        .args(args)
        .current_dir(dir)
        .env_remove("CORE_MEMORY_TEST_WEBHOOK_SECRET");
    if let Some(secret) = secret {
        command.env("CORE_MEMORY_TEST_WEBHOOK_SECRET", secret);
    }
    command.output().unwrap()
}

#[test]
fn ingest_posts_signed_records_to_the_receiver() {
    let (url, received) = serve(1, |_| Reply {
        status: 204,
        headers: Vec::new(),
        body: String::new(),
    });
    let dir = setup(&webhook(&url));
    let out = run(
        dir.path(),
        &["ingest", "--input", "events.jsonl"],
        Some(SECRET),
    );
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );

    let request = received.join().unwrap().remove(0);
    assert!(
        request.request_line.starts_with("POST /core-memory "),
        "{}",
        request.request_line
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let signature = request.header(SIGNATURE_HEADER).unwrap();
    assert_eq!(
        verify(
            SECRET.as_bytes(),
            signature,
            request.body.as_bytes(),
            now,
            300
        ),
        Ok(())
    );
    let body: Value = serde_json::from_str(&request.body).unwrap();
    assert_eq!(body["sink"], "host");
    let sent: Vec<Value> = body["records"].as_array().unwrap().clone();

    // The webhook received exactly what the JSONL sink wrote.
    let written: Vec<Value> = std::fs::read_to_string(dir.path().join("records.jsonl"))
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(sent.len(), 4);
    assert_eq!(sent, written);
}

#[test]
fn a_webhook_without_its_secret_refuses_to_start() {
    let dir = setup(&webhook("http://127.0.0.1:9"));
    for secret in [None, Some("too short")] {
        let out = run(dir.path(), &["ingest", "--input", "events.jsonl"], secret);
        assert!(!out.status.success());
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(
            stderr.contains("CORE_MEMORY_TEST_WEBHOOK_SECRET"),
            "{stderr}"
        );
        assert!(
            out.stdout.is_empty(),
            "nothing is admitted before the sinks open"
        );
    }
}

#[test]
fn each_sink_kind_takes_only_its_own_settings() {
    for (sink, message) in [
        (
            "[[sink]]\nid = \"host\"\nkind = \"webhook\"\nsecret_env = \"X\"\n",
            "needs `url`",
        ),
        (
            "[[sink]]\nid = \"host\"\nkind = \"webhook\"\nurl = \"https://x\"\nsecret_env = \"X\"\npath = \"x.jsonl\"\n",
            "does not take `path`",
        ),
        (
            "[[sink]]\nid = \"more\"\nkind = \"jsonl\"\npath = \"more.jsonl\"\nurl = \"https://x\"\n",
            "does not take `url`",
        ),
    ] {
        let dir = setup(&format!("\n{sink}"));
        let out = run(dir.path(), &["check"], Some(SECRET));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(
            !out.status.success() && stderr.contains(message),
            "{stderr}"
        );
    }
}

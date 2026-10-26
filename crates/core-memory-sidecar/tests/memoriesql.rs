//! A memoriesql sink in core-memory.toml: its settings and environment.
//! Delivery itself is proven by the sink's conformance suite against a
//! memoriesQL database.

#![allow(clippy::unwrap_used)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

const SINK: &str = r#"
[[sink]]
id = "memoriesql"
kind = "memoriesql"
url_env = "CORE_MEMORY_TEST_MQL_URL"
secret_env = "CORE_MEMORY_TEST_MQL_CREDENTIAL"
workspace_id = "00000000-0000-4000-8000-0000000000a5"
"#;

fn example_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../examples/quickstart")
}

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

fn run(dir: &Path, args: &[&str], env: &[(&str, &str)]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_core-memory"));
    command
        .args(args)
        .current_dir(dir)
        .env_remove("CORE_MEMORY_TEST_MQL_URL")
        .env_remove("CORE_MEMORY_TEST_MQL_CREDENTIAL");
    for (k, v) in env {
        command.env(k, v);
    }
    command.output().unwrap()
}

#[test]
fn the_sink_needs_its_settings_and_refuses_others() {
    for (edit, message) in [
        (
            ("workspace_id = ", "# workspace_id = "),
            "needs `workspace_id`",
        ),
        (
            ("url_env = ", "url = \"x\"\nurl_env = "),
            "does not take `url`",
        ),
        (
            (
                "kind = \"memoriesql\"",
                "kind = \"memoriesql\"\nsystem_of_record = true",
            ),
            "does not take `system_of_record`",
        ),
    ] {
        let dir = setup(&SINK.replace(edit.0, edit.1));
        let out = run(dir.path(), &["check"], &[]);
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(
            !out.status.success() && stderr.contains(message),
            "{stderr}"
        );
    }
}

#[test]
fn the_engine_does_not_start_without_its_environment() {
    let dir = setup(SINK);
    for (env, missing) in [
        (vec![], "CORE_MEMORY_TEST_MQL_URL"),
        (
            vec![(
                "CORE_MEMORY_TEST_MQL_URL",
                "host=127.0.0.1 dbname=memoriesql",
            )],
            "CORE_MEMORY_TEST_MQL_CREDENTIAL",
        ),
    ] {
        let out = run(dir.path(), &["ingest", "--input", "events.jsonl"], &env);
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(
            !out.status.success() && stderr.contains(missing),
            "{stderr}"
        );
        assert!(
            out.stdout.is_empty(),
            "nothing is admitted before the sinks open"
        );
    }
    let out = run(
        dir.path(),
        &["ingest", "--input", "events.jsonl"],
        &[
            ("CORE_MEMORY_TEST_MQL_URL", "postgresql://[bad"),
            ("CORE_MEMORY_TEST_MQL_CREDENTIAL", "a credential"),
        ],
    );
    assert!(String::from_utf8_lossy(&out.stderr).contains("does not parse"));
}

//! Runs the `core-memory` binary on the quickstart example, against a real
//! PostgreSQL database.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use core_memory_store_postgres::testing::TestDb;
use serde_json::Value;

fn example_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../examples/quickstart")
}

/// The example in a scratch folder, and a database with no schema yet.
struct Quickstart {
    dir: tempfile::TempDir,
    db: TestDb,
}

impl Quickstart {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        for file in ["core-memory.toml", "events.jsonl"] {
            std::fs::copy(example_dir().join(file), dir.path().join(file)).unwrap();
        }
        Self {
            dir,
            db: TestDb::create_empty(),
        }
    }

    /// Migrated, with scope `acme` enrolled in workspace `demo`.
    fn ready() -> Self {
        let q = Self::new();
        q.ok(&["migrate"]);
        q.ok(&["scope", "add", "acme", "--workspace", "demo"]);
        q
    }

    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_core-memory"))
            .args(args)
            .current_dir(self.dir.path())
            .env("CORE_MEMORY_DATABASE_URL", &self.db.url)
            .output()
            .unwrap()
    }

    /// Runs a command that must succeed and returns its JSON lines.
    fn ok(&self, args: &[&str]) -> Vec<Value> {
        let output = self.run(args);
        assert!(
            output.status.success(),
            "{args:?}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        lines(&output)
    }
}

fn lines(output: &Output) -> Vec<Value> {
    String::from_utf8(output.stdout.clone())
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn the_quickstart_stores_once_and_replays_after() {
    let q = Quickstart::new();
    assert_eq!(q.ok(&["migrate"])[0]["schema_version"], 1);
    assert_eq!(
        q.ok(&["scope", "add", "acme", "--workspace", "demo"])[0]["result"],
        "enrolled"
    );

    let outcomes = q.ok(&["ingest", "--input", "events.jsonl"]);
    assert_eq!(outcomes.len(), 4);
    assert!(
        outcomes
            .iter()
            .all(|o| o["result"]["outcome"] == "accepted"),
        "{outcomes:?}"
    );

    let records = q.ok(&["export"]);
    let events: Vec<_> = records
        .iter()
        .filter(|r| r["record_kind"] == "event")
        .collect();
    let chunks: Vec<_> = records
        .iter()
        .filter(|r| r["record_kind"] == "chunk")
        .collect();
    assert_eq!(events.len(), 4);
    assert!(chunks.len() >= 4);
    assert!(
        records
            .iter()
            .all(|r| r["contract"] == "core_memory.record.v2" && r["scope"] == "acme")
    );
    let crm = events
        .iter()
        .find(|r| r["source_system"] == "salesforce")
        .unwrap();
    assert_eq!(crm["workspace"], "demo");
    assert_eq!(crm["structure"]["record"]["action"], "closed_won");
    let crm_chunk = chunks
        .iter()
        .find(|c| c["event_id"] == crm["event_id"])
        .unwrap();
    assert_eq!(
        crm_chunk["text"],
        r#"{"Amount":48000,"Id":"006Ak000002XyZa","Name":"Northwind renewal","StageName":"Closed Won"}"#
    );
    // Every chunk belongs to an exported event, and each event's chunks
    // rebuild its length.
    for event in &events {
        let own: Vec<_> = chunks
            .iter()
            .filter(|c| c["event_id"] == event["event_id"])
            .collect();
        assert_eq!(own.len() as u64, event["chunk_count"].as_u64().unwrap());
    }

    let again = q.ok(&["ingest", "--input", "events.jsonl"]);
    assert!(again.iter().all(|o| o["result"]["outcome"] == "replay"));
    assert_eq!(
        q.ok(&["export"]).len(),
        records.len(),
        "replays store nothing"
    );

    // Events carry their workspace's change number, which is where an
    // export can be resumed.
    let seqs: Vec<u64> = events.iter().map(|e| e["seq"].as_u64().unwrap()).collect();
    assert!(seqs.windows(2).all(|pair| pair[0] < pair[1]), "{seqs:?}");
    let after = seqs[1].to_string();
    let rest = q.ok(&["export", "--workspace", "demo", "--after", &after]);
    assert_eq!(
        rest.iter().filter(|r| r["record_kind"] == "event").count(),
        2
    );
    assert_eq!(
        q.ok(&["export", "--scope", "acme", "--after", &after])
            .len(),
        rest.len()
    );
    for (args, problem) in [
        (&["export", "--after", "1"][..], "--workspace or --scope"),
        (&["export", "--scope", "elsewhere"], "not enrolled"),
        (
            &["export", "--scope", "acme", "--workspace", "other"],
            "not `other`",
        ),
    ] {
        let refused = q.run(args);
        assert!(!refused.status.success(), "{args:?}");
        assert!(
            stderr(&refused).contains(problem),
            "{args:?}: {}",
            stderr(&refused)
        );
    }
    assert_eq!(q.ok(&["export", "--workspace", "nowhere"]).len(), 0);

    let scopes = q.ok(&["scope", "list"]);
    assert_eq!(scopes.len(), 1);
    assert_eq!(scopes[0]["scope"], "acme");
    assert_eq!(scopes[0]["live_events"], 4);
}

#[test]
fn bad_lines_are_reported_and_the_rest_admitted() {
    let q = Quickstart::ready();
    let events = std::fs::read_to_string(q.dir.path().join("events.jsonl")).unwrap();
    let mut lines: Vec<&str> = events.lines().collect();
    lines.insert(1, "{not json");
    std::fs::write(q.dir.path().join("events.jsonl"), lines.join("\n")).unwrap();
    let outcomes = q.ok(&["ingest", "--input", "events.jsonl"]);
    assert_eq!(outcomes.len(), 5);
    assert_eq!(outcomes[1]["line"], 2);
    assert_eq!(outcomes[1]["result"]["reason"], "malformed");
    let accepted = outcomes
        .iter()
        .filter(|o| o["result"]["outcome"] == "accepted")
        .count();
    assert_eq!(accepted, 4);
}

#[test]
fn nothing_runs_against_a_database_that_was_not_migrated() {
    let q = Quickstart::new();
    for args in [
        &["ingest", "--input", "events.jsonl"][..],
        &["export"],
        &["scope", "list"],
        &["scope", "add", "acme", "--workspace", "demo"],
    ] {
        let output = q.run(args);
        assert!(!output.status.success(), "{args:?}");
        assert!(
            stderr(&output).contains("core-memory migrate"),
            "{args:?}: {}",
            stderr(&output)
        );
    }
    // `check` validates the config and never connects.
    assert!(!q.ok(&["check"]).is_empty());
}

#[test]
fn events_for_a_scope_that_is_not_enrolled_are_refused() {
    let q = Quickstart::new();
    q.ok(&["migrate"]);
    let outcomes = q.ok(&["ingest", "--input", "events.jsonl"]);
    assert!(
        outcomes
            .iter()
            .all(|o| o["result"]["reason"] == "scope_denied"),
        "{outcomes:?}"
    );
    assert_eq!(q.ok(&["export"]).len(), 0);
}

#[test]
fn a_scope_can_be_paused_resumed_and_revoked() {
    let q = Quickstart::ready();
    assert_eq!(q.ok(&["scope", "pause", "acme"])[0]["was"], "active");
    let paused = q.ok(&["ingest", "--input", "events.jsonl"]);
    assert!(
        paused
            .iter()
            .all(|o| o["result"]["reason"] == "scope_not_active")
    );
    assert_eq!(q.ok(&["scope", "resume", "acme"])[0]["state"], "active");
    let resumed = q.ok(&["ingest", "--input", "events.jsonl"]);
    assert!(resumed.iter().all(|o| o["result"]["outcome"] == "accepted"));

    q.ok(&["scope", "revoke", "acme"]);
    let refused = q.run(&["scope", "resume", "acme"]);
    assert!(!refused.status.success());
    assert!(
        stderr(&refused).contains("enroll it again"),
        "{}",
        stderr(&refused)
    );
    assert_eq!(
        q.ok(&["scope", "add", "acme", "--workspace", "demo"])[0]["result"],
        "reactivated"
    );
    // A scope never moves to another workspace.
    let moved = q.run(&["scope", "add", "acme", "--workspace", "other"]);
    assert!(!moved.status.success());
    assert!(
        stderr(&moved).contains("another workspace"),
        "{}",
        stderr(&moved)
    );
}

#[test]
fn erased_messages_leave_the_export_and_never_come_back() {
    let q = Quickstart::ready();
    q.ok(&["ingest", "--input", "events.jsonl"]);
    let before = q.ok(&["export"]);

    // A reason is required.
    let no_reason = q.run(&[
        "erase",
        "key",
        "--scope",
        "acme",
        "--source-ns",
        "docs/drive",
        "--external-id",
        "cutover-plan.md",
        "--revision",
        "v3",
    ]);
    assert!(!no_reason.status.success());

    let erased = q.ok(&[
        "erase",
        "--reason",
        "user_request",
        "key",
        "--scope",
        "acme",
        "--source-ns",
        "docs/drive",
        "--external-id",
        "cutover-plan.md",
        "--revision",
        "v3",
    ]);
    assert_eq!(erased[0]["result"], "erased");
    let event_id = erased[0]["event_id"].as_str().unwrap().to_string();

    let after = q.ok(&["export"]);
    assert!(after.len() < before.len());
    // The document's event and chunks are gone from the export, with
    // their text. One record says that the event was erased.
    assert!(
        after
            .iter()
            .all(|r| !r.to_string().contains("Billing cutover plan")),
        "no text of the erased document is exported"
    );
    let (erasures, kept): (Vec<_>, Vec<_>) =
        after.iter().partition(|r| r["record_kind"] == "erasure");
    assert!(kept.iter().all(|r| r["event_id"] != event_id.as_str()));
    assert_eq!(erasures.len(), 1);
    assert_eq!(erasures[0]["event_id"], event_id.as_str());
    assert_eq!(erasures[0]["record_key"], format!("erasure:{event_id}"));
    // Erasing again, by ID this time, changes nothing.
    let again = q.ok(&["erase", "--reason", "user_request", "event", &event_id]);
    assert_eq!(again[0]["result"], "already_erased");
    // Without a revision, a key names every revision of the object. None
    // is left here, which is what the command is for: it succeeds, and
    // says that it erased nothing.
    let none_left = q.ok(&[
        "erase",
        "--reason",
        "user_request",
        "key",
        "--scope",
        "acme",
        "--source-ns",
        "docs/drive",
        "--external-id",
        "cutover-plan.md",
    ]);
    assert_eq!(none_left[0]["erased_events"], 0);
    let unknown = q.run(&[
        "erase",
        "--reason",
        "user_request",
        "event",
        "00000000-0000-0000-0000-000000000000",
    ]);
    assert_eq!(unknown.status.code(), Some(3));
    assert_eq!(lines(&unknown)[0]["result"], "unknown");
    // An ID that starts with a hyphen is a value, not an option.
    let hyphen = q.run(&[
        "erase",
        "--reason",
        "user_request",
        "key",
        "--scope",
        "acme",
        "--source-ns",
        "telegram/chat",
        "--external-id",
        "-1001234567890/42",
    ]);
    assert_eq!(hyphen.status.code(), Some(0), "{}", stderr(&hyphen));
    assert_eq!(lines(&hyphen)[0]["external_id"], "-1001234567890/42");

    let outcomes = q.ok(&["ingest", "--input", "events.jsonl"]);
    let tombstoned: Vec<_> = outcomes
        .iter()
        .filter(|o| o["result"]["reason"] == "tombstoned")
        .collect();
    assert_eq!(tombstoned.len(), 1);
    assert_eq!(tombstoned[0]["external_id"], "cutover-plan.md");

    // The CRM record by its key: the one revision it has.
    let record = q.ok(&[
        "erase",
        "--reason",
        "user_request",
        "key",
        "--scope",
        "acme",
        "--source-ns",
        "salesforce/prod",
        "--external-id",
        "006Ak000002XyZaAAB",
    ]);
    assert_eq!(record[0]["erased_events"], 1);

    let rest = q.ok(&[
        "erase",
        "--reason",
        "account_closed",
        "--actor",
        "owner",
        "scope",
        "acme",
    ]);
    assert_eq!(rest[0]["erased_events"], 2);
    // Nothing is left to export but the four erasures.
    let left = q.ok(&["export"]);
    assert_eq!(left.len(), 4);
    assert!(left.iter().all(|r| r["record_kind"] == "erasure"));
    let scopes = q.ok(&["scope", "list"]);
    assert_eq!(
        (&scopes[0]["live_events"], &scopes[0]["erased_events"]),
        (&Value::from(0), &Value::from(4))
    );
}

#[test]
fn a_store_that_cannot_be_reached_is_reported_plainly() {
    let q = Quickstart::new();
    let unreachable = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_core-memory"))
            .args(args)
            .current_dir(q.dir.path())
            // Nothing listens on port 9.
            .env(
                "CORE_MEMORY_DATABASE_URL",
                "postgresql://postgres:x@127.0.0.1:9/postgres?connect_timeout=2",
            )
            .output()
            .unwrap()
    };
    // Every command says the same thing the same way: try again later.
    for args in [
        &["ingest", "--input", "events.jsonl"][..],
        &["export"],
        &["scope", "list"],
        &["erase", "--reason", "x", "scope", "acme"],
        &["migrate"],
    ] {
        let output = unreachable(args);
        assert_eq!(output.status.code(), Some(75), "{args:?}");
        assert!(
            stderr(&output).contains("unavailable"),
            "{args:?}: {}",
            stderr(&output)
        );
        assert!(
            !stderr(&output).contains(":x@"),
            "the password is not printed"
        );
    }

    // No URL at all is a mistake in the setup, not a store to wait for.
    let missing = Command::new(env!("CARGO_BIN_EXE_core-memory"))
        .args(["ingest", "--input", "events.jsonl"])
        .current_dir(q.dir.path())
        .env_remove("CORE_MEMORY_DATABASE_URL")
        .output()
        .unwrap();
    assert_eq!(missing.status.code(), Some(1));
    assert!(
        stderr(&missing).contains("CORE_MEMORY_DATABASE_URL"),
        "{}",
        stderr(&missing)
    );
}

#[test]
fn ingest_stops_at_the_first_event_the_store_cannot_take() {
    let q = Quickstart::ready();
    // A short statement limit, so a held workspace is noticed quickly.
    let config = q.dir.path().join("core-memory.toml");
    let text = std::fs::read_to_string(&config).unwrap();
    std::fs::write(
        &config,
        format!("[store]\nstatement_timeout_ms = 300\n\n{text}"),
    )
    .unwrap();

    // Another session holds the workspace, as a long erase would.
    let mut holder = q.db.connect();
    holder
        .batch_execute("BEGIN; SELECT core_memory.lock_workspace('demo')")
        .unwrap();
    let stopped = q.run(&["ingest", "--input", "events.jsonl"]);
    assert_eq!(stopped.status.code(), Some(75), "{}", stderr(&stopped));
    let outcomes = lines(&stopped);
    assert_eq!(outcomes.len(), 1, "no later line was tried: {outcomes:?}");
    assert_eq!(outcomes[0]["result"]["outcome"], "busy");
    assert!(
        stderr(&stopped).contains("\"stopped_at_line\":1"),
        "{}",
        stderr(&stopped)
    );
    // A command that governs is told the same.
    let erase = q.run(&["erase", "--reason", "x", "scope", "acme"]);
    assert_eq!(erase.status.code(), Some(75), "{}", stderr(&erase));
    holder.batch_execute("ROLLBACK").unwrap();

    // Running it again stores everything, in the file's order.
    let outcomes = q.ok(&["ingest", "--input", "events.jsonl"]);
    assert!(
        outcomes
            .iter()
            .all(|o| o["result"]["outcome"] == "accepted")
    );
    let seqs: Vec<u64> = q
        .ok(&["export"])
        .iter()
        .filter(|r| r["record_kind"] == "event")
        .map(|r| r["seq"].as_u64().unwrap())
        .collect();
    assert_eq!(seqs, [1, 3, 5, 7]);
}

#[test]
fn a_mistake_in_the_config_is_reported_without_the_database_url() {
    const URL: &str = "postgresql://engine:hunter2@db.internal/memory";
    let q = Quickstart::ready();
    let config = q.dir.path().join("core-memory.toml");
    let original = std::fs::read_to_string(&config).unwrap();
    let hash = core_memory_contract::Sha256Digest::of(b"t");
    // The URL where the syntax breaks, and where other settings belong: a
    // token's id, a listening address, a profile's id, a type's key and a
    // certificate file.
    let mistakes = [
        (format!("[store]\nurl = \"{URL}\"oops\n{original}"), "check"),
        (
            format!(
                "{original}\n[[http.token]]\nid = \"{URL}\"\nsha256 = \"{hash}\"\nscopes = [\"acme\"]\n"
            ),
            "check",
        ),
        (format!("{original}\n[http]\nlisten = \"{URL}\"\n"), "check"),
        (
            original.replace("id = \"acme.crm\"", &format!("id = \"{URL}\"")),
            "check",
        ),
        (
            original.replace("key = \"decision\"", &format!("key = \"{URL}\"")),
            "check",
        ),
        (
            original.replace(
                "\"outcome\", \"action\"",
                &format!("\"outcome\", \"{URL}\""),
            ),
            "check",
        ),
        (format!("[store]\nca_file = \"{URL}\"\n{original}"), "scope"),
    ];
    for (text, command) in mistakes {
        std::fs::write(&config, &text).unwrap();
        let args: &[&str] = if command == "scope" {
            &["scope", "list"]
        } else {
            &["check"]
        };
        let output = q.run(args);
        let said = stderr(&output);
        assert_eq!(output.status.code(), Some(1), "{said}");
        assert!(said.starts_with("core-memory: "), "{said}");
        for part in ["hunter2", "db.internal", "engine:"] {
            assert!(!said.contains(part), "{part}: {said}");
        }
    }
    // The first of them still says where.
    std::fs::write(&config, format!("[store]\nurl = \"{URL}\"oops\n{original}")).unwrap();
    assert!(stderr(&q.run(&["check"])).contains("line 2"));
}

/// `core-memory serve` on a free port, with what it has written to
/// standard error so far.
#[cfg(unix)]
struct Serving {
    child: std::process::Child,
    log: std::io::BufReader<std::process::ChildStderr>,
    addr: std::net::SocketAddr,
}

#[cfg(unix)]
impl Serving {
    fn start(q: &Quickstart, http: &str) -> Self {
        use std::io::BufRead;
        let config = q.dir.path().join("core-memory.toml");
        let text = std::fs::read_to_string(&config).unwrap();
        let hash = core_memory_contract::Sha256Digest::of(b"cm_test_token");
        std::fs::write(
            &config,
            format!(
                "{text}\n[http]\nlisten = \"127.0.0.1:0\"\n{http}\n\n[[http.token]]\nid = \"writer\"\nsha256 = \"{hash}\"\nscopes = [\"acme\"]\n"
            ),
        )
        .unwrap();
        let mut child = Command::new(env!("CARGO_BIN_EXE_core-memory"))
            .arg("serve")
            .current_dir(q.dir.path())
            .env("CORE_MEMORY_DATABASE_URL", &q.db.url)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        let mut log = std::io::BufReader::new(child.stderr.take().unwrap());
        // The server says where it listens once it does. Other lines may
        // come first.
        let mut said = String::new();
        let addr = loop {
            let mut line = String::new();
            if log.read_line(&mut line).unwrap() == 0 {
                let _ = child.wait();
                panic!("serve did not start: {said}");
            }
            said.push_str(&line);
            let listening = serde_json::from_str::<Value>(&line)
                .ok()
                .and_then(|l| l["listening"].as_str().map(str::to_string));
            if let Some(addr) = listening {
                break addr.parse().unwrap();
            }
        };
        Self { child, log, addr }
    }

    /// One request on a connection of its own, and the whole answer.
    fn ask(&self, request: &str) -> String {
        use std::io::{Read, Write};
        let mut stream = std::net::TcpStream::connect(self.addr).unwrap();
        stream.write_all(request.as_bytes()).unwrap();
        let mut answer = String::new();
        stream.read_to_string(&mut answer).unwrap();
        answer
    }

    /// Asks the server to stop, as a service manager does, and returns its
    /// exit status and the rest of what it wrote.
    fn stop(mut self) -> (std::process::ExitStatus, String) {
        use std::io::Read;
        let signalled = Command::new("kill")
            .args(["-TERM", &self.child.id().to_string()])
            .status()
            .unwrap();
        assert!(signalled.success());
        let status = self.child.wait().unwrap();
        let mut rest = String::new();
        self.log.read_to_string(&mut rest).unwrap();
        (status, rest)
    }
}

/// A test that fails before it stops the server must not leave it running.
#[cfg(unix)]
impl Drop for Serving {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(unix)]
#[test]
fn the_server_stops_cleanly_when_it_is_told_to() {
    let q = Quickstart::ready();
    let serving = Serving::start(&q, "shutdown_grace_ms = 300");
    // A request that used a store connection, and a connection that is
    // still open when the server is told to stop and outlives its grace.
    let ready = serving.ask("GET /v1/ready HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n");
    assert!(ready.starts_with("HTTP/1.1 200"), "{ready}");
    let _open = std::net::TcpStream::connect(serving.addr).unwrap();
    std::thread::sleep(std::time::Duration::from_millis(100));

    let (status, log) = serving.stop();
    assert_eq!(status.code(), Some(0), "{log}");
    assert!(!log.contains("panicked"), "{log}");
    assert!(log.contains("finishing requests"), "{log}");
}

#[cfg(unix)]
#[test]
fn the_server_says_why_it_cannot_listen() {
    let q = Quickstart::ready();
    let taken = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = taken.local_addr().unwrap().port();
    let config = q.dir.path().join("core-memory.toml");
    let text = std::fs::read_to_string(&config).unwrap();
    let hash = core_memory_contract::Sha256Digest::of(b"cm_test_token");
    std::fs::write(
        &config,
        format!(
            "{text}\n[http]\nlisten = \"127.0.0.1:{port}\"\n\n[[http.token]]\nid = \"writer\"\nsha256 = \"{hash}\"\nscopes = [\"acme\"]\n"
        ),
    )
    .unwrap();
    let output = q.run(&["serve"]);
    let said = stderr(&output);
    assert_eq!(output.status.code(), Some(1), "{said}");
    assert!(
        said.contains(&format!("cannot listen on 127.0.0.1:{port}")),
        "{said}"
    );
    assert!(!said.contains("panicked"), "{said}");
}

/// The test database's URL with another password.
fn url_with_password(url: &str, password: &str) -> String {
    let (scheme, rest) = url.split_once("://").unwrap();
    let (login, address) = rest.split_once('@').unwrap();
    let user = login.split(':').next().unwrap();
    format!("{scheme}://{user}:{password}@{address}")
}

/// Adds an `[http]` section with one token, listening on `listen`.
fn serve_on(q: &Quickstart, listen: &str, http: &str) {
    let config = q.dir.path().join("core-memory.toml");
    let text = std::fs::read_to_string(&config).unwrap();
    let hash = core_memory_contract::Sha256Digest::of(b"cm_test_token");
    std::fs::write(
        &config,
        format!(
            "{text}\n[http]\nlisten = \"{listen}\"\n{http}\n\n[[http.token]]\nid = \"writer\"\nsha256 = \"{hash}\"\nscopes = [\"acme\"]\n"
        ),
    )
    .unwrap();
}

#[test]
fn a_login_the_store_refuses_is_a_failure_and_not_a_reason_to_wait() {
    let q = Quickstart::ready();
    serve_on(&q, "127.0.0.1:0", "");
    let wrong_password = url_with_password(&q.db.url, "not-the-password");
    let no_such_database = {
        let (server, _) = q.db.url.rsplit_once('/').unwrap();
        format!("{server}/cm_t_no_such_database")
    };
    for (url, says) in [
        (&wrong_password, "password authentication failed"),
        (&no_such_database, "does not exist"),
    ] {
        for args in [
            &["migrate"][..],
            &["scope", "list"],
            &["ingest", "--input", "events.jsonl"],
            &["export"],
            &["erase", "--reason", "x", "scope", "acme"],
            &["serve"],
        ] {
            let output = Command::new(env!("CARGO_BIN_EXE_core-memory"))
                .args(args)
                .current_dir(q.dir.path())
                .env("CORE_MEMORY_DATABASE_URL", url)
                .output()
                .unwrap();
            let said = stderr(&output);
            // Running the command again would end the same way.
            assert_eq!(output.status.code(), Some(1), "{args:?}: {said}");
            assert!(
                said.contains("the store refused the connection") && said.contains(says),
                "{args:?}: {said}"
            );
            assert!(!said.contains("not-the-password"), "{said}");
        }
    }
}

#[cfg(unix)]
#[test]
fn a_command_whose_output_no_one_reads_fails_without_a_crash() {
    let q = Quickstart::ready();
    // Something to export.
    q.ok(&["ingest", "--input", "events.jsonl"]);
    for args in [
        &["schema"][..],
        &["schema", "source_event"],
        &["openapi"],
        &["check"],
        &["token"],
        &["scope", "list"],
        &["scope", "pause", "acme"],
        &["export"],
    ] {
        // A pipe whose reading end is already closed.
        let (reader, writer) = std::io::pipe().unwrap();
        drop(reader);
        let output = Command::new(env!("CARGO_BIN_EXE_core-memory"))
            .args(args)
            .current_dir(q.dir.path())
            .env("CORE_MEMORY_DATABASE_URL", &q.db.url)
            .stdout(writer)
            .output()
            .unwrap();
        let said = stderr(&output);
        assert_eq!(output.status.code(), Some(1), "{args:?}: {said}");
        assert!(
            said.starts_with("core-memory: stdout: "),
            "{args:?}: {said}"
        );
        assert!(!said.contains("panicked"), "{args:?}: {said}");
    }
    // What a command did before it could not say so stays done.
    assert_eq!(q.ok(&["scope", "list"])[0]["state"], "paused");

    // With no one reading the error either, the status still says it.
    let (out_reader, out) = std::io::pipe().unwrap();
    let (err_reader, err) = std::io::pipe().unwrap();
    drop((out_reader, err_reader));
    let status = Command::new(env!("CARGO_BIN_EXE_core-memory"))
        .arg("schema")
        .stdout(out)
        .stderr(err)
        .status()
        .unwrap();
    assert_eq!(status.code(), Some(1));
}

#[cfg(unix)]
#[test]
fn the_server_runs_and_stops_cleanly_with_no_one_reading_its_log() {
    use std::io::{Read, Write};
    use std::time::{Duration, Instant};

    /// Ends the server if the test fails before it has.
    struct Running(std::process::Child);
    impl Drop for Running {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    let q = Quickstart::ready();
    // A port that was free a moment ago. The server cannot say which one it
    // took, because nothing reads what it says.
    let port = {
        let free = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        free.local_addr().unwrap().port()
    };
    serve_on(&q, &format!("127.0.0.1:{port}"), "shutdown_grace_ms = 300");
    let (reader, log) = std::io::pipe().unwrap();
    drop(reader);
    let mut server = Running(
        Command::new(env!("CARGO_BIN_EXE_core-memory"))
            .arg("serve")
            .current_dir(q.dir.path())
            .env("CORE_MEMORY_DATABASE_URL", &q.db.url)
            .stdout(std::process::Stdio::null())
            .stderr(log)
            .spawn()
            .unwrap(),
    );
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut stream = loop {
        if let Ok(stream) = std::net::TcpStream::connect(("127.0.0.1", port)) {
            break stream;
        }
        let ended = server.0.try_wait().unwrap();
        assert!(ended.is_none(), "the server ended: {ended:?}");
        assert!(Instant::now() < deadline, "the server never listened");
        std::thread::sleep(Duration::from_millis(50));
    };
    // It said where it listens to no one, and went on to serve.
    stream
        .write_all(b"GET /v1/ready HTTP/1.1\r\nHost: x\r\n\r\n")
        .unwrap();
    let mut answer = String::new();
    stream.read_to_string(&mut answer).unwrap();
    assert!(answer.starts_with("HTTP/1.1 200"), "{answer}");

    let signalled = Command::new("kill")
        .args(["-TERM", &server.0.id().to_string()])
        .status()
        .unwrap();
    assert!(signalled.success());
    assert_eq!(server.0.wait().unwrap().code(), Some(0));
}

// ---- reading with a model ----

/// A stand-in for a model server that speaks the chat completions API, on
/// a local port. For every chunk it is shown it answers with the first
/// listed type and marks the chunk's first word as an entity.
struct StandIn {
    addr: std::net::SocketAddr,
    requests: std::sync::Arc<std::sync::Mutex<Vec<Value>>>,
    /// The status it answers with instead, when not 0.
    failing: std::sync::Arc<std::sync::atomic::AtomicU16>,
}

impl StandIn {
    fn start() -> Self {
        use std::io::{BufRead, Read, Write};
        use std::sync::atomic::Ordering;

        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let requests = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let failing = std::sync::Arc::new(std::sync::atomic::AtomicU16::new(0));
        let (seen, status) = (requests.clone(), failing.clone());
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let mut reader = std::io::BufReader::new(stream.try_clone().unwrap());
                let mut length = 0usize;
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).unwrap_or(0) == 0 || line.trim().is_empty() {
                        break;
                    }
                    if let Some((name, value)) = line.split_once(':')
                        && name.eq_ignore_ascii_case("content-length")
                    {
                        length = value.trim().parse().unwrap_or(0);
                    }
                }
                let mut body = vec![0u8; length];
                if reader.read_exact(&mut body).is_err() {
                    continue;
                }
                let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                let (line, answer) = match status.load(Ordering::SeqCst) {
                    0 => ("200 OK".to_string(), completion(&request)),
                    code => (
                        format!("{code} Stand-in"),
                        serde_json::json!({"error": {"message": "the stand-in was told to fail"}}),
                    ),
                };
                seen.lock().unwrap().push(request);
                let answer = answer.to_string();
                let _ = write!(
                    stream,
                    "HTTP/1.1 {line}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{answer}",
                    answer.len()
                );
            }
        });
        Self {
            addr,
            requests,
            failing,
        }
    }

    fn requests(&self) -> Vec<Value> {
        self.requests.lock().unwrap().clone()
    }

    fn fail_with(&self, status: u16) {
        self.failing
            .store(status, std::sync::atomic::Ordering::SeqCst);
    }

    /// The config lines for a reader that calls this server.
    fn config(&self, reader: &str) -> String {
        format!(
            "\n[reader]\nbinding = \"local\"\n{reader}\n\n[[binding]]\nid = \"local\"\nprovider = \"openai-compatible\"\nendpoint = \"http://{}/v1\"\nmodel = \"stand-in-1\"\nmax_output_tokens = 512\ntimeout_ms = 5000\n",
            self.addr
        )
    }
}

/// A chat completion that reads every chunk the request shows.
fn completion(request: &Value) -> Value {
    let shown: Value = request["messages"][1]["content"]
        .as_str()
        .and_then(|user| serde_json::from_str(user.lines().next().unwrap_or_default()).ok())
        .unwrap_or(Value::Null);
    let first_type = shown["types"][0]["key"].clone();
    let items: Vec<Value> = shown["chunks"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .map(|chunk| {
            let first_word = chunk["text"]
                .as_str()
                .and_then(|text| text.split_whitespace().next())
                .unwrap_or_default();
            serde_json::json!({
                "chunk": chunk["chunk"],
                "type": first_type,
                "entities": [{"text": first_word, "kind": "other"}],
                "actions": [],
                "outcomes": [],
            })
        })
        .collect();
    serde_json::json!({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": serde_json::json!({"items": items}).to_string(),
                "refusal": null,
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 400, "completion_tokens": 60},
    })
}

impl Quickstart {
    /// Adds lines to the config file.
    fn configure(&self, more: &str) {
        let config = self.dir.path().join("core-memory.toml");
        let text = std::fs::read_to_string(&config).unwrap();
        std::fs::write(&config, format!("{text}{more}")).unwrap();
    }
}

fn of_kind<'a>(records: &'a [Value], kind: &str) -> Vec<&'a Value> {
    records
        .iter()
        .filter(|r| r["record_kind"] == kind)
        .collect()
}

#[test]
fn chunks_are_read_once_their_scope_allows_the_model() {
    let model = StandIn::start();
    let destination = model.addr.to_string();
    let q = Quickstart::ready();
    q.configure(&model.config("batch_size = 3"));

    // The config names the binding, and where it would send text.
    let checked = q.ok(&["check"]);
    assert!(
        checked
            .iter()
            .any(|l| l["binding"] == "local" && l["destination"] == destination.as_str()),
        "{checked:?}"
    );
    assert!(checked.iter().any(|l| l["reader"]["binding"] == "local"));

    // Admission queues every chunk, and nothing is sent yet.
    q.ok(&["ingest", "--input", "events.jsonl"]);
    let stored = q.ok(&["export"]);
    let chunks = of_kind(&stored, "chunk");
    assert_eq!(q.ok(&["read", "status"])[0]["ready"], chunks.len());
    assert!(of_kind(&stored, "chunk_read").is_empty());

    // Without the scope's consent no text leaves.
    let put_off = q.ok(&["read", "run"]);
    assert!(!put_off.is_empty());
    assert!(
        put_off
            .iter()
            .all(|b| b["read"]["problem"] == "no_consent" && b["read"]["calls"] == 0),
        "{put_off:?}"
    );
    assert!(model.requests().is_empty());

    // Consent is to the binding's destination, and takes effect at once.
    let allowed = q.ok(&["scope", "allow", "acme", "--binding", "local"]);
    assert_eq!(
        (&allowed[0]["destination"], &allowed[0]["changed"]),
        (&Value::from(destination.as_str()), &Value::from(true))
    );
    assert_eq!(
        q.ok(&["scope", "list"])[0]["allowed"],
        serde_json::json!([destination])
    );
    let batches = q.ok(&["read", "run"]);
    let read: u64 = batches
        .iter()
        .map(|b| b["read"]["stored"].as_u64().unwrap())
        .sum();
    assert_eq!(read, chunks.len() as u64, "{batches:?}");
    assert!(batches.iter().all(|b| b["read"]["problem"].is_null()));
    // One call for each batch of at most three chunks of one profile.
    let requests = model.requests();
    assert_eq!(requests.len(), batches.len());
    assert!(requests.iter().all(|r| r["model"] == "stand-in-1"));

    // What was read is exported after the chunks: a type from the
    // vocabulary and ranges of the chunk's own text. No text of the model's.
    let records = q.ok(&["export"]);
    let reads = of_kind(&records, "chunk_read");
    assert_eq!(reads.len(), chunks.len());
    for read in &reads {
        let chunk = chunks
            .iter()
            .find(|c| c["chunk_id"] == read["chunk_id"])
            .unwrap();
        let text = chunk["text"].as_str().unwrap();
        let span = &read["spans"][0];
        let (start, end) = (
            span["byte_start"].as_u64().unwrap() as usize,
            span["byte_end"].as_u64().unwrap() as usize,
        );
        assert_eq!(
            Some(&text[start..end]),
            text.split_whitespace().next(),
            "{read}"
        );
        assert_eq!(span["kind"], "entity");
        assert!(read["type"]["key"].is_string(), "{read}");
        assert_eq!(read["reader"]["binding_id"], "local");
        assert!(read.get("text").is_none() && span.get("text").is_none());
    }
    assert_eq!(q.ok(&["read", "status"])[0]["ready"], 0);
    // Nothing is due, so nothing is sent again.
    assert!(q.ok(&["read", "run"]).is_empty());
    assert_eq!(model.requests().len(), requests.len());

    // Erasing a message removes what was read in it.
    let erased = reads[0]["chunk_id"].clone();
    let event_id = chunks.iter().find(|c| c["chunk_id"] == erased).unwrap()["event_id"]
        .as_str()
        .unwrap()
        .to_string();
    q.ok(&["erase", "--reason", "user_request", "event", &event_id]);
    let after = q.ok(&["export"]);
    assert!(
        of_kind(&after, "chunk_read")
            .iter()
            .all(|r| r["chunk_id"] != erased)
    );
    assert_eq!(of_kind(&after, "erasure").len(), 1);

    // Consent withdrawn, the next chunk is not sent.
    let withdrawn = q.ok(&["scope", "disallow", "acme", "--destination", &destination]);
    assert_eq!(withdrawn[0]["changed"], true);
    std::fs::write(
        q.dir.path().join("more.jsonl"),
        r#"{"scope":"acme","source_ns":"slack/T01","external_id":"later","profile_id":"core.chat_transcript","media_type":"text/plain","content":{"text":"Written after consent was withdrawn."},"parser_contract_version":"1"}"#,
    )
    .unwrap();
    q.ok(&["ingest", "--input", "more.jsonl"]);
    let refused = q.ok(&["read", "run"]);
    assert_eq!(refused[0]["read"]["problem"], "no_consent");
    assert_eq!(model.requests().len(), requests.len());
}

#[test]
fn what_was_stored_before_a_reader_is_queued_and_failed_reads_are_retried() {
    let model = StandIn::start();
    let q = Quickstart::ready();
    // No reader yet: nothing is queued, and reading is refused by name.
    q.ok(&["ingest", "--input", "events.jsonl"]);
    let none = q.run(&["read", "run"]);
    assert_eq!(none.status.code(), Some(1));
    assert!(stderr(&none).contains("[reader]"), "{}", stderr(&none));

    q.configure(&model.config("max_attempts = 1"));
    q.ok(&["scope", "allow", "acme", "--binding", "local"]);
    assert_eq!(q.ok(&["read", "status"])[0]["ready"], 0);
    assert!(q.ok(&["read", "run"]).is_empty());
    let chunks = of_kind(&q.ok(&["export"]), "chunk").len();
    assert_eq!(q.ok(&["read", "queue", "acme"])[0]["queued"], chunks);
    assert_eq!(q.ok(&["read", "queue", "acme"])[0]["queued"], 0);

    // The provider turns the binding down. With one attempt allowed, the
    // reads are set aside for an operator.
    model.fail_with(401);
    let failed = q.ok(&["read", "run"]);
    assert!(
        failed
            .iter()
            .all(|b| b["read"]["problem"] == "binding_rejected" && b["read"]["stored"] == 0),
        "{failed:?}"
    );
    let status = &q.ok(&["read", "status"])[0];
    assert_eq!(
        (&status["failed"], &status["problems"]["binding_rejected"]),
        (&Value::from(chunks), &Value::from(chunks))
    );
    assert!(q.ok(&["read", "run"]).is_empty());

    // Fixed, and told to try again.
    model.fail_with(0);
    assert_eq!(q.ok(&["read", "retry"])[0]["retried"], chunks);
    let batches = q.ok(&["read", "run"]);
    let read: u64 = batches
        .iter()
        .map(|b| b["read"]["stored"].as_u64().unwrap())
        .sum();
    assert_eq!(read, chunks as u64);
    assert_eq!(of_kind(&q.ok(&["export"]), "chunk_read").len(), chunks);

    // Read with one reader, the chunks are queued again only on request,
    // and only when the reader differs.
    assert_eq!(q.ok(&["read", "queue", "acme", "--reread"])[0]["queued"], 0);
    let config = q.dir.path().join("core-memory.toml");
    let text = std::fs::read_to_string(&config).unwrap();
    std::fs::write(&config, text.replace("stand-in-1", "stand-in-2")).unwrap();
    assert_eq!(q.ok(&["read", "queue", "acme"])[0]["queued"], 0);
    assert_eq!(
        q.ok(&["read", "queue", "acme", "--reread"])[0]["queued"],
        chunks
    );

    // A scope that is not enrolled, and one that is paused.
    let unknown = q.run(&["read", "queue", "nowhere"]);
    assert_eq!(unknown.status.code(), Some(1));
    assert!(stderr(&unknown).contains("not enrolled"));
    q.ok(&["scope", "pause", "acme"]);
    let paused = q.run(&["read", "queue", "acme"]);
    assert_eq!(paused.status.code(), Some(1));
    assert!(stderr(&paused).contains("paused"), "{}", stderr(&paused));
}

#[cfg(unix)]
#[test]
fn the_service_reads_what_it_admits_and_still_stops_cleanly() {
    use std::io::{Read, Write};

    let model = StandIn::start();
    let q = Quickstart::ready();
    q.configure(&model.config(""));
    q.ok(&["scope", "allow", "acme", "--binding", "local"]);
    let serving = Serving::start(&q, "shutdown_grace_ms = 2000");

    let body = r#"{"scope":"acme","source_ns":"slack/T01","external_id":"served","profile_id":"core.chat_transcript","media_type":"text/plain","content":{"text":"Priya shipped the export on Friday."},"parser_contract_version":"1"}"#;
    let mut stream = std::net::TcpStream::connect(serving.addr).unwrap();
    write!(
        stream,
        "POST /v1/events HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer cm_test_token\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
    .unwrap();
    let mut answer = String::new();
    stream.read_to_string(&mut answer).unwrap();
    assert!(answer.starts_with("HTTP/1.1 200"), "{answer}");

    // The reader beside the server picks the chunk up within a few seconds.
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
    let reads = loop {
        let reads = of_kind(&q.ok(&["export"]), "chunk_read").len();
        if reads > 0 || std::time::Instant::now() > deadline {
            break reads;
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    };
    assert_eq!(reads, 1);
    assert_eq!(model.requests().len(), 1);

    let (status, log) = serving.stop();
    assert_eq!(status.code(), Some(0), "{log}");
    assert!(!log.contains("panicked"), "{log}");
    // The log says a chunk was read, and nothing of what it said.
    assert!(log.contains("\"stored\":1"), "{log}");
    assert!(!log.contains("Priya"), "{log}");
}

//! Runs the `core-memory` binary on the quickstart example, against a real
//! PostgreSQL database.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use core_memory_store_postgres::testing::TestDb;
use serde_json::{Value, json};

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
/// listed type and marks the chunk's first word as an entity. Of a tool
/// call it says what the tool was and how its output ends. As a judge it
/// goes by a few words in the memories it is shown: see [`judgment`].
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

    /// The config lines for a binding that calls this server.
    fn binding(&self) -> String {
        format!(
            "\n[[binding]]\nid = \"local\"\nprovider = \"openai-compatible\"\nendpoint = \"http://{}/v1\"\nmodel = \"stand-in-1\"\nmax_output_tokens = 512\ntimeout_ms = 5000\n",
            self.addr
        )
    }

    /// The config lines for a reader of chunks that calls this server.
    fn config(&self, reader: &str) -> String {
        format!(
            "\n[reader]\nbinding = \"local\"\n{reader}\n{}",
            self.binding()
        )
    }

    /// The config lines for readers of chunks and of tool calls that both
    /// call this server.
    fn config_with_tools(&self) -> String {
        format!(
            "\n[reader]\nbinding = \"local\"\n\n[tool_reader]\nbinding = \"local\"\n{}",
            self.binding()
        )
    }

    /// The config lines for a reader of chunks and a judge that both call
    /// this server. A new bead is judged as soon as its chunk was read.
    fn config_with_judge(&self, judge: &str) -> String {
        format!(
            "\n[reader]\nbinding = \"local\"\n\n[judge]\nbinding = \"local\"\ndebounce_ms = 0\n{judge}\n{}",
            self.binding()
        )
    }
}

/// What the stand-in says as a judge, of the memories `listing` shows. It
/// goes by words in their text, whichever of two memories is the new one:
///
/// - the message that agrees follows from the one that decided;
/// - a message that changes the plan takes the place of the decision,
///   while the decision is current;
/// - two memories about the big bang cutover are parts of one;
/// - the billing migration and the billing cutover are one project under
///   two names.
fn judgment(listing: &Value) -> Value {
    let text = |memory: &Value| memory["text"].as_str().unwrap_or_default().to_string();
    let listed = listing["listed"].as_array().cloned().unwrap_or_default();
    let new = listing["new"].as_array().cloned().unwrap_or_default();
    let mut names = Vec::new();
    let mut entries = Vec::new();
    for (n, memory) in new.iter().enumerate() {
        let mine = text(memory);
        let mut related = Vec::new();
        for other in listed.iter().chain(&new[..n]) {
            let theirs = text(other);
            let says = |new: &str, other: &str| mine.contains(new) && theirs.contains(other);
            let current = memory["current"] != false && other["current"] != false;
            // `>` runs from the other memory to the new one, `<` from the
            // new one to the other.
            let rel = if says("Agreed", "phased cutover") {
                Some(("LEADSTO>", None))
            } else if says("phased cutover", "Agreed") {
                Some(("LEADSTO<", None))
            } else if says("Change of plan:", "phased cutover") && current {
                Some(("SUPERSEDES<", Some("supersede")))
            } else if says("phased cutover", "Change of plan:") && current {
                Some(("SUPERSEDES>", Some("supersede")))
            } else if says("big bang cutover", "big bang cutover") {
                Some(("CONTAINS", None))
            } else {
                None
            };
            match rel {
                Some((rel, Some(label))) => {
                    related.push(serde_json::json!({"to": other["id"], "rel": rel, "label": label}))
                }
                Some((rel, None)) => {
                    related.push(serde_json::json!({"to": other["id"], "rel": rel}));
                }
                None => {}
            }
            for (mine, theirs) in [
                ("Billing migration", "Billing cutover"),
                ("Billing cutover", "Billing migration"),
            ] {
                if says(mine, theirs) {
                    names.push(serde_json::json!({
                        "a": memory["id"], "a_name": mine,
                        "b": other["id"], "b_name": theirs,
                    }));
                }
            }
        }
        entries.push(serde_json::json!({"id": memory["id"], "related": related, "unsure": []}));
    }
    serde_json::json!({"new": entries, "names": names})
}

/// A chat completion that reads what the request shows: every chunk of a
/// batch, one tool call, or the memories a judge is to compare.
fn completion(request: &Value) -> Value {
    let user = request["messages"][1]["content"]
        .as_str()
        .unwrap_or_default();
    // A judge is shown one JSON document of several lines, and after it,
    // when its last reply was turned down, what was wrong with that reply.
    let listing: Value = serde_json::from_str(
        user.split("\n\nYour previous reply")
            .next()
            .unwrap_or_default(),
    )
    .unwrap_or(Value::Null);
    if listing["new"].is_array() {
        return serde_json::json!({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": judgment(&listing).to_string(),
                    "refusal": null,
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 900, "completion_tokens": 70},
        });
    }
    let shown: Value =
        serde_json::from_str(user.lines().next().unwrap_or_default()).unwrap_or(Value::Null);
    if let Some(tool) = shown["tool"].as_str() {
        let last_line = shown["output"]
            .as_str()
            .and_then(|output| output.lines().last())
            .unwrap_or_default();
        let reading = serde_json::json!({
            "action": format!("Called {tool}"),
            "outcome": format!("Its output ends: {last_line}"),
            "status": if shown["tool_reported_error"] == true { "failed" } else { "succeeded" },
        });
        return serde_json::json!({
            "choices": [{
                "message": {"role": "assistant", "content": reading.to_string(), "refusal": null},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 700, "completion_tokens": 40},
        });
    }
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

#[test]
fn tool_calls_are_read_whole_and_what_was_read_is_exported() {
    let model = StandIn::start();
    let q = Quickstart::ready();
    q.configure(&model.config_with_tools());
    let checked = q.ok(&["check"]);
    assert!(
        checked
            .iter()
            .any(|l| l["tool_reader"]["binding"] == "local"),
        "{checked:?}"
    );

    // One message and one call of a tool with a long, repetitive output.
    let output: Vec<String> = (1..=300)
        .map(|i| format!("test case_{i} ... ok"))
        .chain(["test result: FAILED. 300 passed; 2 failed".to_string()])
        .collect();
    let message = serde_json::json!({
        "scope": "acme", "source_ns": "agent/s1", "external_id": "msg_1",
        "profile_id": "core.chat_transcript", "media_type": "text/plain",
        "actor": {"id": "agent-1", "kind": "agent", "role": "assistant"},
        "content": {"text": "Running the tests before I change the exporter."},
        "parser_contract_version": "agent-log/1"
    });
    let call = serde_json::json!({
        "scope": "acme", "source_ns": "agent/s1", "external_id": "toolu_01",
        "profile_id": "core.tool_call", "media_type": "application/json",
        "content": {"tool_call": {
            "tool": "Bash",
            "input": {"command": "cargo test -p exporter"},
            "output": output.join("\n"),
            "is_error": true
        }},
        "structure": {"parent": {"external_id": "msg_1"}},
        "parser_contract_version": "agent-log/1"
    });
    std::fs::write(
        q.dir.path().join("session.jsonl"),
        format!("{message}\n{call}\n"),
    )
    .unwrap();
    let admitted = q.ok(&["ingest", "--input", "session.jsonl"]);
    assert!(
        admitted
            .iter()
            .all(|line| line["result"]["outcome"] == "accepted"),
        "{admitted:?}"
    );
    let call_id = admitted[1]["result"]["event_id"]
        .as_str()
        .unwrap()
        .to_string();

    // One line for each kind of work: the message's chunk, and the call.
    let status = q.ok(&["read", "status"]);
    assert_eq!(
        (&status[0]["work"], &status[0]["ready"]),
        (&Value::from("chunk_read"), &Value::from(1))
    );
    assert_eq!(
        (&status[1]["work"], &status[1]["ready"]),
        (&Value::from("tool_read"), &Value::from(1))
    );

    // Nothing is sent until the scope allows the model.
    let put_off = q.ok(&["read", "run"]);
    assert_eq!(put_off.len(), 2, "{put_off:?}");
    assert!(put_off.iter().all(|r| r["read"]["problem"] == "no_consent"));
    assert!(model.requests().is_empty());
    q.ok(&["scope", "allow", "acme", "--binding", "local"]);

    // Both kinds are read by one run: the chunk in a batch, the call alone.
    let rounds = q.ok(&["read", "run"]);
    assert_eq!(rounds.len(), 2, "{rounds:?}");
    assert!(rounds.iter().all(|r| r["read"]["problem"].is_null()));
    let read: Vec<(u64, u64)> = rounds
        .iter()
        .map(|r| {
            (
                r["read"]["chunks"].as_u64().unwrap(),
                r["read"]["tool_calls"].as_u64().unwrap(),
            )
        })
        .collect();
    assert!(read.contains(&(1, 0)) && read.contains(&(0, 1)), "{read:?}");

    // The model was shown the call with the repetition taken out, and
    // none of the 300 passing lines but the first.
    let shown: Vec<Value> = model
        .requests()
        .iter()
        .filter_map(|request| {
            serde_json::from_str::<Value>(request["messages"][1]["content"].as_str()?).ok()
        })
        .filter(|shown| shown["tool"] == "Bash")
        .collect();
    assert_eq!(shown.len(), 1);
    let output_shown = shown[0]["output"].as_str().unwrap();
    assert!(
        output_shown.contains("[... 299 more lines like the one above]"),
        "{output_shown}"
    );
    assert!(!output_shown.contains("case_17"), "{output_shown}");

    // What was read is exported after the event it is about.
    let records = q.ok(&["export"]);
    let outcomes = of_kind(&records, "tool_outcome");
    assert_eq!(outcomes.len(), 1);
    let outcome = outcomes[0];
    assert_eq!(outcome["event_id"], call_id.as_str());
    assert_eq!(outcome["bead_id"], call_id.as_str());
    assert_eq!(outcome["status"], "failed");
    assert_eq!(outcome["action"], "Called Bash");
    assert_eq!(
        outcome["outcome"],
        "Its output ends: test result: FAILED. 300 passed; 2 failed"
    );
    assert_eq!(outcome["compressor"]["id"], "tool.lines");
    assert_eq!(outcome["reader"]["binding_id"], "local");
    assert_eq!(of_kind(&records, "chunk_read").len(), 1);
    let event = records
        .iter()
        .find(|r| r["record_kind"] == "event" && r["event_id"] == call_id.as_str())
        .unwrap();
    assert_eq!(event["content_kind"], "tool_call");
    assert_eq!(event["tool_call"]["tool"], "Bash");
    assert!(event["seq"].as_u64() < outcome["seq"].as_u64());
    let status = q.ok(&["read", "status"]);
    assert!(status.iter().all(|line| line["ready"] == 0), "{status:?}");

    // Erasing the call takes what was read in it out of the export.
    q.ok(&["erase", "--reason", "user_request", "event", &call_id]);
    let after = q.ok(&["export"]);
    assert!(of_kind(&after, "tool_outcome").is_empty());
    assert_eq!(of_kind(&after, "chunk_read").len(), 1);
    // Nothing is left to queue: the message was read, the call is gone.
    let queued = q.ok(&["read", "queue", "acme"]);
    assert_eq!(
        queued
            .iter()
            .map(|line| (
                line["work"].as_str().unwrap(),
                line["queued"].as_u64().unwrap()
            ))
            .collect::<Vec<_>>(),
        [("chunk_read", 0), ("tool_read", 0)]
    );
}

// ---- judging ----

/// The judged lines among what `read run` printed.
fn judged(rounds: &[Value]) -> Vec<&Value> {
    rounds
        .iter()
        .filter_map(|line| line.get("judged"))
        .collect()
}

/// The sum of one count over the judge's rounds.
fn sum(rounds: &[&Value], count: &str) -> u64 {
    rounds
        .iter()
        .map(|round| round[count].as_u64().unwrap())
        .sum()
}

/// The queue's line for one kind of work.
fn queue_of<'a>(lines: &'a [Value], work: &str) -> &'a Value {
    lines
        .iter()
        .find(|line| line["work"] == work)
        .unwrap_or_else(|| panic!("no line for {work}: {lines:?}"))
}

#[test]
fn new_beads_are_judged_and_what_the_judge_said_is_exported() {
    let model = StandIn::start();
    let destination = model.addr.to_string();
    let q = Quickstart::ready();
    q.configure(&model.config_with_judge(""));

    // The config names the judge, where it would send text, and whether a
    // label that changes what is current is read twice.
    let checked = q.ok(&["check"]);
    let judge = checked
        .iter()
        .find_map(|line| line.get("judge"))
        .unwrap_or_else(|| panic!("{checked:?}"));
    assert_eq!(
        (
            &judge["binding"],
            &judge["destination"],
            &judge["second_read"]
        ),
        (
            &Value::from("local"),
            &Value::from(destination.as_str()),
            &Value::from(true)
        )
    );

    // Admission queues every bead for the judge. There are four kinds of
    // work, and a line for each.
    q.ok(&["ingest", "--input", "events.jsonl"]);
    let status = q.ok(&["read", "status"]);
    assert_eq!(
        status.iter().map(|line| &line["work"]).collect::<Vec<_>>(),
        ["chunk_read", "tool_read", "judge", "second_read"]
    );
    assert_eq!(queue_of(&status, "judge")["ready"], 4);

    // Without the scope's consent its chunks are not read, and a bead
    // waits for its chunk to be read: the judge is shown its type. So
    // nothing is shown to the judge either.
    let put_off = q.ok(&["read", "run"]);
    assert!(
        !put_off.is_empty() && judged(&put_off).is_empty(),
        "{put_off:?}"
    );
    assert!(
        put_off
            .iter()
            .all(|round| round["read"]["problem"] == "no_consent"),
        "{put_off:?}"
    );
    assert!(model.requests().is_empty());
    assert!(of_kind(&q.ok(&["export"]), "verdict").is_empty());

    // With it, the chunks are read and then their beads are judged: every
    // pair once, whichever of the two the judge was handed first.
    q.ok(&["scope", "allow", "acme", "--binding", "local"]);
    // Work that was put off is due again after a while. It is told to be
    // due now.
    q.db.connect()
        .batch_execute("UPDATE core_memory.work SET not_before = now() - interval '1 second'")
        .unwrap();
    let rounds = q.ok(&["read", "run"]);
    let read = judged(&rounds);
    assert!(
        read.iter().all(|round| round["problem"].is_null()),
        "{rounds:?}"
    );
    assert_eq!(
        (
            sum(&read, "judged"),
            sum(&read, "verdicts"),
            sum(&read, "edges"),
            sum(&read, "names")
        ),
        (4, 6, 1, 1),
        "{rounds:?}"
    );
    assert!(read.iter().all(|round| round["workspace"] == "demo"));
    let status = q.ok(&["read", "status"]);
    assert_eq!(queue_of(&status, "judge")["ready"], 0);

    // What the judge said is exported after what it is about: a verdict
    // for every pair, the one relationship it saw, and the two places
    // where one name stands. Nothing of it is text.
    let records = q.ok(&["export"]);
    let chunks = of_kind(&records, "chunk");
    let verdicts = of_kind(&records, "verdict");
    assert_eq!(verdicts.len(), 6);
    let text_of = |bead: &Value| {
        chunks
            .iter()
            .find(|chunk| chunk["chunk_id"] == *bead)
            .map(|chunk| chunk["text"].as_str().unwrap().to_string())
            .unwrap_or_else(|| panic!("no chunk {bead}"))
    };
    for verdict in &verdicts {
        assert_eq!(verdict["judge"]["binding_id"], "local");
        assert!(verdict.get("scope").is_none() && verdict.get("text").is_none());
    }
    let related: Vec<_> = verdicts
        .iter()
        .filter(|verdict| verdict["verdict"] != "none")
        .collect();
    assert_eq!(related.len(), 1, "{verdicts:?}");
    assert_eq!(
        (&related[0]["verdict"], &related[0]["direction"]),
        (&Value::from("leadsto"), &Value::from("earlier_to_later"))
    );
    let edges = of_kind(&records, "edge");
    assert_eq!(edges.len(), 1);
    assert_eq!(edges[0]["edge_type"], "leadsto");
    assert!(text_of(&edges[0]["from_bead"]).contains("phased cutover"));
    assert!(text_of(&edges[0]["to_bead"]).starts_with("Agreed"));
    let links = of_kind(&records, "name_link");
    assert_eq!(links.len(), 1);
    let mut linked: Vec<String> = [
        ("a_bead", "a_start", "a_end"),
        ("b_bead", "b_start", "b_end"),
    ]
    .iter()
    .map(|(bead, start, end)| {
        let text = text_of(&links[0][*bead]);
        let (start, end) = (
            links[0][*start].as_u64().unwrap() as usize,
            links[0][*end].as_u64().unwrap() as usize,
        );
        text[start..end].to_string()
    })
    .collect();
    linked.sort();
    assert_eq!(linked, ["Billing cutover", "Billing migration"]);
    for line in records.iter().filter(|r| {
        matches!(
            r["record_kind"].as_str(),
            Some("verdict" | "edge" | "name_link")
        )
    }) {
        let line = line.to_string();
        assert!(
            !line.contains("cutover") && !line.contains("Billing"),
            "{line}"
        );
    }
    // Nothing is due, so nothing is sent again.
    let sent = model.requests().len();
    assert!(q.ok(&["read", "run"]).is_empty());
    assert_eq!(model.requests().len(), sent);

    // A message that changes the plan, and one more about the new plan.
    // The judge says the first takes the decision's place. That changes
    // what is current, so it is held, read a second time on its own, and
    // written when the second read agrees.
    std::fs::write(
        q.dir.path().join("more.jsonl"),
        concat!(
            r#"{"scope":"acme","source_ns":"slack/T01","external_id":"C042/1730000009.000900","profile_id":"core.chat_transcript","occurred_at":{"at":"2026-10-25T10:00:00Z","precision":"second"},"media_type":"text/plain","content":{"text":"Change of plan: big bang cutover on November 3 after all."},"parser_contract_version":"slack-export/1"}"#,
            "\n",
            r#"{"scope":"acme","source_ns":"slack/T01","external_id":"C042/1730000010.001000","profile_id":"core.chat_transcript","occurred_at":{"at":"2026-10-25T10:05:00Z","precision":"second"},"media_type":"text/plain","content":{"text":"The big bang cutover needs a write freeze from Friday."},"parser_contract_version":"slack-export/1"}"#,
            "\n",
        ),
    )
    .unwrap();
    q.ok(&["ingest", "--input", "more.jsonl"]);
    let rounds = q.ok(&["read", "run"]);
    let read = judged(&rounds);
    assert!(
        read.iter().all(|round| round["problem"].is_null()),
        "{rounds:?}"
    );
    let (first, second): (Vec<&Value>, Vec<&Value>) =
        read.iter().partition(|round| round["second_read"] == false);
    assert_eq!(
        (
            sum(&first, "judged"),
            sum(&first, "verdicts"),
            sum(&first, "held"),
            sum(&first, "groups")
        ),
        (2, 9, 1, 1),
        "{rounds:?}"
    );
    assert_eq!(sum(&first, "labels"), 0);
    assert_eq!(
        (
            second.len(),
            sum(&second, "verdicts"),
            sum(&second, "labels")
        ),
        (1, 1, 1),
        "{rounds:?}"
    );
    // The second read was shown the two statements and nothing else.
    let requests = model.requests();
    let last: Value = serde_json::from_str(
        requests.last().unwrap()["messages"][1]["content"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(
        (
            last["listed"].as_array().unwrap().len(),
            last["new"].as_array().unwrap().len()
        ),
        (1, 1)
    );

    let records = q.ok(&["export"]);
    assert_eq!(of_kind(&records, "verdict").len(), 16);
    let made = of_kind(&records, "bead");
    let label = made
        .iter()
        .find(|bead| bead["bead_kind"] == "label")
        .unwrap_or_else(|| panic!("{made:?}"));
    assert_eq!(label["label"], "supersede");
    let chunks = of_kind(&records, "chunk");
    let text_of = |bead: &Value| {
        chunks
            .iter()
            .find(|chunk| chunk["chunk_id"] == *bead)
            .map(|chunk| chunk["text"].as_str().unwrap().to_string())
            .unwrap_or_else(|| panic!("no chunk {bead}"))
    };
    assert!(text_of(&label["source"]).starts_with("Change of plan"));
    let edges = of_kind(&records, "edge");
    let follows: Vec<_> = edges
        .iter()
        .filter(|edge| edge["edge_type"] == "supersedes")
        .collect();
    assert_eq!(follows.len(), 1);
    assert_eq!(follows[0]["from_bead"], label["bead_id"]);
    assert!(text_of(&follows[0]["to_bead"]).contains("phased cutover"));
    let group = made
        .iter()
        .find(|bead| bead["bead_kind"] == "group")
        .unwrap_or_else(|| panic!("{made:?}"));
    let members: Vec<String> = edges
        .iter()
        .filter(|edge| edge["edge_type"] == "contains" && edge["from_bead"] == group["bead_id"])
        .map(|edge| text_of(&edge["to_bead"]))
        .collect();
    assert_eq!(members.len(), 2);
    assert!(members.iter().all(|text| text.contains("big bang cutover")));
    // One scope's export holds the same, since nothing here names a bead
    // of another scope, and it resumes from a change number.
    let scoped = q.ok(&["export", "--scope", "acme"]);
    assert_eq!(scoped, records);
    let after = records
        .iter()
        .rev()
        .find(|r| r["record_kind"] == "event")
        .unwrap()["seq"]
        .as_u64()
        .unwrap();
    let later = q.ok(&[
        "export",
        "--workspace",
        "demo",
        "--after",
        &after.to_string(),
    ]);
    assert!(later.iter().all(|r| r["seq"].as_u64().unwrap() > after));
    assert_eq!(of_kind(&later, "verdict").len(), 10);

    // Erasing the decision removes every verdict and name link about it.
    // The label that followed it stays, with the place it held.
    let decision = chunks
        .iter()
        .find(|chunk| chunk["text"].as_str().unwrap().contains("phased cutover"))
        .unwrap();
    let event_id = decision["event_id"].as_str().unwrap().to_string();
    q.ok(&["erase", "--reason", "user_request", "event", &event_id]);
    let after = q.ok(&["export"]);
    let left = of_kind(&after, "verdict");
    assert_eq!(left.len(), 10, "{left:?}");
    assert!(left.iter().all(|verdict| {
        verdict["earlier_bead"] != decision["chunk_id"]
            && verdict["later_bead"] != decision["chunk_id"]
    }));
    assert!(of_kind(&after, "name_link").is_empty());
    assert!(
        of_kind(&after, "bead")
            .iter()
            .any(|bead| bead["bead_kind"] == "label")
    );
    assert_eq!(of_kind(&after, "erasure").len(), 1);
}

#[test]
fn what_was_stored_before_a_judge_is_queued_for_it_when_asked() {
    let model = StandIn::start();
    let q = Quickstart::ready();
    q.ok(&["ingest", "--input", "events.jsonl"]);
    // No judge: a queue of beads would wait for no one, so none is made.
    let queued = q.ok(&["read", "queue", "acme"]);
    assert_eq!(
        queued.iter().map(|line| &line["work"]).collect::<Vec<_>>(),
        ["chunk_read", "tool_read"]
    );
    let none = q.run(&["read", "queue", "acme", "--reread"]);
    assert_eq!(none.status.code(), Some(1));
    assert!(stderr(&none).contains("[judge]"), "{}", stderr(&none));

    // A judge alone, with no reader of chunks: beads are judged without a
    // type.
    q.configure(&format!(
        "\n[judge]\nbinding = \"local\"\ndebounce_ms = 0\nmax_attempts = 1\n{}",
        model.binding()
    ));
    q.ok(&["scope", "allow", "acme", "--binding", "local"]);
    assert_eq!(queue_of(&q.ok(&["read", "status"]), "judge")["ready"], 0);
    assert!(q.ok(&["read", "run"]).is_empty());
    let queued = q.ok(&["read", "queue", "acme"]);
    assert_eq!(queue_of(&queued, "judge")["queued"], 4);
    assert_eq!(
        queue_of(&q.ok(&["read", "queue", "acme"]), "judge")["queued"],
        0
    );

    // The provider turns the binding down. With one attempt allowed, the
    // beads are set aside for an operator, and told to be tried again.
    // The record of four days later comes up alone: the others are still
    // owed a judgment, so it has none to be read with, and no call is
    // made for it. They are read with it once they are tried again.
    model.fail_with(401);
    let failed = q.ok(&["read", "run"]);
    let outcomes: Vec<(Value, Value, Value)> = judged(&failed)
        .iter()
        .map(|round| {
            (
                round["new_beads"].clone(),
                round["calls"].clone(),
                round["problem"].clone(),
            )
        })
        .collect();
    assert_eq!(
        outcomes,
        [
            (json!(3), json!(1), json!("binding_rejected")),
            (json!(1), json!(0), json!("nothing_to_compare")),
        ]
    );
    let status = q.ok(&["read", "status"]);
    let judge = queue_of(&status, "judge");
    assert_eq!(
        (&judge["failed"], &judge["problems"]["binding_rejected"]),
        (&Value::from(3), &Value::from(3))
    );
    model.fail_with(0);
    // The chunks queued above wait for a reader that is not configured,
    // and were not set aside.
    assert_eq!(q.ok(&["read", "retry"])[0]["retried"], 3);
    let rounds = q.ok(&["read", "run"]);
    let read = judged(&rounds);
    assert_eq!(
        (
            sum(&read, "judged"),
            sum(&read, "window_beads"),
            sum(&read, "verdicts"),
            sum(&read, "names")
        ),
        (3, 1, 6, 1),
        "{rounds:?}"
    );
    let shown: Value = serde_json::from_str(
        model.requests().last().unwrap()["messages"][1]["content"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    assert!(
        shown["new"]
            .as_array()
            .unwrap()
            .iter()
            .all(|memory| memory.get("type").is_none()),
        "{shown}"
    );

    // Judged by one judge, the beads are queued again only on request,
    // and only for another judge. The record that came up alone was never
    // read as new, and is queued: every pair it has was read beside the
    // others since, so it is settled again without a call.
    assert_eq!(
        queue_of(&q.ok(&["read", "queue", "acme", "--reread"]), "judge")["queued"],
        1
    );
    let rounds = q.ok(&["read", "run"]);
    let outcomes: Vec<(Value, Value)> = judged(&rounds)
        .iter()
        .map(|round| (round["calls"].clone(), round["problem"].clone()))
        .collect();
    assert_eq!(outcomes, [(json!(0), json!("nothing_to_compare"))]);
    let config = q.dir.path().join("core-memory.toml");
    let text = std::fs::read_to_string(&config).unwrap();
    std::fs::write(&config, text.replace("stand-in-1", "stand-in-2")).unwrap();
    assert_eq!(
        queue_of(&q.ok(&["read", "queue", "acme"]), "judge")["queued"],
        1
    );
    assert_eq!(
        queue_of(&q.ok(&["read", "queue", "acme", "--reread"]), "judge")["queued"],
        3
    );
    let rounds = q.ok(&["read", "run"]);
    let read = judged(&rounds);
    // Its verdicts are its own. The relationship and the name link it saw
    // again are the ones that were there.
    assert_eq!(
        (
            sum(&read, "verdicts"),
            sum(&read, "edges"),
            sum(&read, "names")
        ),
        (6, 0, 0),
        "{rounds:?}"
    );
    let records = q.ok(&["export"]);
    assert_eq!(of_kind(&records, "verdict").len(), 12);
    assert_eq!(of_kind(&records, "edge").len(), 1);
    assert_eq!(of_kind(&records, "name_link").len(), 1);
}

#[cfg(unix)]
#[test]
fn the_service_judges_what_it_admits_and_still_stops_cleanly() {
    use std::io::{Read, Write};

    let model = StandIn::start();
    let q = Quickstart::ready();
    q.configure(&model.config_with_judge(""));
    q.ok(&["scope", "allow", "acme", "--binding", "local"]);
    let serving = Serving::start(&q, "shutdown_grace_ms = 2000");

    for (id, text) in [
        ("first", "We're going with the phased cutover."),
        ("second", "Agreed. Priya takes the ledger export."),
    ] {
        let body = format!(
            r#"{{"scope":"acme","source_ns":"slack/T01","external_id":"{id}","profile_id":"core.chat_transcript","media_type":"text/plain","content":{{"text":"{text}"}},"parser_contract_version":"1"}}"#
        );
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
    }

    // The judge beside the server gets to the two beads within a few
    // seconds, once their chunks were read.
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    let records = loop {
        let records = q.ok(&["export"]);
        if !of_kind(&records, "verdict").is_empty() || std::time::Instant::now() > deadline {
            break records;
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    };
    let verdicts = of_kind(&records, "verdict");
    assert_eq!(verdicts.len(), 1, "{records:?}");
    assert_eq!(verdicts[0]["verdict"], "leadsto");
    assert_eq!(of_kind(&records, "edge").len(), 1);

    let (status, log) = serving.stop();
    assert_eq!(status.code(), Some(0), "{log}");
    assert!(!log.contains("panicked"), "{log}");
    // The log counts what the judge did. It holds nothing of what was
    // said.
    assert!(log.contains("\"judged\":{"), "{log}");
    assert!(log.contains("\"verdicts\":1"), "{log}");
    assert!(!log.contains("Priya") && !log.contains("cutover"), "{log}");
}

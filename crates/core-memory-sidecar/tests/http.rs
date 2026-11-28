//! The HTTP API against a real PostgreSQL database. Most tests drive the
//! router without a network socket; the ones about connections use one.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

use axum::Router;
use axum::body::Body;
use axum::http::{HeaderMap, Request, StatusCode, header};
use core_memory_contract::Sha256Digest;
use core_memory_sidecar::config::Loaded;
use core_memory_sidecar::server::{ServeLimits, prepare, serve};
use core_memory_store_postgres::SCHEMA_VERSION;
use core_memory_store_postgres::testing::{TestDb, admin_application};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use tower::ServiceExt;

const WRITER: &str = "cm_test_writer";
const OUTSIDER: &str = "cm_test_outsider";
/// May recall from `acme` and write nothing.
const READER: &str = "cm_test_reader";

/// A prepared server on a database of its own, with scope `acme` enrolled
/// in workspace `w1`. The store client blocks, so the server is built and
/// the database governed outside the async runtime.
struct Server {
    rt: tokio::runtime::Runtime,
    router: Router,
    dir: tempfile::TempDir,
    db: Option<TestDb>,
}

struct Reply {
    status: StatusCode,
    body: Value,
    headers: HeaderMap,
}

impl Reply {
    /// The status with the body's `outcome`, `reason` or error code.
    fn outcome(&self) -> (StatusCode, &str) {
        let name = self.body["reason"]
            .as_str()
            .or(self.body["outcome"].as_str())
            .or(self.body["error"]["code"].as_str())
            .unwrap_or_default();
        (self.status, name)
    }
}

fn config(url: &str, store: &str, http: &str) -> String {
    let batch = if http.contains("max_batch_events") {
        ""
    } else {
        "max_batch_events = 3"
    };
    format!(
        r#"
[store]
url = "{url}"
pool_size = 2
{store}

[[profile]]
id = "acme.notes"
rev = 1
source_type = "document"
content = "text"
max_event_bytes = 200
chunker = {{ id = "text.structural", rev = 1, max = 40 }}

[http]
{batch}
busy_retry_after_ms = 1500
{http}

[[http.token]]
id = "writer"
sha256 = "{writer}"
scopes = ["acme"]

[[http.token]]
id = "outsider"
sha256 = "{outsider}"
scopes = ["elsewhere"]

[[http.token]]
id = "reader"
sha256 = "{reader}"
recall = ["acme"]
"#,
        writer = Sha256Digest::of(WRITER.as_bytes()),
        outsider = Sha256Digest::of(OUTSIDER.as_bytes()),
        reader = Sha256Digest::of(READER.as_bytes()),
    )
}

impl Server {
    fn new() -> Self {
        Self::with("", "")
    }

    fn with(store: &str, http: &str) -> Self {
        let db = TestDb::create();
        db.connect()
            .batch_execute(
                "SELECT core_memory.create_workspace('w1');
                 SELECT core_memory.enroll_scope('acme', 'w1');",
            )
            .unwrap();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("core-memory.toml");
        std::fs::write(&path, config(&db.url, store, http)).unwrap();
        let prepared = prepare(&Loaded::read(&path).unwrap()).unwrap();
        Self {
            rt: tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
                .unwrap(),
            router: prepared.router,
            dir,
            db: Some(db),
        }
    }

    /// Runs governance or inspection SQL as the database owner.
    fn sql(&self, sql: &str) {
        self.db
            .as_ref()
            .unwrap()
            .connect()
            .batch_execute(sql)
            .unwrap();
    }

    /// The engine's sessions on this test's database: every client session
    /// there that the test helpers did not open. Each test has a database of
    /// its own, so no other run's sessions can match.
    fn engine_sessions() -> String {
        format!(
            "FROM pg_stat_activity WHERE datname = current_database() \
             AND backend_type = 'client backend' AND application_name <> '{}'",
            admin_application()
        )
    }

    fn count(&self, sql: &str) -> i64 {
        self.db
            .as_ref()
            .unwrap()
            .connect()
            .query_one(sql, &[])
            .unwrap()
            .get(0)
    }

    fn send(&self, request: Request<Body>) -> Reply {
        let router = self.router.clone();
        self.rt.block_on(async move {
            let response = router.oneshot(request).await.unwrap();
            let status = response.status();
            let headers = response.headers().clone();
            let bytes = response.into_body().collect().await.unwrap().to_bytes();
            let body = if bytes.is_empty() {
                Value::Null
            } else {
                serde_json::from_slice(&bytes)
                    .unwrap_or_else(|_| Value::String(String::from_utf8_lossy(&bytes).into()))
            };
            Reply {
                status,
                body,
                headers,
            }
        })
    }

    fn call(&self, method: &str, uri: &str, token: Option<&str>, body: Option<Value>) -> Reply {
        self.send(request(method, uri, token, body))
    }

    fn post(&self, token: &str, event: Value) -> Reply {
        self.call("POST", "/v1/events", Some(token), Some(event))
    }

    fn get(&self, uri: &str, token: Option<&str>) -> Reply {
        self.call("GET", uri, token, None)
    }
}

fn request(method: &str, uri: &str, token: Option<&str>, body: Option<Value>) -> Request<Body> {
    let mut request = Request::builder().method(method).uri(uri);
    if let Some(token) = token {
        request = request.header(header::AUTHORIZATION, format!("Bearer {token}"));
    }
    let body = match body {
        Some(v) => Body::from(v.to_string()),
        None => Body::empty(),
    };
    request.body(body).unwrap()
}

fn event(id: &str, text: &str) -> Value {
    event_in("acme", id, text)
}

fn event_in(scope: &str, id: &str, text: &str) -> Value {
    json!({
        "scope": scope,
        "source_ns": "slack/T01",
        "external_id": id,
        "profile_id": "core.chat_transcript",
        "media_type": "text/plain",
        "content": {"text": text},
        "parser_contract_version": "slack-export/1"
    })
}

fn with_checkpoint(mut event: Value, key: &str, expected: u64, next: u64) -> Value {
    event["checkpoint"] = json!({
        "key": key,
        "expected_sequence": expected,
        "next_sequence": next,
        "hash": Sha256Digest::of(format!("{key}:{next}").as_bytes()),
    });
    event
}

#[test]
fn requests_need_a_valid_token() {
    let s = Server::new();
    let reply = s.call("POST", "/v1/events", None, Some(event("1", "hi")));
    assert_eq!(reply.outcome(), (StatusCode::UNAUTHORIZED, "unauthorized"));
    assert_eq!(reply.headers[header::WWW_AUTHENTICATE], "Bearer");
    assert_eq!(
        s.post("cm_wrong", event("1", "hi")).status,
        StatusCode::UNAUTHORIZED
    );
    for (method, uri) in [
        ("POST", "/v1/events/batch"),
        ("GET", "/v1/checkpoints?scope=acme&key=k"),
        ("POST", "/v1/recall"),
    ] {
        let reply = s.call(method, uri, None, Some(json!({"events": []})));
        assert_eq!(reply.status, StatusCode::UNAUTHORIZED, "{uri}");
    }
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 0);

    // The scheme name is case-insensitive.
    let request = Request::builder()
        .method("POST")
        .uri("/v1/events")
        .header(header::AUTHORIZATION, format!("bearer {WRITER}"))
        .body(Body::from(event("1", "hi").to_string()))
        .unwrap();
    assert_eq!(s.send(request).status, StatusCode::OK);
}

#[test]
fn admission_outcomes_map_to_statuses() {
    let s = Server::new();

    let accepted = s.post(WRITER, event("1", "We ship Friday."));
    assert_eq!(accepted.outcome(), (StatusCode::OK, "accepted"));
    assert_eq!(accepted.body["chunk_ids"].as_array().unwrap().len(), 1);
    // The answer is sent after the commit.
    assert_eq!(s.count("SELECT count(*) FROM core_memory.chunk_text"), 1);

    let replay = s.post(WRITER, event("1", "We ship Friday."));
    assert_eq!(replay.outcome(), (StatusCode::OK, "replay"));
    assert_eq!(replay.body["event_id"], accepted.body["event_id"]);
    assert_eq!(
        s.post(WRITER, event("1", "Changed.")).outcome(),
        (StatusCode::CONFLICT, "identity_conflict")
    );

    // A token writes only the scopes it was granted, and a granted scope
    // must be enrolled.
    assert_eq!(
        s.post(OUTSIDER, event("2", "hi")).outcome(),
        (StatusCode::FORBIDDEN, "scope_denied")
    );
    assert_eq!(
        s.post(OUTSIDER, event_in("elsewhere", "2", "hi")).outcome(),
        (StatusCode::FORBIDDEN, "scope_denied")
    );

    let mut unknown = event("3", "hi");
    unknown["profile_id"] = json!("nope");
    assert_eq!(
        s.post(WRITER, unknown).outcome(),
        (StatusCode::UNPROCESSABLE_ENTITY, "invalid_profile")
    );
    assert_eq!(
        s.post(WRITER, json!({"scope": "acme"})).outcome(),
        (StatusCode::BAD_REQUEST, "malformed")
    );
    let not_json = Request::builder()
        .method("POST")
        .uri("/v1/events")
        .header(header::AUTHORIZATION, format!("Bearer {WRITER}"))
        .body(Body::from("{not json"))
        .unwrap();
    assert_eq!(
        s.send(not_json).outcome(),
        (StatusCode::BAD_REQUEST, "malformed")
    );

    let mut long = event("4", &"too long for this profile. ".repeat(12));
    long["profile_id"] = json!("acme.notes");
    assert_eq!(
        s.post(WRITER, long).outcome(),
        (StatusCode::PAYLOAD_TOO_LARGE, "oversize")
    );

    // Content PostgreSQL cannot hold is refused, never altered.
    assert_eq!(
        s.post(WRITER, event("5", "a\u{0}b")).outcome(),
        (StatusCode::BAD_REQUEST, "malformed")
    );
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 1);
}

#[test]
fn a_tool_call_is_admitted_like_any_other_event() {
    let s = Server::new();
    let call = json!({
        "scope": "acme",
        "source_ns": "agent/s1",
        "external_id": "toolu_01",
        "profile_id": "core.tool_call",
        "media_type": "application/json",
        "content": {"tool_call": {
            "tool": "Bash",
            "input": {"command": "ls"},
            "output": "Cargo.toml\nsrc\n"
        }},
        "parser_contract_version": "agent-log/1"
    });
    assert_eq!(
        s.post(WRITER, call.clone()).outcome(),
        (StatusCode::OK, "accepted")
    );
    assert_eq!(
        s.post(WRITER, call.clone()).outcome(),
        (StatusCode::OK, "replay")
    );
    // One bead for the call, and its text kept whole: the arguments in
    // canonical form, a line feed, and the output byte for byte.
    assert_eq!(
        s.count("SELECT count(*) FROM core_memory.beads WHERE kind = 'tool'"),
        1
    );
    assert_eq!(
        s.count(
            "SELECT count(*) FROM core_memory.chunk_text
              WHERE text = E'{\"command\":\"ls\"}\\nCargo.toml\\nsrc\\n'"
        ),
        1
    );

    // The same call with another output is not the same event.
    let mut changed = call.clone();
    changed["content"]["tool_call"]["output"] = json!("Cargo.toml\n");
    assert_eq!(
        s.post(WRITER, changed).outcome(),
        (StatusCode::CONFLICT, "identity_conflict")
    );
    // Arguments that could be read two ways are refused.
    let doubled = call
        .to_string()
        .replace(
            r#"{"command":"ls"}"#,
            r#"{"command":"ls","command":"rm -rf ."}"#,
        )
        .replace("toolu_01", "toolu_02");
    let request = Request::builder()
        .method("POST")
        .uri("/v1/events")
        .header(header::AUTHORIZATION, format!("Bearer {WRITER}"))
        .body(Body::from(doubled))
        .unwrap();
    let refused = s.send(request);
    assert_eq!(refused.outcome(), (StatusCode::BAD_REQUEST, "malformed"));
    assert!(
        refused.body.to_string().contains("duplicate key"),
        "{}",
        refused.body
    );
    // A tool call is not a message, and a profile for messages refuses it.
    let mut misfiled = call;
    misfiled["external_id"] = json!("toolu_03");
    misfiled["profile_id"] = json!("core.chat_transcript");
    let refused = s.post(WRITER, misfiled);
    assert_eq!(refused.outcome(), (StatusCode::BAD_REQUEST, "malformed"));
    assert!(
        refused.body["problems"][0]["problem"]
            .as_str()
            .unwrap()
            .contains("takes Text content"),
        "{}",
        refused.body
    );
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 1);
}

#[test]
fn a_scopes_state_and_erasures_take_effect_at_once() {
    let s = Server::new();
    let first = s.post(WRITER, event("1", "before the pause"));
    assert_eq!(first.outcome(), (StatusCode::OK, "accepted"));

    s.sql("SELECT core_memory.set_scope_state('acme', 'paused')");
    assert_eq!(
        s.post(WRITER, event("2", "while paused")).outcome(),
        (StatusCode::FORBIDDEN, "scope_not_active")
    );
    s.sql("SELECT core_memory.set_scope_state('acme', 'active')");
    assert_eq!(
        s.post(WRITER, event("2", "while paused")).outcome(),
        (StatusCode::OK, "accepted")
    );

    let erase = format!(
        "SELECT core_memory.erase_event('{}', 'user_request', 'owner')",
        first.body["event_id"].as_str().unwrap()
    );
    s.sql(&erase);
    assert_eq!(
        s.post(WRITER, event("1", "before the pause")).outcome(),
        (StatusCode::GONE, "tombstoned")
    );

    s.sql("SELECT core_memory.set_scope_state('acme', 'revoked')");
    assert_eq!(
        s.post(WRITER, event("3", "after revoking")).outcome(),
        (StatusCode::FORBIDDEN, "scope_not_active")
    );
    assert_eq!(
        s.count("SELECT count(*) FROM core_memory.events WHERE state = 'live'"),
        1
    );
}

#[test]
fn batches_return_one_outcome_per_event() {
    let s = Server::new();
    let body = json!({"events": [event("b1", "one"), {"not": "an event"}, event("b2", "two")]});
    let reply = s.call("POST", "/v1/events/batch", Some(WRITER), Some(body));
    assert_eq!(reply.status, StatusCode::OK);
    let outcomes: Vec<_> = reply.body["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["outcome"].as_str().unwrap())
        .collect();
    assert_eq!(outcomes, ["accepted", "refused", "accepted"]);
    assert_eq!(
        reply.body["results"][1]["problems"][0]["field"],
        "events[1]"
    );

    let too_many =
        json!({"events": [event("x1", "a"), event("x2", "b"), event("x3", "c"), event("x4", "d")]});
    assert_eq!(
        s.call("POST", "/v1/events/batch", Some(WRITER), Some(too_many))
            .outcome(),
        (StatusCode::PAYLOAD_TOO_LARGE, "batch_too_large")
    );
    assert_eq!(
        s.call(
            "POST",
            "/v1/events/batch",
            Some(WRITER),
            Some(json!([1, 2]))
        )
        .outcome(),
        (StatusCode::BAD_REQUEST, "malformed")
    );
    let empty = s.call(
        "POST",
        "/v1/events/batch",
        Some(WRITER),
        Some(json!({"events": []})),
    );
    assert_eq!(
        (empty.status, &empty.body),
        (StatusCode::OK, &json!({"results": []}))
    );
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 2);
}

#[test]
fn a_reader_resumes_from_its_checkpoint() {
    let s = Server::new();
    let uri = "/v1/checkpoints?scope=acme&key=session-1";
    assert_eq!(
        s.get(uri, Some(WRITER)).outcome(),
        (StatusCode::NOT_FOUND, "not_found")
    );

    let first = s.post(
        WRITER,
        with_checkpoint(event("1", "one"), "session-1", 0, 1),
    );
    assert_eq!(first.outcome(), (StatusCode::OK, "accepted"));
    assert_eq!(
        first.body["checkpoint"],
        json!({"key": "session-1", "sequence": 1})
    );
    let stored = s.get(uri, Some(WRITER));
    assert_eq!(stored.status, StatusCode::OK);
    assert_eq!(
        (
            &stored.body["scope"],
            &stored.body["key"],
            &stored.body["sequence"]
        ),
        (&json!("acme"), &json!("session-1"), &json!(1))
    );
    assert_eq!(stored.body["hash"], json!(Sha256Digest::of(b"session-1:1")));

    // A second reader that also starts from zero is told what is stored,
    // and its event is not admitted.
    let late = s.post(
        WRITER,
        with_checkpoint(event("2", "two"), "session-1", 0, 5),
    );
    assert_eq!(
        late.outcome(),
        (StatusCode::CONFLICT, "checkpoint_conflict")
    );
    assert_eq!(late.body["stored_sequence"], 1);
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 1);

    // A retry of the first request advances nothing and gets the same receipt.
    let retry = s.post(
        WRITER,
        with_checkpoint(event("1", "one"), "session-1", 0, 1),
    );
    assert_eq!(retry.outcome(), (StatusCode::OK, "replay"));
    assert_eq!(retry.body["checkpoint"], first.body["checkpoint"]);

    // A token reads only the checkpoints of scopes it may write.
    assert_eq!(
        s.get(uri, Some(OUTSIDER)).outcome(),
        (StatusCode::FORBIDDEN, "scope_denied")
    );
    for bad in [
        "/v1/checkpoints?scope=acme",
        "/v1/checkpoints",
        "/v1/checkpoints?scope=acme&key=",
        "/v1/checkpoints?scope=acme&key=%00",
        "/v1/checkpoints?scope=acme&key=line%0Abreak",
    ] {
        assert_eq!(
            s.get(bad, Some(WRITER)).outcome(),
            (StatusCode::BAD_REQUEST, "malformed"),
            "{bad}"
        );
    }
    // Who is asking is settled before what they asked.
    assert_eq!(
        s.get("/v1/checkpoints", None).outcome(),
        (StatusCode::UNAUTHORIZED, "unauthorized")
    );
}

#[test]
fn many_requests_share_a_small_pool() {
    let s = Server::new();
    let router = s.router.clone();
    let statuses = s.rt.block_on(async move {
        let mut calls = tokio::task::JoinSet::new();
        for i in 0..24 {
            let router = router.clone();
            // Each event is offered twice.
            let event = event(&format!("e{}", i / 2), "said once");
            calls.spawn(async move {
                let response = router
                    .oneshot(request("POST", "/v1/events", Some(WRITER), Some(event)))
                    .await
                    .unwrap();
                let status = response.status();
                let bytes = response.into_body().collect().await.unwrap().to_bytes();
                let body: Value = serde_json::from_slice(&bytes).unwrap();
                (status, body["outcome"].as_str().unwrap().to_string())
            });
        }
        calls.join_all().await
    });
    assert!(
        statuses.iter().all(|(status, _)| *status == StatusCode::OK),
        "{statuses:?}"
    );
    let accepted = statuses.iter().filter(|(_, o)| o == "accepted").count();
    let replays = statuses.iter().filter(|(_, o)| o == "replay").count();
    assert_eq!((accepted, replays), (12, 12), "{statuses:?}");
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 12);
    // Twelve events and their twelve beads, numbered without a gap.
    assert_eq!(
        s.count("SELECT last_seq FROM core_memory.workspace_seq WHERE workspace_id = 'w1'"),
        24
    );
    assert!(
        s.count(&format!("SELECT count(*) {}", Server::engine_sessions())) <= 2,
        "the pool holds at most two connections"
    );
}

#[test]
fn a_token_recalls_only_from_the_scopes_it_may_recall_from() {
    let s = Server::new();
    s.sql("SELECT core_memory.enroll_scope('elsewhere', 'w1');");
    for (id, text) in [
        ("1", "The launch is on Thursday."),
        ("2", "The export takes three hours."),
    ] {
        assert_eq!(s.post(WRITER, event(id, text)).status, StatusCode::OK);
    }
    assert_eq!(
        s.post(
            OUTSIDER,
            event_in("elsewhere", "3", "Their launch is secret.")
        )
        .status,
        StatusCode::OK
    );
    let recall = |token: &str, body: Value| s.call("POST", "/v1/recall", Some(token), Some(body));

    // Without naming scopes, a reader searches every scope it may recall
    // from, and gets the text as it was written.
    let reply = recall(READER, json!({"query": "launches"}));
    assert_eq!(reply.status, StatusCode::OK);
    assert_eq!(reply.body["found"], "matched");
    let beads = reply.body["beads"].as_array().unwrap();
    assert_eq!(beads.len(), 1);
    assert_eq!(beads[0]["chunks"][0]["text"], "The launch is on Thursday.");
    assert_eq!(beads[0]["event"]["key"]["scope"], "acme");
    assert_eq!(beads[0]["via"], "match");
    assert_eq!(beads[0]["current"], true);

    // A token that writes does not read back, and a reader is refused a
    // scope it was not given, without being told whether it exists.
    assert_eq!(
        recall(WRITER, json!({"query": "launch"})).outcome(),
        (StatusCode::FORBIDDEN, "recall_not_granted")
    );
    let refused = recall(READER, json!({"query": "launch", "scopes": ["elsewhere"]}));
    assert_eq!(
        refused.outcome(),
        (StatusCode::FORBIDDEN, "scope_not_granted")
    );
    assert!(!refused.body.to_string().contains("elsewhere"));
    // A reader cannot write.
    assert_eq!(
        s.post(READER, event("4", "not stored")).outcome(),
        (StatusCode::FORBIDDEN, "scope_denied")
    );

    // Nothing found and nothing to search for are answers, not errors.
    assert_eq!(
        recall(READER, json!({"query": "zeppelin"})).body["found"],
        "no_match"
    );
    assert_eq!(
        recall(READER, json!({"query": "the"})).body["found"],
        "no_terms"
    );
    for (body, code) in [
        (json!({"query": "  "}), "empty_query"),
        (json!({"query": "launch", "limit": 0}), "bad_limit"),
        (json!({"query": "launch", "follow": 3}), "bad_follow"),
        (json!({"query": "launch", "scopes": []}), "bad_scopes"),
        (json!({"query": "x".repeat(1_025)}), "query_too_long"),
    ] {
        assert_eq!(
            recall(READER, body).outcome(),
            (StatusCode::BAD_REQUEST, code)
        );
    }
    // A body that is not a request is refused without being repeated: a
    // query may be words of what a source said.
    let reply = recall(
        READER,
        json!({"query": "launch codes 4417", "summarize": true}),
    );
    assert_eq!(reply.outcome(), (StatusCode::BAD_REQUEST, "malformed"));
    assert!(!reply.body.to_string().contains("4417"), "{}", reply.body);
    assert!(
        !reply.body.to_string().contains("summarize"),
        "{}",
        reply.body
    );
}

#[test]
fn a_store_that_is_gone_makes_the_server_busy_and_not_ready() {
    let mut s = Server::new();
    let ready = s.get("/v1/ready", None);
    assert_eq!(ready.status, StatusCode::OK);
    assert_eq!(
        ready.body,
        json!({"ready": true, "schema_version": SCHEMA_VERSION})
    );
    assert_eq!(
        s.post(WRITER, event("1", "stored")).outcome(),
        (StatusCode::OK, "accepted")
    );

    // Dropping the database ends the pool's connections.
    s.db = None;

    let ready = s.get("/v1/ready", None);
    assert_eq!(ready.status, StatusCode::SERVICE_UNAVAILABLE);
    // A probe is told the kind of problem and nothing about the database.
    assert_eq!(
        ready.body,
        json!({"ready": false, "schema_version": SCHEMA_VERSION, "problem": "store_unavailable"})
    );

    let busy = s.post(WRITER, event("2", "not stored"));
    assert_eq!(busy.outcome(), (StatusCode::SERVICE_UNAVAILABLE, "busy"));
    assert_eq!(
        busy.body,
        json!({"outcome": "busy", "retry_after_ms": 1500, "cause": "capacity"})
    );
    assert_eq!(busy.headers[header::RETRY_AFTER], "2");

    // A batch the store took none of answers the same way.
    let batch = json!({"events": [event("3", "a"), event("4", "b")]});
    let reply = s.call("POST", "/v1/events/batch", Some(WRITER), Some(batch));
    assert_eq!(reply.status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(reply.headers[header::RETRY_AFTER], "2");
    let outcomes: Vec<_> = reply.body["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["outcome"].as_str().unwrap())
        .collect();
    assert_eq!(outcomes, ["busy", "busy"]);

    assert_eq!(
        s.get("/v1/checkpoints?scope=acme&key=k", Some(WRITER))
            .outcome(),
        (StatusCode::SERVICE_UNAVAILABLE, "store_unavailable")
    );
    let recall = s.call(
        "POST",
        "/v1/recall",
        Some(READER),
        Some(json!({"query": "stored"})),
    );
    assert_eq!(
        recall.outcome(),
        (StatusCode::SERVICE_UNAVAILABLE, "store_unavailable")
    );
    assert_eq!(recall.headers[header::RETRY_AFTER], "2");
    // By now the engine has tried to connect again and been told there is
    // no such database. Callers are still asked to come back, and whoever
    // runs the engine is told that waiting will not help.
    let ready = s.get("/v1/ready", None);
    assert_eq!(ready.status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(ready.body["problem"], "store_refused");
    // Liveness does not depend on the store.
    assert_eq!(s.get("/v1/health", None).status, StatusCode::OK);
}

#[test]
fn a_store_at_another_schema_version_takes_no_writes() {
    let s = Server::new();
    assert_eq!(
        s.post(WRITER, event("1", "stored")).outcome(),
        (StatusCode::OK, "accepted")
    );

    // A newer build migrates the database under the running server.
    s.sql(
        "INSERT INTO core_memory.schema_migrations (version, name, checksum)
         VALUES (9999, 'from the future', 'x')",
    );
    let ready = s.get("/v1/ready", None);
    assert_eq!(ready.status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(ready.body["problem"], "schema_mismatch");

    // Connections that were open before the migration are fenced too.
    for i in 0..4 {
        let reply = s.post(WRITER, event(&format!("late-{i}"), "not stored"));
        assert_eq!(reply.outcome(), (StatusCode::SERVICE_UNAVAILABLE, "busy"));
    }
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 1);
}

#[test]
fn a_body_over_the_limit_is_refused_before_it_is_read() {
    let s = Server::with("", "max_body_bytes = 512");
    assert_eq!(s.post(WRITER, event("1", "small")).status, StatusCode::OK);
    let reply = s.post(WRITER, event("2", &"x".repeat(600)));
    assert_eq!(
        reply.outcome(),
        (StatusCode::PAYLOAD_TOO_LARGE, "body_too_large")
    );
    // A body that does not declare its length is cut off at the limit too.
    let chunks = (0..100).map(|_| Ok::<_, std::io::Error>(vec![b'x'; 64]));
    let streamed = Request::builder()
        .method("POST")
        .uri("/v1/events")
        .header(header::AUTHORIZATION, format!("Bearer {WRITER}"))
        .body(Body::from_stream(tokio_stream(chunks)))
        .unwrap();
    assert_eq!(
        s.send(streamed).outcome(),
        (StatusCode::PAYLOAD_TOO_LARGE, "body_too_large")
    );
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 1);
}

/// A body stream over ready-made chunks.
fn tokio_stream<I>(chunks: I) -> impl futures_core::Stream<Item = I::Item> + Send + 'static
where
    I: IntoIterator + Send + 'static,
    I::IntoIter: Send + Unpin,
{
    struct Ready<T>(T);
    impl<T: Iterator + Unpin> futures_core::Stream for Ready<T> {
        type Item = T::Item;
        fn poll_next(
            mut self: std::pin::Pin<&mut Self>,
            _: &mut std::task::Context<'_>,
        ) -> std::task::Poll<Option<T::Item>> {
            std::task::Poll::Ready(self.0.next())
        }
    }
    Ready(chunks.into_iter())
}

#[test]
fn the_token_is_checked_before_any_of_the_body_is_read() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

    let s = Server::new();
    for token in [None, Some("cm_wrong")] {
        let polled = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&polled);
        let chunks = std::iter::from_fn(move || {
            flag.store(true, Ordering::SeqCst);
            Some(Ok::<_, std::io::Error>(vec![b'x'; 1024]))
        });
        let mut request = Request::builder().method("POST").uri("/v1/events");
        if let Some(token) = token {
            request = request.header(header::AUTHORIZATION, format!("Bearer {token}"));
        }
        // A body with no end: reading it at all would never finish.
        let request = request
            .body(Body::from_stream(tokio_stream(chunks)))
            .unwrap();
        assert_eq!(s.send(request).status, StatusCode::UNAUTHORIZED);
        assert!(!polled.load(Ordering::SeqCst), "the body was not touched");
    }
}

#[test]
fn input_that_could_be_read_two_ways_is_malformed() {
    let s = Server::new();
    let mut unknown_nested = event("1", "hi");
    unknown_nested["actor"] = json!({"id": "U1", "kind": "human", "display_name": "Ada"});
    let mut far_future = event("2", "hi");
    far_future["occurred_at"] = json!({"at": "9999-12-31T23:59:59-01:00", "precision": "second"});
    let mut before_time = event("3", "hi");
    before_time["occurred_at"] = json!({"at": "0000-01-01T00:00:00+01:00", "precision": "second"});
    let mut inexact = event("4", "hi");
    inexact["x_reply_to"] = json!(9_007_199_254_740_993u64);
    for (what, body) in [
        ("an unknown nested field", unknown_nested.to_string()),
        ("a time past the last year", far_future.to_string()),
        ("a time before the first year", before_time.to_string()),
        ("an integer canonical JSON would round", inexact.to_string()),
        (
            "a duplicate key",
            event("5", "hi").to_string().replacen(
                r#""scope":"acme""#,
                r#""scope":"acme","attributes":{"a":"1","a":"2"}"#,
                1,
            ),
        ),
    ] {
        let request = Request::builder()
            .method("POST")
            .uri("/v1/events")
            .header(header::AUTHORIZATION, format!("Bearer {WRITER}"))
            .body(Body::from(body))
            .unwrap();
        assert_eq!(
            s.send(request).outcome(),
            (StatusCode::BAD_REQUEST, "malformed"),
            "{what}"
        );
    }
    // One such event in a batch is refused alone.
    let batch = json!({"events": [event("b1", "one"), far_future, event("b2", "two")]});
    let reply = s.call("POST", "/v1/events/batch", Some(WRITER), Some(batch));
    assert_eq!(reply.status, StatusCode::OK);
    let outcomes: Vec<_> = reply.body["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["outcome"].as_str().unwrap())
        .collect();
    assert_eq!(outcomes, ["accepted", "refused", "accepted"]);
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 2);
}

#[test]
fn a_very_wide_event_is_answered_quickly() {
    let s = Server::new();
    let mut wide = event("1", "hi");
    for i in 0..150_000 {
        wide[format!("x{i}")] = json!(0);
    }
    let started = std::time::Instant::now();
    let reply = s.post(WRITER, wide);
    // Its metadata is over the limit. What matters is that the answer does
    // not take time in proportion to the square of the key count.
    assert_eq!(reply.outcome(), (StatusCode::PAYLOAD_TOO_LARGE, "oversize"));
    assert!(started.elapsed() < std::time::Duration::from_secs(20));
    assert_eq!(s.get("/v1/health", None).status, StatusCode::OK);
}

#[test]
fn a_database_that_takes_no_writes_makes_the_server_busy_and_not_ready() {
    let s = Server::new();
    assert_eq!(s.post(WRITER, event("1", "stored")).status, StatusCode::OK);
    // As after a failover that left this database a standby. Connections
    // opened from now on are read-only; the pool's are closed.
    s.sql(&format!(
        "DO $$ BEGIN EXECUTE format(
             'ALTER DATABASE %I SET default_transaction_read_only = on', current_database());
         END $$;
         SELECT pg_terminate_backend(pid) {};",
        Server::engine_sessions()
    ));
    let ready = s.get("/v1/ready", None);
    assert_eq!(ready.status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(ready.body["problem"], "store_unavailable");
    let reply = s.post(WRITER, event("2", "not stored"));
    assert_eq!(reply.outcome(), (StatusCode::SERVICE_UNAVAILABLE, "busy"));
    // What is stored can still be recognised.
    assert_eq!(
        s.post(WRITER, event("1", "stored")).outcome(),
        (StatusCode::OK, "replay")
    );
}

#[test]
fn describes_itself() {
    let s = Server::new();
    let doc = s.get("/v1/openapi.json", None);
    assert_eq!(doc.status, StatusCode::OK);
    assert_eq!(doc.body["openapi"], "3.1.0");
    for path in [
        "/v1/events",
        "/v1/events/batch",
        "/v1/checkpoints",
        "/v1/recall",
        "/v1/ready",
    ] {
        assert!(doc.body["paths"][path].is_object(), "{path}");
    }
    for name in ["source_event", "admission_outcome", "record"] {
        let schema = s.get(&format!("/v1/schemas/{name}"), None);
        assert_eq!(schema.status, StatusCode::OK, "{name}");
        assert!(schema.body.is_object(), "{name}");
    }
    assert_eq!(
        s.get("/v1/schemas/nope", None).outcome(),
        (StatusCode::NOT_FOUND, "not_found")
    );
    let health = s.get("/v1/health", None);
    assert_eq!(
        (health.status, &health.body),
        (StatusCode::OK, &json!({"status": "ok"}))
    );
}

#[test]
fn an_answer_does_not_grow_with_the_request_it_refuses() {
    let s = Server::new();
    // Thousands of problems are counted, not listed.
    let mut wrong = event("many-problems", "text");
    wrong["attributes"] = Value::Object(
        (0..5_000)
            .map(|i| (format!("Bad-Key-{i}-{}", "x".repeat(200)), json!("v")))
            .collect(),
    );
    let reply = s.post(WRITER, wrong);
    assert_eq!(reply.outcome(), (StatusCode::BAD_REQUEST, "malformed"));
    let problems = reply.body["problems"].as_array().unwrap();
    assert_eq!(problems.len(), 32);
    assert_eq!(problems[31]["problem"], "and 4970 more problems");
    assert!(reply.body.to_string().len() < 16 * 1024);

    // A parser's message quotes the name it did not expect, at any length.
    let mut odd = event("long-name", "text");
    odd["actor"] = json!({"id": "U1", "kind": "human", "x".repeat(100_000): 1});
    let reply = s.post(WRITER, odd);
    assert_eq!(reply.outcome(), (StatusCode::BAD_REQUEST, "malformed"));
    assert!(reply.body.to_string().len() < 2_000, "{}", reply.body);
    let reply = s.call(
        "POST",
        "/v1/events/batch",
        Some(WRITER),
        Some(json!({"y".repeat(100_000): []})),
    );
    assert_eq!(reply.outcome(), (StatusCode::BAD_REQUEST, "malformed"));
    assert!(reply.body.to_string().len() < 2_000, "{}", reply.body);

    // A route that needs no token repeats nothing its caller chose.
    let reply = s.get(&format!("/v1/schemas/{}", "z".repeat(5_000)), None);
    assert_eq!(reply.outcome(), (StatusCode::NOT_FOUND, "not_found"));
    assert!(!reply.body.to_string().contains("zzzz"), "{}", reply.body);
}

#[test]
fn an_answer_sent_before_the_body_was_read_ends_the_connection() {
    let s = Server::with("", "max_body_bytes = 300");
    let closes = |reply: &Reply| {
        reply
            .headers
            .get(header::CONNECTION)
            .is_some_and(|v| v == "close")
    };
    let no_token = s.call("POST", "/v1/events", None, Some(event("1", "text")));
    assert_eq!(no_token.status, StatusCode::UNAUTHORIZED);
    assert!(closes(&no_token));
    let too_large = s.post(WRITER, event("2", &"long ".repeat(200)));
    assert_eq!(too_large.status, StatusCode::PAYLOAD_TOO_LARGE);
    assert!(closes(&too_large));
    // An answer to a request that was read whole leaves the connection open.
    let stored = s.post(WRITER, event("3", "short"));
    assert_eq!(stored.status, StatusCode::OK);
    assert!(!closes(&stored));
}

#[test]
fn serving_needs_tokens_a_reachable_store_and_matching_profiles() {
    let s = Server::new();
    let url = &s.db.as_ref().unwrap().url;
    let prepare_with = |text: String| {
        let path = s.dir.path().join("other.toml");
        std::fs::write(&path, text).unwrap();
        prepare(&Loaded::read(&path).unwrap())
            .unwrap_err()
            .to_string()
    };

    let err = prepare_with(format!("[store]\nurl = \"{url}\"\n"));
    assert!(err.contains("[http]"), "{err}");
    let err = prepare_with(format!("[store]\nurl = \"{url}\"\n[http]\n"));
    assert!(err.contains("[[http.token]]"), "{err}");

    // The same profile revision with other contents would chunk differently
    // from what is stored.
    let changed = config(url, "", "").replace("max = 40 }", "max = 60 }");
    let err = prepare_with(changed);
    assert!(err.contains("bump its rev"), "{err}");

    let empty = TestDb::create_empty();
    let err = prepare_with(config(&empty.url, "", ""));
    assert!(err.contains("core-memory migrate"), "{err}");
}

// ---- connections ----

/// The server on a real socket, with short limits.
struct Listening {
    addr: std::net::SocketAddr,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    served: Option<tokio::task::JoinHandle<()>>,
}

const SHORT: ServeLimits = ServeLimits {
    read_timeout: std::time::Duration::from_millis(400),
    write_timeout: std::time::Duration::from_millis(400),
    shutdown_grace: std::time::Duration::from_millis(400),
    max_connections: 2,
};

fn listen(s: &Server) -> Listening {
    listen_with(s, SHORT)
}

fn listen_with(s: &Server, limits: ServeLimits) -> Listening {
    let router = s.router.clone();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let (ready, addr) = std::sync::mpsc::channel();
    let served = s.rt.spawn(async move {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        ready.send(listener.local_addr().unwrap()).unwrap();
        serve(listener, router, limits, async {
            let _ = stopped.await;
        })
        .await
        .unwrap();
    });
    Listening {
        addr: addr.recv().unwrap(),
        stop: Some(stop),
        served: Some(served),
    }
}

impl Listening {
    fn connect(&self) -> std::net::TcpStream {
        let stream = std::net::TcpStream::connect(self.addr).unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(5)))
            .unwrap();
        stream
    }
}

/// Everything the server sends until it closes the connection, or `None`
/// if it is still open after the read timeout.
fn read_to_close(stream: &mut std::net::TcpStream) -> Option<String> {
    use std::io::Read;
    let mut received = Vec::new();
    match stream.read_to_end(&mut received) {
        Ok(_) => Some(String::from_utf8_lossy(&received).into_owned()),
        Err(_) => None,
    }
}

#[test]
fn a_connection_that_sends_nothing_useful_is_closed() {
    use std::io::Write;
    let s = Server::new();
    let l = listen(&s);

    // Headers that never finish.
    let mut slow = l.connect();
    slow.write_all(b"GET /v1/health HTTP/1.1\r\nHost: x\r\n")
        .unwrap();
    let started = std::time::Instant::now();
    let answer = read_to_close(&mut slow).expect("the server closed the connection");
    assert!(started.elapsed() < std::time::Duration::from_secs(3));
    assert!(
        answer.is_empty() || answer.starts_with("HTTP/1.1 408"),
        "{answer}"
    );

    // A connection kept open after its request was answered. Only a
    // caller with a token gets one.
    let mut idle = l.connect();
    idle.write_all(
        format!(
            "GET /v1/checkpoints?scope=acme&key=none HTTP/1.1\r\nHost: x\r\n\
             Authorization: Bearer {WRITER}\r\n\r\n"
        )
        .as_bytes(),
    )
    .unwrap();
    let started = std::time::Instant::now();
    let answer = read_to_close(&mut idle).expect("the idle connection was closed");
    assert!(answer.starts_with("HTTP/1.1 404"), "{answer}");
    assert!(
        !answer.to_ascii_lowercase().contains("connection: close"),
        "{answer}"
    );
    // It was kept for the next request until the read timeout.
    let kept = started.elapsed();
    assert!(
        kept >= std::time::Duration::from_millis(300) && kept < std::time::Duration::from_secs(3),
        "{kept:?}"
    );
}

#[test]
fn a_caller_without_a_token_does_not_keep_a_connection() {
    use std::io::Write;
    let s = Server::new();
    let l = listen(&s);
    // None of these asks for the connection to be closed.
    for (ask, status) in [
        ("GET /v1/health", "200"),
        ("GET /v1/ready", "200"),
        ("GET /v1/schemas/source_event", "200"),
        ("GET /v1/nothing-here", "404"),
        // The token is asked for before the method is looked at.
        ("GET /v1/events", "401"),
        ("DELETE /v1/health", "405"),
        ("GET /v1/checkpoints?scope=acme&key=none", "401"),
    ] {
        let mut stream = l.connect();
        stream
            .write_all(format!("{ask} HTTP/1.1\r\nHost: x\r\n\r\n").as_bytes())
            .unwrap();
        let started = std::time::Instant::now();
        let answer = read_to_close(&mut stream).expect("the connection was closed");
        assert!(
            answer.starts_with(&format!("HTTP/1.1 {status}")),
            "{ask}: {answer}"
        );
        assert!(
            answer.to_ascii_lowercase().contains("connection: close"),
            "{ask}: {answer}"
        );
        // At once, not when the read timeout ran out.
        assert!(
            started.elapsed() < std::time::Duration::from_millis(300),
            "{ask}: {:?}",
            started.elapsed()
        );
    }
}

#[test]
fn a_caller_without_a_token_is_answered_before_it_sends_a_body() {
    use std::io::{Read, Write};
    let s = Server::new();
    let l = listen(&s);
    let mut stream = l.connect();
    // The caller announces a large body and waits to be told to send it.
    stream
        .write_all(
            b"POST /v1/events HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer cm_wrong\r\n\
              Content-Type: application/json\r\nContent-Length: 16000000\r\n\
              Expect: 100-continue\r\n\r\n",
        )
        .unwrap();
    let mut first = [0u8; 12];
    stream.read_exact(&mut first).unwrap();
    assert_eq!(
        &first,
        b"HTTP/1.1 401",
        "{}",
        String::from_utf8_lossy(&first)
    );
}

#[test]
fn shutdown_does_not_wait_for_a_request_that_never_finishes() {
    use std::io::Write;
    let s = Server::new();
    let mut l = listen(&s);
    let mut stalled = l.connect();
    stalled
        .write_all(
            format!(
                "POST /v1/events HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {WRITER}\r\n\
                 Content-Length: 1000\r\n\r\n{{\"scope\":"
            )
            .as_bytes(),
        )
        .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(100));

    let started = std::time::Instant::now();
    l.stop.take().unwrap().send(()).unwrap();
    s.rt.block_on(async {
        tokio::time::timeout(std::time::Duration::from_secs(5), l.served.take().unwrap())
            .await
            .expect("the server stopped within its grace period")
            .unwrap();
    });
    assert!(started.elapsed() < std::time::Duration::from_secs(3));
    // Nothing listens any more.
    assert!(std::net::TcpStream::connect(l.addr).is_err());
}

/// A request with a token whose answer is long: one refusal for each of
/// `events` empty events, about 150 bytes each.
fn long_answer_request(events: usize, last: bool) -> Vec<u8> {
    let body = format!(r#"{{"events":[{}]}}"#, vec!["{}"; events].join(","));
    let close = if last { "Connection: close\r\n" } else { "" };
    format!(
        "POST /v1/events/batch HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {WRITER}\r\n\
         Content-Type: application/json\r\n{close}Content-Length: {}\r\n\r\n{body}",
        body.len()
    )
    .into_bytes()
}

/// Events in a batch whose answer is several megabytes: more than the
/// operating system will hold for a client that is not reading.
const LONG_ANSWER: usize = 40_000;

#[test]
fn a_client_that_does_not_read_loses_its_connection() {
    use std::io::Write;
    let s = Server::with("", &format!("max_batch_events = {LONG_ANSWER}"));
    let l = listen(&s);
    // Both connections the server allows ask for a long answer, and read
    // none of it.
    let _held: Vec<_> = (0..2)
        .map(|_| {
            let mut stream = l.connect();
            stream
                .write_all(&long_answer_request(LONG_ANSWER, false))
                .unwrap();
            stream
        })
        .collect();
    // A third caller is served once the two have waited too long.
    let started = std::time::Instant::now();
    let mut third = l.connect();
    third
        .write_all(b"GET /v1/health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        .unwrap();
    let answer = read_to_close(&mut third).expect("the third connection was served");
    assert!(answer.starts_with("HTTP/1.1 200"), "{answer}");
    let waited = started.elapsed();
    assert!(
        waited >= std::time::Duration::from_millis(200)
            && waited < std::time::Duration::from_secs(4),
        "{waited:?}"
    );
}

#[test]
fn a_client_that_reads_slowly_but_steadily_gets_the_whole_answer() {
    use std::io::{Read, Write};
    use std::time::{Duration, Instant};
    let s = Server::with("", &format!("max_batch_events = {LONG_ANSWER}"));
    let l = listen_with(
        &s,
        ServeLimits {
            write_timeout: Duration::from_secs(1),
            ..SHORT
        },
    );
    let mut stream = l.connect();
    stream
        .write_all(&long_answer_request(LONG_ANSWER, true))
        .unwrap();
    // About half a megabyte a second, for well over the write timeout.
    // Most of the answer is still waiting in the server all that time.
    let started = Instant::now();
    let mut received = Vec::new();
    let mut some = vec![0u8; 16 * 1024];
    while started.elapsed() < Duration::from_millis(2_500) {
        let n = stream.read(&mut some).unwrap();
        assert!(
            n > 0,
            "the connection was closed {:?} in, after {} bytes",
            started.elapsed(),
            received.len()
        );
        received.extend_from_slice(&some[..n]);
        std::thread::sleep(Duration::from_millis(32));
    }
    let slowly = received.len();
    // The rest as fast as it comes.
    stream.read_to_end(&mut received).unwrap();
    let text = String::from_utf8(received).unwrap();
    let (head, body) = text.split_once("\r\n\r\n").unwrap();
    assert!(head.starts_with("HTTP/1.1 200"), "{head}");
    assert!(
        slowly < body.len() / 2,
        "the slow part was {slowly} of {} bytes",
        body.len()
    );
    let answer: Value = serde_json::from_str(body).expect("the whole answer arrived");
    assert_eq!(answer["results"].as_array().unwrap().len(), LONG_ANSWER);
}

#[test]
fn a_client_that_closes_its_side_after_asking_is_still_answered() {
    use std::io::Write;
    let s = Server::new();
    let l = listen(&s);
    let body = event("half-closed", "sent and then closed").to_string();
    let mut stream = l.connect();
    stream
        .write_all(
            format!(
                "POST /v1/events HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {WRITER}\r\n\
                 Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            )
            .as_bytes(),
        )
        .unwrap();
    stream.shutdown(std::net::Shutdown::Write).unwrap();
    let answer = read_to_close(&mut stream).expect("the request was answered");
    assert!(answer.starts_with("HTTP/1.1 200"), "{answer}");
    assert!(answer.contains("\"accepted\""), "{answer}");
    assert_eq!(s.count("SELECT count(*) FROM core_memory.events"), 1);
}

#[test]
fn connections_past_the_limit_wait_their_turn() {
    use std::io::Write;
    let s = Server::new();
    let l = listen(&s);
    // Two connections, the limit, are held open and idle.
    let _held: Vec<_> = (0..2)
        .map(|_| {
            let mut stream = l.connect();
            stream.write_all(b"GET /v1/health HTTP/1.1\r\n").unwrap();
            stream
        })
        .collect();
    std::thread::sleep(std::time::Duration::from_millis(100));
    // A third is served once the idle ones time out.
    let mut third = l.connect();
    third
        .write_all(b"GET /v1/health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        .unwrap();
    let answer = read_to_close(&mut third).expect("the third connection was served");
    assert!(answer.starts_with("HTTP/1.1 200"), "{answer}");
}

#[test]
fn an_answer_says_whether_it_was_searched_for_by_meaning() {
    let s = Server::new();
    assert_eq!(
        s.post(WRITER, event("1", "The launch is on Thursday."))
            .status,
        StatusCode::OK
    );
    let recall = |body: Value| s.call("POST", "/v1/recall", Some(READER), Some(body));

    // This engine has no embedder, so the words are what is searched.
    let reply = recall(json!({"query": "launch"}));
    assert_eq!(reply.status, StatusCode::OK);
    assert_eq!(
        (&reply.body["found"], &reply.body["meaning"]),
        (&json!("matched"), &json!("not_configured"))
    );
    assert_eq!(reply.body["beads"][0]["by"], "words");
    assert!(reply.body["beads"][0].get("distance").is_none());

    let reply = recall(json!({"query": "launch", "search": "words"}));
    assert_eq!(reply.body["meaning"], "not_asked");
    // Asked for meaning alone, the answer says nothing was searched: it
    // does not say nothing matched.
    let reply = recall(json!({"query": "launch", "search": "meaning"}));
    assert_eq!(reply.status, StatusCode::OK);
    assert_eq!(
        (&reply.body["found"], &reply.body["meaning"]),
        (&json!("not_searched"), &json!("not_configured"))
    );
    assert_eq!(
        recall(json!({"query": "launch", "search": "both"})).outcome(),
        (StatusCode::BAD_REQUEST, "malformed")
    );
}

#[test]
fn a_question_matches_any_of_its_words_unless_all_are_asked_for() {
    let s = Server::new();
    assert_eq!(
        s.post(WRITER, event("1", "The launch is on Thursday."))
            .status,
        StatusCode::OK
    );
    let recall = |body: Value| s.call("POST", "/v1/recall", Some(READER), Some(body));

    // Asked as it would be said, the question finds the chunk that holds
    // some of its words, and the answer says the words were searched.
    let question = "Is the launch still on Thursday, or was it moved?";
    let reply = recall(json!({"query": question}));
    assert_eq!(reply.status, StatusCode::OK);
    assert_eq!(
        (&reply.body["found"], &reply.body["words"]),
        (&json!("matched"), &json!("used"))
    );
    // An answer that was not searched by meaning says nothing of vectors.
    assert!(reply.body.get("compared").is_none());

    // Asked for every word, nothing holds them all.
    let reply = recall(json!({"query": "launch Thursday moved", "match": "all"}));
    assert_eq!(reply.body["found"], "no_match");
    let reply = recall(json!({"query": "launch Thursday", "match": "all"}));
    assert_eq!(reply.body["found"], "matched");
    assert_eq!(
        recall(json!({"query": "launch", "match": "some"})).outcome(),
        (StatusCode::BAD_REQUEST, "malformed")
    );
    // Asked for meaning alone, the words were not searched.
    let reply = recall(json!({"query": "launch", "search": "meaning"}));
    assert_eq!(reply.body["words"], "not_asked");
}

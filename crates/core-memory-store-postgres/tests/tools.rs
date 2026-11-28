//! Reading tool calls with a model, against PostgreSQL and a stand-in
//! model: one bead to a call, shortened text, consent, receipts, repair,
//! retries and erasure.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use common::{CONFIG, World, behind_a_migration, chat, engine_with, tool_call};
use core_memory_contract::{BeadId, EventId, ToolCallDetails, ToolStatus};
use core_memory_core::model::{
    FnTransport, ModelRequest, ModelResponse, ScriptedTransport, TransportError, Usage,
};
use core_memory_core::store::{
    CallFinish, CallResult, Change, Permit, PermitRequest, Purpose, ScopeState, Sending, Store,
    ToolOutcomeWrite, WorkItem, WorkNext, WorkUpdate,
};
use core_memory_core::{Clock, ReadReport, ReadRound};
use core_memory_store_postgres::testing::unique_name;
use core_memory_store_postgres::{ConnectConfig, PostgresStore, QueuedReads, Reread};
use serde_json::{Value, json};

const DESTINATION: &str = "models.test:8080";

fn tool_config(extra: &str) -> String {
    format!(
        r#"{CONFIG}
[tool_reader]
binding = "small"
max_attempts = 3
retry_backoff_ms = 1000

[[binding]]
id = "small"
provider = "stand-in"
endpoint = "http://Models.Test:8080/v1"
model = "stand-in-1"
max_output_tokens = 300
{extra}
"#
    )
}

/// A world whose engine reads tool calls, with the model's destination
/// granted to scope `acme`.
fn reading(extra: &str) -> World {
    let w = World::with_config(&tool_config(extra));
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.grant_destination('acme', '{DESTINATION}')"
        ))
        .unwrap();
    w
}

/// The output of a test run: long, repetitive, and with what matters at
/// the end.
fn test_output() -> String {
    let mut lines: Vec<String> = (1..=400).map(|i| format!("test case_{i} ... ok")).collect();
    lines.push("test result: FAILED. 41 passed; 3 failed".into());
    lines.join("\n")
}

/// A failing test run, as an agent's shell tool reported it.
fn test_run(external_id: &str) -> core_memory_contract::SourceEvent {
    tool_call(
        external_id,
        "Bash",
        json!({"timeout": 600, "command": "cargo test -p billing"}),
        &test_output(),
        Some(true),
    )
}

/// The arguments of [`test_run`] as they are stored: canonical JSON.
const INPUT: &str = r#"{"command":"cargo test -p billing","timeout":600}"#;

fn said(action: &str, outcome: &str, status: &str) -> Result<ModelResponse, TransportError> {
    Ok(ModelResponse {
        text: json!({"action": action, "outcome": outcome, "status": status}).to_string(),
        usage: Usage {
            input_tokens: 100,
            output_tokens: 20,
            ..Usage::default()
        },
    })
}

/// A valid reply, whatever was asked.
fn tests_failed(_: &ModelRequest) -> Result<ModelResponse, TransportError> {
    said(
        "Ran the unit tests of the billing package",
        "41 tests passed and 3 failed",
        "failed",
    )
}

fn handled(round: ReadRound) -> ReadReport {
    match round {
        ReadRound::Handled(report) => report,
        ReadRound::Idle => panic!("nothing was due"),
    }
}

fn round(w: &mut World, transport: &dyn core_memory_core::ModelTransport) -> ReadRound {
    w.engine.read_tool_once(&mut w.store, transport).unwrap()
}

/// What kinds of change the workspace has seen, in order.
fn kinds(w: &mut World) -> Vec<&'static str> {
    w.changes().iter().map(common::kind_of).collect()
}

#[test]
fn a_tool_call_is_one_bead_read_whole_and_recorded_in_a_few_words() {
    let mut w = reading("");
    let (event_id, chunk_ids) = w.accept(&test_run("toolu_01"));
    // Its text is kept whole, in several chunks. Among beads it is one.
    assert!(chunk_ids.len() >= 2, "{}", chunk_ids.len());
    let bead =
        w.db.connect()
            .query_one(
                "SELECT count(*), min(kind), bool_and(bead_id = event_id AND chunk_id IS NULL)
                   FROM core_memory.beads",
                &[],
            )
            .unwrap();
    assert_eq!(
        (
            bead.get::<_, i64>(0),
            bead.get::<_, &str>(1),
            bead.get::<_, bool>(2)
        ),
        (1, "tool", true)
    );
    let stored = w.export().remove(0);
    let text: String = stored.chunks.iter().map(|c| c.text.as_str()).collect();
    assert_eq!(text, format!("{INPUT}\n{}", test_output()));
    assert_eq!(
        stored.event.details.tool_call,
        Some(ToolCallDetails {
            tool: "Bash".into(),
            input_bytes: INPUT.len() as u64,
            is_error: Some(true),
        })
    );
    // One piece of work for the call, and none for its chunks.
    let work =
        w.db.connect()
            .query("SELECT kind, item_id FROM core_memory.work", &[])
            .unwrap();
    assert_eq!(work.len(), 1);
    assert_eq!(work[0].get::<_, &str>(0), "tool_read");
    assert_eq!(work[0].get::<_, uuid::Uuid>(1), event_id.0);

    let transport = ScriptedTransport::new(vec![tests_failed(&ModelRequest {
        system: String::new(),
        user: String::new(),
        response_schema: Value::Null,
        enforce_schema: true,
        max_output_tokens: 0,
    })]);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        report,
        ReadReport {
            scope: "acme".into(),
            chunks: 0,
            tool_calls: 1,
            stored: 1,
            dropped: 0,
            calls: 1,
            input_tokens: 100,
            output_tokens: 20,
            problem: None,
            ..ReadReport::default()
        }
    );

    // The model was shown the tool, its arguments, and its output with the
    // repetition taken out by code.
    let requests = transport.requests();
    assert_eq!(requests.len(), 1);
    let shown: Value = serde_json::from_str(&requests[0].user).unwrap();
    assert_eq!(shown["tool"], "Bash");
    assert_eq!(shown["tool_reported_error"], true);
    assert_eq!(shown["input"], INPUT);
    let output_shown = shown["output"].as_str().unwrap();
    assert!(
        output_shown.starts_with("test case_1 ... ok\n[... 399 more lines like the one above]"),
        "{output_shown}"
    );
    assert!(output_shown.ends_with("test result: FAILED. 41 passed; 3 failed"));
    let sent = (INPUT.len() + output_shown.len()) as u64;
    assert!(sent < text.len() as u64 / 10);

    // What it wrote is stored as the record of the call.
    let Some(Change::ToolOutcome { scope, outcome }) = w.changes().pop() else {
        panic!("the last change is the tool outcome");
    };
    assert_eq!(scope, "acme");
    assert_eq!(outcome.event_id, event_id);
    assert_eq!(outcome.bead_id, BeadId::from(event_id));
    assert_eq!(outcome.status, ToolStatus::Failed);
    assert_eq!(outcome.action, "Ran the unit tests of the billing package");
    assert_eq!(outcome.outcome, "41 tests passed and 3 failed");
    assert_eq!(
        (outcome.compressor.id.as_str(), outcome.compressor.rev),
        ("tool.lines", 1)
    );
    assert_eq!(outcome.compressed_bytes, sent);
    assert_eq!(outcome.reader.model, "stand-in-1");
    assert_eq!(outcome.reader.binding_id, "small");

    // The receipt names every chunk of the call and counts what left,
    // which is less than what is stored.
    let receipt =
        w.db.connect()
            .query_one(
                "SELECT o.destination, o.purpose, o.attempt, o.chunk_ids, o.bytes_sent, o.seq,
                        r.result, o.call_id
                   FROM core_memory.outbound_calls AS o
                   JOIN core_memory.outbound_results AS r USING (call_id)",
                &[],
            )
            .unwrap();
    assert_eq!(receipt.get::<_, &str>(0), DESTINATION);
    assert_eq!(receipt.get::<_, &str>(1), "tool_read");
    assert_eq!(receipt.get::<_, i32>(2), 1);
    assert_eq!(
        receipt.get::<_, Vec<uuid::Uuid>>(3),
        chunk_ids.iter().map(|c| c.0).collect::<Vec<_>>()
    );
    assert_eq!(receipt.get::<_, i64>(4), sent as i64);
    assert_eq!(receipt.get::<_, &str>(6), "accepted");
    assert_eq!(outcome.call_id.0, receipt.get::<_, uuid::Uuid>(7));
    // Event 1, bead 2, receipt 3, outcome 4.
    assert_eq!((receipt.get::<_, i64>(5), outcome.seq), (3, 4));

    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
}

#[test]
fn a_tool_call_is_not_read_as_chunks_and_a_message_is_not_read_as_a_tool_call() {
    // Both readers, on one binding.
    let mut w = reading("[reader]\nbinding = \"small\"\n");
    w.accept(&test_run("toolu_01"));
    w.accept(&chat("1", "We ship on Friday."));
    let work: Vec<(String, i64)> =
        w.db.connect()
            .query(
                "SELECT kind, count(*) FROM core_memory.work GROUP BY kind ORDER BY kind",
                &[],
            )
            .unwrap()
            .iter()
            .map(|row| (row.get(0), row.get(1)))
            .collect();
    assert_eq!(work, [("chunk_read".into(), 1), ("tool_read".into(), 1)]);

    // Each reader is given its own kind of work, and says so in its
    // receipts. The two differ as readers although the binding is one.
    let tools = FnTransport(tests_failed);
    let report = handled(round(&mut w, &tools));
    assert_eq!((report.tool_calls, report.chunks, report.stored), (1, 0, 1));
    assert_eq!(round(&mut w, &tools), ReadRound::Idle);
    let chunks = FnTransport(|request: &ModelRequest| {
        let user: Value = serde_json::from_str(request.user.lines().next().unwrap()).unwrap();
        assert_eq!(user["chunks"].as_array().unwrap().len(), 1);
        Ok(ModelResponse {
            text: json!({"items": [{"chunk": "C1", "type": "decision"}]}).to_string(),
            usage: Usage::default(),
        })
    });
    let report = handled(w.engine.read_once(&mut w.store, &chunks).unwrap());
    assert_eq!((report.tool_calls, report.chunks, report.stored), (0, 1, 1));
    let purposes: Vec<String> =
        w.db.connect()
            .query(
                "SELECT purpose FROM core_memory.outbound_calls ORDER BY seq",
                &[],
            )
            .unwrap()
            .iter()
            .map(|row| row.get(0))
            .collect();
    assert_eq!(purposes, ["tool_read", "chunk_read"]);
    assert_eq!(
        w.count("SELECT count(DISTINCT binding_rev) FROM core_memory.outbound_calls"),
        2
    );
}

#[test]
fn nothing_of_a_tool_call_is_sent_without_consent_or_while_its_scope_is_not_active() {
    let mut w = World::with_config(&tool_config(""));
    w.accept(&test_run("toolu_01"));
    let transport = ScriptedTransport::new(vec![]);

    let report = handled(round(&mut w, &transport));
    assert_eq!((report.problem, report.calls), (Some("no_consent"), 0));
    assert!(transport.requests().is_empty());
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        0
    );
    // The attempt did not count, and the work waits.
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'tool_read' AND state = 'ready' AND attempts = 0
                AND problem = 'no_consent'"
        ),
        1
    );
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);

    // Granted, a paused scope is still not read. Resumed, it is, at once.
    assert!(w.store.grant_destination("acme", DESTINATION).unwrap());
    w.store.set_scope_state("acme", ScopeState::Paused).unwrap();
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.problem, report.calls),
        (Some("scope_not_active"), 0)
    );
    w.store.set_scope_state("acme", ScopeState::Active).unwrap();
    let transport = FnTransport(tests_failed);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);

    // Revoked, the next call is not sent.
    assert!(w.store.revoke_destination("acme", DESTINATION).unwrap());
    w.accept(&test_run("toolu_02"));
    assert_eq!(
        handled(round(&mut w, &transport)).problem,
        Some("no_consent")
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
}

#[test]
fn a_reply_that_breaks_the_shape_is_repaired_once() {
    let mut w = reading("");
    w.accept(&test_run("toolu_01"));
    let too_long = "a".repeat(121);
    let transport = ScriptedTransport::new(vec![
        said(&too_long, "41 tests passed and 3 failed", "failed"),
        said("Ran the billing tests", "41 passed and 3 failed", "failed"),
    ]);
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.stored, report.calls, report.problem), (1, 2, None));
    // The repair shows the model its reply and what was wrong with it.
    let requests = transport.requests();
    assert!(
        requests[1]
            .user
            .contains("121 characters; the limit is 120")
    );
    // Two calls, two receipts: the first rejected, the second its repair.
    let calls =
        w.db.connect()
            .query(
                "SELECT o.attempt, r.result, o.repairs IS NOT NULL, o.bytes_sent
                   FROM core_memory.outbound_calls AS o
                   JOIN core_memory.outbound_results AS r USING (call_id)
                  ORDER BY o.seq",
                &[],
            )
            .unwrap();
    let calls: Vec<(i32, &str, bool, i64)> = calls
        .iter()
        .map(|row| (row.get(0), row.get(1), row.get(2), row.get(3)))
        .collect();
    assert_eq!(calls.len(), 2);
    assert_eq!((calls[0].0, calls[0].1, calls[0].2), (1, "rejected", false));
    assert_eq!((calls[1].0, calls[1].1, calls[1].2), (2, "accepted", true));
    // Both showed the same shortened text.
    assert_eq!(calls[0].3, calls[1].3);
    let Some(Change::ToolOutcome { outcome, .. }) = w.changes().pop() else {
        panic!("the last change is the tool outcome");
    };
    assert_eq!(outcome.action, "Ran the billing tests");
    // The rejected reply left nothing behind.
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 1);
}

#[test]
fn a_tool_call_no_reply_fits_is_tried_again_and_then_set_aside() {
    let mut w = reading("");
    w.accept(&test_run("toolu_01"));
    // Never valid: the outcome has a line break in it.
    let transport =
        FnTransport(|_: &ModelRequest| said("Ran the tests", "failed\nbadly", "failed"));
    for attempt in 1..=3 {
        let report = handled(round(&mut w, &transport));
        assert_eq!(
            (report.stored, report.calls, report.problem),
            (0, 2, Some("reply_invalid")),
            "attempt {attempt}"
        );
        // Not before the backoff has passed.
        assert_eq!(round(&mut w, &transport), ReadRound::Idle);
        w.clock.advance_millis(1_000);
    }
    // After three attempts it is set aside for an operator.
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'tool_read' AND state = 'failed' AND attempts = 3
                AND problem = 'reply_invalid'"
        ),
        1
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 0);
    let counts = w.store.work_counts(Purpose::ToolRead).unwrap();
    assert_eq!((counts.ready, counts.failed), (0, 1));
    assert_eq!(counts.problems, [("reply_invalid".to_string(), 1)]);
    assert_eq!(w.store.work_counts(Purpose::ChunkRead).unwrap().failed, 0);

    // Told to, the engine tries again from the start.
    let now = w.clock.now();
    assert_eq!(w.store.retry_failed_reads(Some("acme"), now).unwrap(), 1);
    let transport = FnTransport(tests_failed);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
}

#[test]
fn a_call_that_fails_is_recorded_and_tried_again_later() {
    let mut w = reading("");
    w.accept(&test_run("toolu_01"));
    let transport = ScriptedTransport::new(vec![
        Err(TransportError::Transient {
            message: "the provider is overloaded".into(),
            retry_after_ms: Some(5_000),
        }),
        Err(TransportError::InDoubt {
            message: "timed out after sending".into(),
        }),
        tests_failed(&ModelRequest {
            system: String::new(),
            user: String::new(),
            response_schema: Value::Null,
            enforce_schema: true,
            max_output_tokens: 0,
        }),
    ]);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.stored, report.calls, report.problem),
        (0, 1, Some("provider_unavailable"))
    );
    // The provider said when to come back, and that is when.
    w.clock.advance_millis(4_000);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    w.clock.advance_millis(1_000);
    let report = handled(round(&mut w, &transport));
    assert_eq!(report.problem, Some("call_in_doubt"));
    w.clock.advance_millis(60_000);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);

    let results: Vec<String> =
        w.db.connect()
            .query(
                "SELECT r.result FROM core_memory.outbound_calls AS o
                   JOIN core_memory.outbound_results AS r USING (call_id)
                  ORDER BY o.seq",
                &[],
            )
            .unwrap()
            .iter()
            .map(|row| row.get(0))
            .collect();
    assert_eq!(results, ["not_sent", "in_doubt", "accepted"]);
    // No message of the provider's is kept anywhere.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE problem LIKE '%overloaded%'"),
        0
    );
}

#[test]
fn a_call_the_model_declines_is_not_made_again() {
    let mut w = reading("");
    w.accept(&test_run("toolu_01"));
    let transport = ScriptedTransport::new(vec![Err(TransportError::Refused {
        message: "the request does not fit the context window".into(),
    })]);
    // A tool call is sent alone, so the same request would be declined
    // again. It is set aside at once, whatever attempts are left.
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.calls, report.problem), (1, Some("model_refused")));
    w.clock.advance_millis(600_000);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    assert_eq!(transport.requests().len(), 1);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'tool_read' AND state = 'failed' AND attempts = 1
                AND problem = 'model_refused'"
        ),
        1
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_results WHERE result = 'refused'"),
        1
    );
}

#[test]
fn an_attempt_counts_once_a_call_was_made_for_it() {
    // The day's budget allows one request, and the model's reply is never
    // valid: each day one call is made and its repair is refused.
    let mut w =
        reading("[binding.budget]\nmax_requests_per_day = 1\nmax_tokens_per_day = 100000\n");
    w.accept(&test_run("toolu_01"));
    let transport =
        FnTransport(|_: &ModelRequest| said("Ran the tests", "failed\nbadly", "failed"));
    for day in 1..=3 {
        let report = handled(round(&mut w, &transport));
        assert_eq!(
            (report.calls, report.problem),
            (1, Some("over_budget")),
            "day {day}"
        );
        w.clock.advance_millis(86_400_000);
    }
    // Three calls were made for it, which is as many as it gets.
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'tool_read' AND state = 'failed' AND attempts = 3
                AND problem = 'over_budget'"
        ),
        1
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        3
    );

    // A permit refused before any call is another matter: nothing was
    // tried, so nothing is counted.
    let mut w = reading("[binding.budget]\nmax_requests_per_day = 1\nmax_tokens_per_day = 10\n");
    w.accept(&test_run("toolu_01"));
    for _ in 0..5 {
        let report = handled(round(&mut w, &transport));
        assert_eq!((report.calls, report.problem), (0, Some("over_budget")));
        w.clock.advance_millis(86_400_000);
    }
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE state = 'ready' AND attempts = 0"),
        1
    );
}

#[test]
fn a_tool_call_is_handed_out_only_as_often_as_it_may_be_tried() {
    let mut w = reading("");
    let (event_id, _) = w.accept(&test_run("toolu_01"));
    // A worker takes the call and dies with it, three times over.
    for attempt in 1..=3 {
        let now = w.clock.now();
        let call = w
            .store
            .claim_tool_read(3, now, now.plus_millis(60_000))
            .unwrap()
            .unwrap();
        assert_eq!((call.event_id, call.attempts), (event_id, attempt));
        // Reserved until its lease runs out.
        assert!(
            w.store
                .claim_tool_read(3, now, now.plus_millis(60_000))
                .unwrap()
                .is_none()
        );
        w.clock.advance_millis(60_001);
    }
    // It is not handed out a fourth time. It is set aside, with the reason.
    let now = w.clock.now();
    assert!(
        w.store
            .claim_tool_read(3, now, now.plus_millis(60_000))
            .unwrap()
            .is_none()
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'tool_read' AND state = 'failed' AND attempts = 3
                AND leased_until IS NULL AND problem = 'attempts_spent'"
        ),
        1
    );
    let counts = w.store.work_counts(Purpose::ToolRead).unwrap();
    assert_eq!(counts.problems, [("attempts_spent".to_string(), 1)]);
    // An operator puts it back, and it is read.
    assert_eq!(w.store.retry_failed_reads(Some("acme"), now).unwrap(), 1);
    let transport = FnTransport(tests_failed);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
}

#[test]
fn a_token_count_no_column_can_hold_does_not_lose_the_reply() {
    let mut w = reading("");
    w.accept(&test_run("toolu_01"));
    // A provider's count of its own tokens is a number from outside.
    let transport = FnTransport(|_: &ModelRequest| {
        Ok(ModelResponse {
            text: json!({"action": "Ran the tests", "outcome": "3 failed", "status": "failed"})
                .to_string(),
            usage: Usage {
                input_tokens: u64::MAX,
                output_tokens: u64::MAX - 1,
                ..Usage::default()
            },
        })
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.stored, report.problem), (1, None));
    assert_eq!(report.input_tokens, u64::MAX);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.outbound_results
              WHERE result = 'accepted' AND input_tokens = 9223372036854775807
                AND output_tokens = 9223372036854775807"
        ),
        1
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);

    // Two calls with such counts in one round do not overflow the sum.
    w.clock.advance_millis(1);
    w.accept(&test_run("toolu_02"));
    let replies = ScriptedTransport::new(vec![
        Ok(ModelResponse {
            text: "not json".into(),
            usage: Usage {
                input_tokens: u64::MAX,
                output_tokens: 1,
                ..Usage::default()
            },
        }),
        Ok(ModelResponse {
            text: json!({"action": "Ran the tests", "outcome": "3 failed", "status": "failed"})
                .to_string(),
            usage: Usage {
                input_tokens: u64::MAX,
                output_tokens: 1,
                ..Usage::default()
            },
        }),
    ]);
    let report = handled(round(&mut w, &replies));
    assert_eq!(
        (report.stored, report.calls, report.input_tokens),
        (1, 2, u64::MAX)
    );
}

#[test]
fn a_reply_is_not_lost_because_the_workspace_was_busy_when_it_came() {
    let mut w = reading("");
    w.accept(&test_run("toolu_01"));
    // A store whose statements give up after a second.
    let mut config = ConnectConfig::new(&w.db.url);
    config.statement_timeout = std::time::Duration::from_millis(1_000);
    let mut impatient = PostgresStore::new(&config).unwrap();

    // While the model answers, a host transaction takes the workspace and
    // holds it for longer than one statement waits.
    let url = w.db.url.clone();
    let holder: std::sync::Mutex<Option<std::thread::JoinHandle<()>>> = Default::default();
    let transport = FnTransport(|request: &ModelRequest| {
        let mut host = postgres::Client::connect(&url, postgres::NoTls).unwrap();
        host.batch_execute("BEGIN; SELECT core_memory.set_scope_state('acme', 'active');")
            .unwrap();
        *holder.lock().unwrap() = Some(std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(1_500));
            host.batch_execute("COMMIT").unwrap();
        }));
        tests_failed(request)
    });
    let started = std::time::Instant::now();
    let round = w.engine.read_tool_once(&mut impatient, &transport).unwrap();
    // The first try to record the reply gave up; the next one waited and
    // got through. The call is not made again.
    assert!(started.elapsed() >= std::time::Duration::from_millis(1_000));
    let report = handled(round);
    assert_eq!((report.stored, report.calls, report.problem), (1, 1, None));
    holder.lock().unwrap().take().unwrap().join().unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.outbound_calls AS o
               JOIN core_memory.outbound_results AS r USING (call_id)"
        ),
        1
    );
}

#[test]
fn the_end_of_a_call_is_recorded_once() {
    let mut w = reading("");
    let (event_id, chunk_ids) = w.accept(&test_run("toolu_01"));
    let engine = &w.engine;
    let reader = engine.tool_reader().unwrap();
    let now = w.clock.now();
    let Permit::Granted { call_id, .. } = w
        .store
        .permit(&PermitRequest {
            scope: "acme",
            chunk_ids: &chunk_ids,
            purpose: Purpose::ToolRead,
            destination: DESTINATION,
            reader: &reader.reader_ref,
            attempt: 1,
            repairs: None,
            sending: &|_| {
                Some(Sending {
                    bytes: 10,
                    reserved_tokens: 10,
                })
            },
            budget: None,
            at: now,
        })
        .unwrap()
    else {
        panic!("the call was not permitted");
    };
    let outcome = ToolOutcomeWrite {
        event_id,
        status: ToolStatus::Failed,
        action: "Ran the tests".into(),
        outcome: "3 failed".into(),
        compressor: core_memory_contract::CompressorRef {
            id: "tool.lines".into(),
            rev: 1,
        },
        compressed_bytes: 10,
    };
    let finish = CallFinish {
        call_id,
        scope: "acme",
        result: CallResult::Accepted,
        usage: None,
        reads: &[],
        tool_outcome: Some(&outcome),
        vocabulary: None,
        reader: &reader.reader_ref,
        work: &[],
        at: now,
        judge_after: None,
    };
    // A caller whose answer was lost on the way back asks again. Nothing
    // is stored twice, and it is told what was stored.
    for _ in 0..2 {
        let finished = w.store.finish_call(&finish).unwrap();
        assert_eq!((finished.stored, finished.dropped), (1, 0));
    }
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 1);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_results"),
        1
    );
}

#[test]
fn what_a_model_is_sent_is_never_longer_than_what_is_stored() {
    let mut w = reading("");
    // Trouble on every other line: nothing here can be left out for less
    // room than it takes.
    let output = "error\nx\n".repeat(2_000);
    w.accept(&tool_call("noisy", "Bash", json!({}), &output, None));
    let transport = FnTransport(tests_failed);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    let row =
        w.db.connect()
            .query_one(
                "SELECT (SELECT sum(octet_length(text)) FROM core_memory.chunk_text)::bigint,
                        o.bytes_sent, t.compressed_bytes
                   FROM core_memory.outbound_calls AS o
                   JOIN core_memory.tool_outcomes AS t USING (call_id)",
                &[],
            )
            .unwrap();
    let (stored, sent, compressed): (i64, i64, i64) = (row.get(0), row.get(1), row.get(2));
    assert_eq!(sent, compressed);
    assert!(sent <= stored, "{sent} bytes sent of {stored} stored");
}

#[test]
fn nothing_is_stored_about_a_tool_call_that_was_erased() {
    let mut w = reading("");
    let (kept, _) = w.accept(&test_run("toolu_01"));
    w.clock.advance_millis(1);
    let (gone, _) = w.accept(&test_run("toolu_02"));
    let transport = FnTransport(tests_failed);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    w.store.erase_event(&gone, "user_request", "owner").unwrap();

    // The engine checks this where it stores an outcome. The table checks
    // it too, for a statement that does not: even the table's owner cannot
    // add a row about an erased call, which no erase would reach again.
    let mut admin = w.db.connect();
    let mut forged = |event: EventId| {
        admin.execute(
            "INSERT INTO core_memory.tool_outcomes (
                 workspace_id, seq, event_id, scope, call_id, status, action, outcome,
                 compressor_id, compressor_rev, compressed_bytes, binding_id, binding_rev,
                 model, prompt_rev, read_at
             )
             SELECT o.workspace_id, 900001, $1, o.scope, o.call_id, o.status, o.action,
                    o.outcome, o.compressor_id, o.compressor_rev, o.compressed_bytes,
                    o.binding_id, o.binding_rev, o.model, o.prompt_rev, o.read_at
               FROM core_memory.tool_outcomes AS o LIMIT 1",
            &[&event.0],
        )
    };
    let refused = forged(gone).unwrap_err();
    let refused = refused.as_db_error().unwrap().message();
    assert!(
        refused.contains("only for an event that is live"),
        "{refused}"
    );
    assert_eq!(
        w.count(&format!(
            "SELECT count(*) FROM core_memory.tool_outcomes WHERE event_id = '{}'",
            gone.0
        )),
        0
    );
    assert_eq!(kinds(&mut w), ["event", "tool outcome", "erasure"]);
    // For a call that is live, the same statement is taken.
    assert_eq!(forged(kept).unwrap(), 1);
}

#[test]
fn erasing_a_tool_call_removes_what_was_read_in_it_and_what_was_still_owed() {
    let mut w = reading("");
    // Admitted a moment apart, they are read in that order.
    let (read, _) = w.accept(&test_run("toolu_01"));
    w.clock.advance_millis(1);
    let (unread, _) = w.accept(&test_run("toolu_02"));
    w.clock.advance_millis(1);
    let (during, _) = w.accept(&test_run("toolu_03"));
    let transport = FnTransport(tests_failed);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 1);

    // One that was read, and one that was still waiting.
    w.store.erase_event(&read, "user_request", "owner").unwrap();
    w.store
        .erase_event(&unread, "user_request", "owner")
        .unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 0);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 1);
    // The receipt of the call that sent it stays: it holds IDs only.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
    assert_eq!(kinds(&mut w), ["event", "erasure", "erasure"]);

    // One erased while the model is reading it: what the model then says
    // is not stored.
    let url = w.db.url.clone();
    let transport = FnTransport(move |request: &ModelRequest| {
        PostgresStore::new(&ConnectConfig::new(url.clone()))
            .unwrap()
            .erase_event(&during, "user_request", "owner")
            .unwrap();
        tests_failed(request)
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.stored, report.dropped), (0, 1));
    for table in ["tool_outcomes", "chunk_text", "work"] {
        assert_eq!(
            w.count(&format!("SELECT count(*) FROM core_memory.{table}")),
            0,
            "{table}"
        );
    }
    assert_eq!(kinds(&mut w), ["erasure", "erasure", "erasure"]);
    // An erased call is not shown to a model again.
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
}

#[test]
fn tool_calls_stored_before_there_was_a_reader_are_queued_when_asked() {
    // No tool reader is configured, so admission queues nothing.
    let mut w = World::new();
    w.accept(&test_run("toolu_01"));
    w.accept(&chat("1", "a message, stored before any reader"));
    let (erased, _) = w.accept(&test_run("toolu_02"));
    w.store
        .erase_event(&erased, "user_request", "owner")
        .unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);

    // The same store, with a tool reader now.
    w.engine = engine_with(&tool_config(""), &w.clock);
    w.store.grant_destination("acme", DESTINATION).unwrap();
    let transport = FnTransport(tests_failed);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    let now = w.clock.now();
    // The live call is queued once, as one piece of work. The message's
    // chunk is queued for a chunk reader, should one be configured.
    assert_eq!(
        w.store.queue_reads("acme", Reread::default(), now).unwrap(),
        QueuedReads::Queued {
            chunks: 1,
            tool_calls: 1,
            embeddings: 0
        }
    );
    assert_eq!(
        w.store.queue_reads("acme", Reread::default(), now).unwrap(),
        QueuedReads::Queued {
            chunks: 0,
            tool_calls: 0,
            embeddings: 0
        }
    );
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);

    // What was read is not queued again, unless the reader has changed
    // and that is asked for.
    let same = w.engine.tool_reader().unwrap().reader_ref.clone();
    let again = |reader| Reread {
        chunks: None,
        tool_calls: Some(reader),
        vectors: None,
    };
    for reread in [Reread::default(), again(&same)] {
        assert_eq!(
            w.store.queue_reads("acme", reread, now).unwrap(),
            QueuedReads::Queued {
                chunks: 0,
                tool_calls: 0,
                embeddings: 0
            }
        );
    }
    let changed = engine_with(
        &tool_config("").replace("stand-in-1", "stand-in-2"),
        &w.clock,
    )
    .tool_reader()
    .unwrap()
    .reader_ref
    .clone();
    assert_eq!(
        w.store.queue_reads("acme", again(&changed), now).unwrap(),
        QueuedReads::Queued {
            chunks: 0,
            tool_calls: 1,
            embeddings: 0
        }
    );
    // The second reading is a second record. The latest is the current one.
    w.engine = engine_with(
        &tool_config("").replace("stand-in-1", "stand-in-2"),
        &w.clock,
    );
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    let models: Vec<String> =
        w.db.connect()
            .query(
                "SELECT model FROM core_memory.tool_outcomes ORDER BY seq",
                &[],
            )
            .unwrap()
            .iter()
            .map(|row| row.get(0))
            .collect();
    assert_eq!(models, ["stand-in-1", "stand-in-2"]);
}

#[test]
fn the_budget_counts_the_text_as_it_is_sent() {
    // What one call may cost: the reply, the instructions, and the call's
    // text once it is shortened. Twice that fits the day; the text as it is
    // stored would not fit once.
    let text = format!("{INPUT}\n{}", test_output());
    let details = ToolCallDetails {
        tool: "Bash".into(),
        input_bytes: INPUT.len() as u64,
        is_error: Some(true),
    };
    let shown = core_memory_core::tool::show(&details, &text).unwrap();
    let instructions = core_memory_core::tool::instructions_bytes();
    let one_call = 300 + (instructions + shown.user.len() as u64) / 3;
    let unshortened = 300 + (instructions + text.len() as u64) / 3;
    assert!(unshortened > 2 * one_call);

    let mut w = reading(&format!(
        "[binding.budget]\nmax_requests_per_day = 100\nmax_tokens_per_day = {}\n",
        2 * one_call
    ));
    for id in ["toolu_01", "toolu_02", "toolu_03"] {
        w.accept(&test_run(id));
    }
    let transport = FnTransport(tests_failed);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.problem, report.calls), (Some("over_budget"), 0));
    assert_eq!(
        w.count("SELECT sum(reserved_tokens)::bigint FROM core_memory.outbound_calls"),
        2 * one_call as i64
    );
}

#[test]
fn text_that_is_not_what_the_event_says_is_not_sent() {
    let mut w = reading("");
    let (event_id, _) = w.accept(&test_run("toolu_01"));
    // Only the owner, with the trigger off, can do this: the event now
    // says its arguments are longer than its whole text.
    w.db.connect()
        .batch_execute(
            "ALTER TABLE core_memory.events DISABLE TRIGGER events_only_erase;
             UPDATE core_memory.events
                SET info = jsonb_set(info, '{tool_call,input_bytes}', '999999');
             ALTER TABLE core_memory.events ENABLE TRIGGER events_only_erase;",
        )
        .unwrap();
    let transport = ScriptedTransport::new(vec![]);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.calls, report.stored, report.problem),
        (0, 0, Some("text_unreadable"))
    );
    assert!(transport.requests().is_empty());
    // No call was permitted, so there is no receipt. The work is set
    // aside for an operator: trying again would find the same text.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        0
    );
    assert_eq!(
        w.count(&format!(
            "SELECT count(*) FROM core_memory.work
              WHERE item_id = '{}' AND state = 'failed' AND problem = 'text_unreadable'",
            event_id.0
        )),
        1
    );
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
}

#[test]
fn reading_a_tool_call_waits_for_a_migration_holding_nothing_else() {
    let mut w = reading("");
    w.accept(&test_run("toolu_01"));
    let mut store = w.db.store();
    store.check().unwrap();

    // One whole round: the claim, the permit and the finish each start a
    // transaction of their own, and the first of them waits.
    let transport = FnTransport(tests_failed);
    {
        let (db, engine) = (&w.db, &w.engine);
        let report = behind_a_migration(db, "read_tool_once", || {
            engine.read_tool_once(&mut store, &transport)
        });
        assert_eq!(handled(report.unwrap()).stored, 1);
    }

    // Then each step by itself.
    let (event_id, _) = w.accept(&test_run("toolu_02"));
    let (db, engine) = (&w.db, &w.engine);
    let now = w.clock.now();
    let claimed = behind_a_migration(db, "claim_tool_read", || {
        store.claim_tool_read(5, now, now.plus_millis(60_000))
    });
    let call = claimed.unwrap().unwrap();
    assert_eq!((call.event_id, call.attempts), (event_id, 1));
    let reader = engine.tool_reader().unwrap();
    let request = PermitRequest {
        scope: "acme",
        chunk_ids: &call.chunk_ids,
        purpose: Purpose::ToolRead,
        destination: DESTINATION,
        reader: &reader.reader_ref,
        attempt: 1,
        repairs: None,
        sending: &|_| {
            Some(Sending {
                bytes: 30,
                reserved_tokens: 10,
            })
        },
        budget: None,
        at: now,
    };
    let permit = behind_a_migration(db, "permit", || store.permit(&request));
    let Permit::Granted { call_id, .. } = permit.unwrap() else {
        panic!("not permitted");
    };
    let outcome = ToolOutcomeWrite {
        event_id,
        status: ToolStatus::Succeeded,
        action: "Ran the tests".into(),
        outcome: "All passed".into(),
        compressor: core_memory_contract::CompressorRef {
            id: "tool.lines".into(),
            rev: 1,
        },
        compressed_bytes: 30,
    };
    let finish = CallFinish {
        call_id,
        scope: "acme",
        result: CallResult::Accepted,
        usage: None,
        reads: &[],
        tool_outcome: Some(&outcome),
        vocabulary: None,
        reader: &reader.reader_ref,
        work: &[],
        at: now,
        judge_after: None,
    };
    let finished = behind_a_migration(db, "finish_call", || store.finish_call(&finish));
    assert_eq!(finished.unwrap().stored, 1);
    let work = [WorkUpdate {
        item: WorkItem::ToolCall(event_id),
        next: WorkNext::Failed,
        problem: "reply_invalid",
    }];
    behind_a_migration(db, "update_work", || store.update_work(&work)).unwrap();
    let counts = behind_a_migration(db, "work_counts", || store.work_counts(Purpose::ToolRead));
    assert_eq!(counts.unwrap().ready, 0);
}

#[test]
fn an_outcome_is_stored_only_for_the_call_its_receipt_names() {
    let mut w = reading("");
    let (first, _) = w.accept(&test_run("toolu_01"));
    w.clock.advance_millis(1);
    let (second, _) = w.accept(&test_run("toolu_02"));
    let now = w.clock.now();
    let call = w
        .store
        .claim_tool_read(5, now, now.plus_millis(60_000))
        .unwrap()
        .unwrap();
    assert_eq!(call.event_id, first);
    let reader = w.engine.tool_reader().unwrap().reader_ref.clone();
    // Two calls, each with a receipt for the first tool call's text.
    let mut permitted = || {
        let Permit::Granted { call_id, .. } = w
            .store
            .permit(&PermitRequest {
                scope: "acme",
                chunk_ids: &call.chunk_ids,
                purpose: Purpose::ToolRead,
                destination: DESTINATION,
                reader: &reader,
                attempt: 1,
                repairs: None,
                sending: &|_| {
                    Some(Sending {
                        bytes: 30,
                        reserved_tokens: 10,
                    })
                },
                budget: None,
                at: now,
            })
            .unwrap()
        else {
            panic!("not permitted");
        };
        call_id
    };
    let (one, another) = (permitted(), permitted());
    // An outcome offered with such a receipt for the second tool call is
    // not stored.
    let outcome = |event_id: EventId| ToolOutcomeWrite {
        event_id,
        status: ToolStatus::Unknown,
        action: "Did something".into(),
        outcome: "Something came of it".into(),
        compressor: core_memory_contract::CompressorRef {
            id: "tool.lines".into(),
            rev: 1,
        },
        compressed_bytes: 30,
    };
    let mut finish = |call_id, event_id: EventId| {
        let outcome = outcome(event_id);
        w.store
            .finish_call(&CallFinish {
                call_id,
                scope: "acme",
                result: CallResult::Accepted,
                usage: None,
                reads: &[],
                tool_outcome: Some(&outcome),
                vocabulary: None,
                reader: &reader,
                work: &[],
                at: now,
                judge_after: None,
            })
            .unwrap()
    };
    let wrong = finish(one, second);
    assert_eq!((wrong.stored, wrong.dropped), (0, 1));
    let right = finish(another, first);
    assert_eq!((right.stored, right.dropped), (1, 0));
    assert_eq!(
        w.count(&format!(
            "SELECT count(*) FROM core_memory.tool_outcomes WHERE event_id = '{}'",
            first.0
        )),
        1
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 1);
}

#[test]
fn the_engine_role_reads_tool_calls_and_cannot_rewrite_what_was_read() {
    let mut w = reading("");
    let (event_id, _) = w.accept(&test_run("toolu_01"));
    let role = unique_name("cm_t_tool_engine");
    let mut admin = w.db.connect();
    admin
        .batch_execute(&format!(
            "CREATE ROLE {role} LOGIN PASSWORD 'synthetic';
             GRANT core_memory_engine TO {role};"
        ))
        .unwrap();
    let (scheme, rest) = w.db.url.split_once("://").unwrap();
    let (_, host) = rest.split_once('@').unwrap();
    let url = format!("{scheme}://{role}:synthetic@{host}");
    // The whole round with the engine's privileges and nothing more: it
    // admits a call, reads it, and stores what was read.
    let mut store = PostgresStore::new(&ConnectConfig::new(url.clone())).unwrap();
    w.clock.advance_millis(1);
    let admitted = w
        .engine
        .admit(
            &mut store,
            &core_memory_core::ScopeGrants::All,
            &test_run("toolu_02"),
        )
        .unwrap();
    assert!(
        matches!(
            admitted,
            core_memory_contract::AdmissionOutcome::Accepted { .. }
        ),
        "{admitted:?}"
    );
    let transport = FnTransport(tests_failed);
    for _ in 0..2 {
        let round = w.engine.read_tool_once(&mut store, &transport).unwrap();
        assert_eq!(handled(round).stored, 1);
    }

    let mut raw = postgres::Client::connect(&url, postgres::NoTls).unwrap();
    for sql in [
        "UPDATE core_memory.tool_outcomes SET outcome = 'All passed'",
        "DELETE FROM core_memory.tool_outcomes",
        "UPDATE core_memory.beads SET kind = 'chunk'",
    ] {
        let message = match raw.batch_execute(sql) {
            Ok(()) => panic!("`{sql}` was allowed"),
            Err(e) => e
                .as_db_error()
                .map_or(e.to_string(), |d| d.message().to_string()),
        };
        assert!(message.contains("permission denied"), "{sql}: {message}");
    }
    // Even the owner cannot change what was read, or delete it while the
    // call it is about is still there.
    for sql in [
        "UPDATE core_memory.tool_outcomes SET outcome = 'All passed'",
        "DELETE FROM core_memory.tool_outcomes",
        "TRUNCATE core_memory.tool_outcomes",
    ] {
        let message = match admin.batch_execute(sql) {
            Ok(()) => panic!("`{sql}` was allowed"),
            Err(e) => e
                .as_db_error()
                .map_or(e.to_string(), |d| d.message().to_string()),
        };
        assert!(message.contains("cannot be"), "{sql}: {message}");
    }
    // A bead of a tool call is its event, and nothing else.
    let message = match admin.batch_execute(&format!(
        "INSERT INTO core_memory.beads (bead_id, workspace_id, scope, kind, state, event_id,
             created_by, time_at, time_basis, created_at, seq)
         VALUES (gen_random_uuid(), 'w1', 'acme', 'tool', 'live', '{}', 'engine', now(),
             'captured', now(), 999)",
        event_id.0
    )) {
        Ok(()) => panic!("a tool bead with an ID of its own was stored"),
        Err(e) => e
            .as_db_error()
            .map_or(e.to_string(), |d| d.message().to_string()),
    };
    assert!(message.contains("check constraint"), "{message}");

    // What a model wrote about a call is kept out of the planner's
    // statistics, like what the call itself said.
    admin
        .batch_execute("ANALYZE core_memory.tool_outcomes")
        .unwrap();
    let sampled: Vec<String> = admin
        .query(
            "SELECT attname::text FROM pg_stats
              WHERE schemaname = 'core_memory' AND tablename = 'tool_outcomes'
                AND attname IN ('action', 'outcome')",
            &[],
        )
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    assert!(sampled.is_empty(), "{sampled:?}");

    drop(raw);
    drop(store);
    admin
        .batch_execute(&format!("DROP OWNED BY {role}; DROP ROLE {role}"))
        .unwrap();
}

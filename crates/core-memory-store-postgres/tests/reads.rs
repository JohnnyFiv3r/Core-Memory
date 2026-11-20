//! Reading chunks with a model, against PostgreSQL and a stand-in model:
//! consent, receipts, validation, repair, retries and erasure.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use std::sync::{Arc, Mutex};

use common::{CONFIG, World, behind_a_migration, chat, chat_in, crm, engine_with};
use core_memory_contract::{Abstention, EntityKind, ReadDecision, SpanKind};
use core_memory_core::model::{
    FnTransport, ModelRequest, ModelResponse, ScriptedTransport, TransportError, Usage,
};
use core_memory_core::read::Fault;
use core_memory_core::store::{
    CallFinish, CallResult, Change, Permit, PermitRequest, Purpose, ReadWrite, Sending, Store,
};
use core_memory_core::{Clock, ReadReport, ReadRound};
use core_memory_store_postgres::{ConnectConfig, PostgresStore, QueuedReads, Reread};
use serde_json::{Value, json};

const DESTINATION: &str = "models.test:8080";

fn reader_config(extra: &str) -> String {
    format!(
        r#"{CONFIG}
[reader]
binding = "small"
batch_size = 4
max_attempts = 3
retry_backoff_ms = 1000

[[binding]]
id = "small"
provider = "stand-in"
endpoint = "http://Models.Test:8080/v1"
model = "stand-in-1"
max_output_tokens = 512
{extra}
"#
    )
}

/// A world whose engine reads, with the model's destination granted to
/// scope `acme`.
fn reading(extra: &str) -> World {
    let w = World::with_config(&reader_config(extra));
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.grant_destination('acme', '{DESTINATION}')"
        ))
        .unwrap();
    w
}

/// The chunks a request shows the model, as `(handle, role, text)`.
fn shown(request: &ModelRequest) -> Vec<(String, Option<String>, String)> {
    let user: Value = serde_json::from_str(request.user.lines().next().unwrap()).unwrap();
    user["chunks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| {
            (
                c["chunk"].as_str().unwrap().to_string(),
                c["role"].as_str().map(str::to_string),
                c["text"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

fn reply(items: Vec<Value>) -> Result<ModelResponse, TransportError> {
    Ok(ModelResponse {
        text: json!({"items": items}).to_string(),
        usage: Usage {
            input_tokens: 100,
            output_tokens: 20,
        },
    })
}

/// A valid reply that gives every chunk shown the type `decision`.
fn decide_all(request: &ModelRequest) -> Result<ModelResponse, TransportError> {
    reply(
        shown(request)
            .iter()
            .map(|(handle, _, _)| json!({"chunk": handle, "type": "decision"}))
            .collect(),
    )
}

fn handled(round: ReadRound) -> ReadReport {
    match round {
        ReadRound::Handled(report) => report,
        ReadRound::Idle => panic!("nothing was due"),
    }
}

fn round(w: &mut World, transport: &dyn core_memory_core::ModelTransport) -> ReadRound {
    w.engine.read_once(&mut w.store, transport).unwrap()
}

#[test]
fn a_chunk_is_read_and_what_was_found_is_stored_as_ranges() {
    let mut w = reading("");
    let text = "Agreed. Priya will take OPS-412 and get the export under an hour.";
    let (_, chunk_ids) = w.accept(&chat("1", text));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 1);

    let transport = ScriptedTransport::new(vec![reply(vec![json!({
        "chunk": "C1", "type": "decision",
        "entities": [{"text": "Priya", "kind": "person"}, {"text": "OPS-412", "kind": "artifact"}],
        "actions": [{"text": "get the export under an hour"}],
        "outcomes": []
    })])]);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        report,
        ReadReport {
            scope: "acme".into(),
            chunks: 1,
            tool_calls: 0,
            stored: 1,
            dropped: 0,
            calls: 1,
            input_tokens: 100,
            output_tokens: 20,
            problem: None,
            ..ReadReport::default()
        }
    );
    // The model was shown the chunk's exact text and who wrote it, and
    // only the types of the vocabulary.
    let requests = transport.requests();
    assert_eq!(
        shown(&requests[0]),
        [("C1".into(), Some("user".into()), text.to_string())]
    );
    let user: Value = serde_json::from_str(&requests[0].user).unwrap();
    assert_eq!(
        user["types"],
        json!([{"key": "decision"}, {"key": "outcome"}])
    );

    let Some(Change::Read { scope, read }) = w.changes().pop() else {
        panic!("the last change is the read");
    };
    assert_eq!(scope, "acme");
    assert_eq!(read.chunk_id, chunk_ids[0]);
    assert!(matches!(
        &read.decision,
        ReadDecision::Typed { type_ref } if type_ref.key == "decision" && type_ref.rev == 1
    ));
    let found: Vec<(SpanKind, &str)> = read
        .spans
        .iter()
        .map(|s| (s.kind, &text[s.byte_start as usize..s.byte_end as usize]))
        .collect();
    assert_eq!(
        found,
        [
            (
                SpanKind::Entity {
                    entity_kind: EntityKind::Person
                },
                "Priya"
            ),
            (
                SpanKind::Entity {
                    entity_kind: EntityKind::Artifact
                },
                "OPS-412"
            ),
            (SpanKind::Action, "get the export under an hour"),
        ]
    );
    assert_eq!(read.reader.model, "stand-in-1");
    assert_eq!(read.reader.binding_id, "small");
    assert_eq!(read.vocabulary.id, "acme.types");

    // The receipt names where the text went and what was sent, and holds
    // none of it. The work is done.
    let receipt =
        w.db.connect()
            .query_one(
                "SELECT o.destination, o.purpose, o.attempt, o.chunk_ids, o.bytes_sent, o.seq,
                    r.result, r.input_tokens, r.output_tokens, o.call_id
               FROM core_memory.outbound_calls AS o
               JOIN core_memory.outbound_results AS r USING (call_id)",
                &[],
            )
            .unwrap();
    assert_eq!(receipt.get::<_, &str>(0), DESTINATION);
    assert_eq!(receipt.get::<_, &str>(1), "chunk_read");
    assert_eq!(receipt.get::<_, i32>(2), 1);
    assert_eq!(receipt.get::<_, Vec<uuid::Uuid>>(3), vec![chunk_ids[0].0]);
    assert_eq!(receipt.get::<_, i64>(4), text.len() as i64);
    assert_eq!(receipt.get::<_, &str>(6), "accepted");
    assert_eq!(
        (receipt.get::<_, i64>(7), receipt.get::<_, i64>(8)),
        (100, 20)
    );
    assert_eq!(read.call_id.0, receipt.get::<_, uuid::Uuid>(9));
    // Event 1, bead 2, receipt 3, read 4.
    assert_eq!((receipt.get::<_, i64>(5), read.seq), (3, 4));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
}

#[test]
fn nothing_is_sent_without_the_scopes_consent() {
    let mut w = World::with_config(&reader_config(""));
    w.accept(&chat("1", "not to be sent anywhere"));
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
        w.count("SELECT count(*) FROM core_memory.work WHERE state = 'ready' AND attempts = 0 AND problem = 'no_consent'"),
        1
    );
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);

    // Consent for another destination, or for another scope, is not consent.
    w.store.enroll_scope("beta", "w1").unwrap();
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.grant_destination('acme', 'elsewhere.test');
             SELECT core_memory.grant_destination('beta', '{DESTINATION}');"
        ))
        .unwrap();
    w.clock.advance_millis(600_000);
    assert_eq!(
        handled(round(&mut w, &transport)).problem,
        Some("no_consent")
    );

    // Granted, the chunk is read, and at once: the work that was put off
    // for want of consent is due again. Revoked, the next one is not.
    assert!(w.store.grant_destination("acme", DESTINATION).unwrap());
    assert!(!w.store.grant_destination("acme", DESTINATION).unwrap());
    let transport = FnTransport(decide_all);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);

    assert!(w.store.revoke_destination("acme", DESTINATION).unwrap());
    assert!(!w.store.revoke_destination("acme", DESTINATION).unwrap());
    w.accept(&chat("2", "after the revocation"));
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
fn a_scope_that_is_not_active_is_not_read() {
    let mut w = reading("");
    w.accept(&chat("1", "captured while active"));
    w.store
        .set_scope_state("acme", core_memory_core::store::ScopeState::Paused)
        .unwrap();
    let transport = FnTransport(decide_all);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.problem, report.calls),
        (Some("scope_not_active"), 0)
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        0
    );

    // Resumed, it is read at once.
    w.store
        .set_scope_state("acme", core_memory_core::store::ScopeState::Active)
        .unwrap();
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);

    // The same after it was revoked and connected again.
    w.accept(&chat("2", "captured before the revocation"));
    w.store
        .set_scope_state("acme", core_memory_core::store::ScopeState::Revoked)
        .unwrap();
    assert_eq!(
        handled(round(&mut w, &transport)).problem,
        Some("scope_not_active")
    );
    w.store.enroll_scope("acme", "w1").unwrap();
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
}

#[test]
fn chunks_stored_before_there_was_a_reader_are_queued_when_asked() {
    use core_memory_core::store::ScopeState;

    // No reader is configured, so admission queues nothing.
    let mut w = World::new();
    w.accept(&chat("1", "stored before any reader"));
    let (erased, _) = w.accept(&chat("2", "erased before any reader"));
    w.store
        .erase_event(&erased, "user_request", "owner")
        .unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);

    // The same store, with a reader now.
    w.engine = engine_with(&reader_config(""), &w.clock);
    w.store.grant_destination("acme", DESTINATION).unwrap();
    let transport = FnTransport(decide_all);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    let now = w.clock.now();
    // Only what is live is queued, and only once.
    assert_eq!(
        w.store.queue_reads("acme", Reread::default(), now).unwrap(),
        QueuedReads::Queued {
            chunks: 1,
            tool_calls: 0
        }
    );
    assert_eq!(
        w.store.queue_reads("acme", Reread::default(), now).unwrap(),
        QueuedReads::Queued {
            chunks: 0,
            tool_calls: 0
        }
    );
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);

    // What was read is not queued again, unless the reader has changed
    // and the caller asks for it.
    let same = w.engine.reader().unwrap().reader_ref.clone();
    let vocabulary = w.engine.vocabulary().unwrap().catalog().clone();
    for current in [None, Some((&same, &vocabulary))] {
        assert_eq!(
            w.store
                .queue_reads(
                    "acme",
                    Reread {
                        chunks: current,
                        tool_calls: None
                    },
                    now
                )
                .unwrap(),
            QueuedReads::Queued {
                chunks: 0,
                tool_calls: 0
            }
        );
    }
    let other = engine_with(
        &reader_config("").replace("stand-in-1", "stand-in-2"),
        &w.clock,
    );
    let changed = other.reader().unwrap().reader_ref.clone();
    assert_ne!(changed.binding_rev, same.binding_rev);
    assert_eq!(
        w.store
            .queue_reads(
                "acme",
                Reread {
                    chunks: Some((&changed, &vocabulary)),
                    tool_calls: None
                },
                now
            )
            .unwrap(),
        QueuedReads::Queued {
            chunks: 1,
            tool_calls: 0
        }
    );

    // Nothing is queued for a scope that is not active.
    assert_eq!(
        w.store
            .queue_reads("nowhere", Reread::default(), now)
            .unwrap(),
        QueuedReads::ScopeNotActive(None)
    );
    w.store.set_scope_state("acme", ScopeState::Paused).unwrap();
    assert_eq!(
        w.store.queue_reads("acme", Reread::default(), now).unwrap(),
        QueuedReads::ScopeNotActive(Some(ScopeState::Paused))
    );
}

#[test]
fn reads_set_aside_as_failed_are_tried_again_when_an_operator_says_so() {
    let mut w = reading("");
    w.accept(&chat("1", "the provider turns this binding down"));
    let failing = FnTransport(|_: &ModelRequest| {
        Err(TransportError::Permanent {
            message: "401".into(),
        })
    });
    // Three attempts, which is all this reader allows.
    for _ in 0..3 {
        assert_eq!(
            handled(round(&mut w, &failing)).problem,
            Some("binding_rejected")
        );
        w.clock.advance_millis(700_000);
    }
    let working = FnTransport(decide_all);
    assert_eq!(round(&mut w, &working), ReadRound::Idle);
    let counts = w.store.work_counts(Purpose::ChunkRead).unwrap();
    assert_eq!((counts.ready, counts.leased, counts.failed), (0, 0, 1));
    assert_eq!(counts.problems, [("binding_rejected".to_string(), 1)]);

    let now = w.clock.now();
    assert_eq!(w.store.retry_failed_reads(Some("beta"), now).unwrap(), 0);
    assert_eq!(w.store.retry_failed_reads(None, now).unwrap(), 1);
    assert_eq!(w.store.retry_failed_reads(None, now).unwrap(), 0);
    assert_eq!(handled(round(&mut w, &working)).stored, 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
}

#[test]
fn reading_waits_for_a_migration_holding_nothing_else() {
    let mut w = reading("");
    w.accept(&chat("1", "read behind a migration"));
    w.accept(&chat("2", "read behind the same migration"));
    let mut store = w.db.store();
    store.check().unwrap();

    // One whole round: the claim, the permit and the finish each start a
    // transaction of their own, and the first of them waits.
    let transport = FnTransport(decide_all);
    {
        let (db, engine) = (&w.db, &w.engine);
        let report =
            behind_a_migration(db, "read_once", || engine.read_once(&mut store, &transport));
        assert_eq!(handled(report.unwrap()).stored, 2);
    }

    // Then each step by itself.
    w.accept(&chat("3", "permitted behind a migration"));
    let (db, engine) = (&w.db, &w.engine);
    let now = w.clock.now();
    let claimed = behind_a_migration(db, "claim_reads", || {
        store.claim_reads(4, 5, now, now.plus_millis(60_000))
    });
    let batch = claimed.unwrap().unwrap();
    let reader = engine.reader().unwrap();
    let ids: Vec<_> = batch.chunks.iter().map(|c| c.chunk_id).collect();
    let request = PermitRequest {
        scope: "acme",
        chunk_ids: &ids,
        purpose: Purpose::ChunkRead,
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
    let work = [core_memory_core::store::WorkUpdate {
        item: core_memory_core::store::WorkItem::Chunk(ids[0]),
        next: core_memory_core::store::WorkNext::Retry {
            not_before: now,
            alone: false,
        },
        problem: "provider_unavailable",
    }];
    let finish = core_memory_core::store::CallFinish {
        call_id,
        scope: "acme",
        result: core_memory_core::store::CallResult::NotSent,
        usage: None,
        reads: &[],
        tool_outcome: None,
        vocabulary: Some(engine.vocabulary().unwrap().catalog()),
        reader: &reader.reader_ref,
        work: &work,
        at: now,
        judge_after: None,
    };
    behind_a_migration(db, "finish_call", || store.finish_call(&finish)).unwrap();
    behind_a_migration(db, "update_work", || store.update_work(&work)).unwrap();
    let counts = behind_a_migration(db, "work_counts", || store.work_counts(Purpose::ChunkRead));
    assert_eq!(counts.unwrap().ready, 1);

    // What an operator and a host call.
    assert_eq!(
        behind_a_migration(db, "queue_reads", || store.queue_reads(
            "acme",
            Reread::default(),
            now
        ))
        .unwrap(),
        QueuedReads::Queued {
            chunks: 0,
            tool_calls: 0
        }
    );
    assert_eq!(
        behind_a_migration(db, "retry_failed_reads", || {
            store.retry_failed_reads(None, now)
        })
        .unwrap(),
        0
    );
    assert!(
        behind_a_migration(db, "revoke_destination", || {
            store.revoke_destination("acme", DESTINATION)
        })
        .unwrap()
    );
    assert!(
        behind_a_migration(db, "grant_destination", || {
            store.grant_destination("acme", DESTINATION)
        })
        .unwrap()
    );
}

#[test]
fn erased_text_is_never_sent_and_a_read_of_it_is_never_stored() {
    let mut w = reading("");
    let (gone, _) = w.accept(&chat("1", "erased before any model saw it"));
    w.accept(&chat("2", "still here"));
    let (during, _) = w.accept(&chat("3", "erased while the model was reading"));
    w.store.erase_event(&gone, "user_request", "owner").unwrap();
    // Erasing dropped the work with the text.
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 2);

    let seen = Arc::new(Mutex::new(Vec::new()));
    let (seen_by_model, url) = (Arc::clone(&seen), w.db.url.clone());
    let transport = FnTransport(move |request: &ModelRequest| {
        seen_by_model.lock().unwrap().extend(shown(request));
        // The owner erases one of the messages while the call is out.
        PostgresStore::new(&ConnectConfig::new(url.clone()))
            .unwrap()
            .erase_event(&during, "user_request", "owner")
            .unwrap();
        decide_all(request)
    });
    let report = handled(round(&mut w, &transport));
    let texts: Vec<String> = seen.lock().unwrap().iter().map(|c| c.2.clone()).collect();
    assert_eq!(
        texts,
        ["still here", "erased while the model was reading"],
        "the message erased first was never shown"
    );
    assert_eq!((report.stored, report.dropped), (1, 1));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_reads"), 1);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.chunk_reads AS r
               JOIN core_memory.chunks AS c USING (chunk_id)
               JOIN core_memory.events AS e USING (event_id)
              WHERE e.state = 'erased'"
        ),
        0
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
}

#[test]
fn erasing_a_message_removes_what_was_read_in_it() {
    let mut w = reading("");
    let text = "Priya shipped the export.";
    let (event_id, _) = w.accept(&chat("1", text));
    let transport = ScriptedTransport::new(vec![reply(vec![json!({
        "chunk": "C1", "type": "outcome",
        "entities": [{"text": "Priya", "kind": "person"}],
        "outcomes": [{"text": "shipped the export"}]
    })])]);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunk_read_spans"),
        2
    );

    w.store
        .erase_event(&event_id, "user_request", "owner")
        .unwrap();
    for table in ["chunk_reads", "chunk_read_spans", "chunk_text", "work"] {
        assert_eq!(
            w.count(&format!("SELECT count(*) FROM core_memory.{table}")),
            0,
            "{table}"
        );
    }
    // The receipt of the call that sent it stays: it holds IDs only.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
    // A consumer that tails the workspace learns of the erasure, and no
    // longer sees the event or its read.
    let kinds: Vec<&str> = w.changes().iter().map(common::kind_of).collect();
    assert_eq!(kinds, ["erasure"]);
}

#[test]
fn a_rejected_reply_is_repaired_once() {
    let mut w = reading("");
    w.accept(&chat("1", "Priya shipped the export."));
    let transport = ScriptedTransport::new(vec![
        // A quote that is not in the chunk.
        reply(vec![json!({
            "chunk": "C1", "type": "outcome",
            "entities": [{"text": "Priya Raman", "kind": "person"}]
        })]),
        reply(vec![json!({
            "chunk": "C1", "type": "outcome",
            "entities": [{"text": "Priya", "kind": "person"}]
        })]),
    ]);
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.calls, report.stored, report.problem), (2, 1, None));
    assert_eq!((report.input_tokens, report.output_tokens), (200, 40));
    // Why the first reply was rejected is told by its kind, never its text.
    assert_eq!(report.reply_faults, [Fault::QuoteNotFound]);
    assert_eq!(report.quotes_dropped, 0);
    let requests = transport.requests();
    assert!(requests[1].user.starts_with(&requests[0].user));
    assert!(requests[1].user.contains("is not in the chunk as written"));

    // Two calls, two receipts: the repair names the call it repairs.
    let calls =
        w.db.connect()
            .query(
                "SELECT o.attempt, r.result, o.repairs IS NOT NULL
               FROM core_memory.outbound_calls AS o
               JOIN core_memory.outbound_results AS r USING (call_id) ORDER BY o.seq",
                &[],
            )
            .unwrap();
    let calls: Vec<(i32, &str, bool)> = calls
        .iter()
        .map(|row| (row.get(0), row.get(1), row.get(2)))
        .collect();
    assert_eq!(calls, [(1, "rejected", false), (2, "accepted", true)]);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunk_read_spans"),
        1
    );
}

#[test]
fn on_the_last_attempt_a_quote_that_is_not_found_is_dropped_and_counted() {
    let mut w = reading("");
    w.accept(&chat("1", "Priya shipped the export."));
    let wrong = || {
        reply(vec![json!({
            "chunk": "C1", "type": "outcome",
            "entities": [{"text": "Priya Raman", "kind": "person"}, {"text": "Priya", "kind": "person"}]
        })])
    };
    let transport = ScriptedTransport::new(vec![wrong(), wrong()]);
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.calls, report.stored), (2, 1));
    assert_eq!(
        (report.reply_faults.as_slice(), report.quotes_dropped),
        ([Fault::QuoteNotFound].as_slice(), 1)
    );
    assert_eq!(
        w.count("SELECT spans_rejected::bigint FROM core_memory.chunk_reads"),
        1
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunk_read_spans"),
        1
    );
}

#[test]
fn a_reply_that_stays_invalid_sends_each_chunk_alone_from_then_on() {
    let mut w = reading("");
    for i in 0..3 {
        w.accept(&chat(&format!("{i}"), &format!("message number {i}")));
    }
    // The model never answers the second chunk.
    let skips_one = |request: &ModelRequest| {
        reply(
            shown(request)
                .iter()
                .filter(|(_, _, text)| !text.ends_with('1'))
                .map(|(handle, _, _)| json!({"chunk": handle, "abstain": "no_fit"}))
                .collect(),
        )
    };
    let transport = FnTransport(skips_one);
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.chunks, report.calls, report.stored), (3, 2, 0));
    assert_eq!(report.problem, Some("reply_invalid"));
    assert_eq!(report.reply_faults, [Fault::MissingChunk]);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work WHERE alone AND state = 'ready' AND attempts = 1"
        ),
        3
    );
    // Nothing is due until the backoff has passed.
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    w.clock.advance_millis(1_500);

    // Alone, the two it can read are read, and the third keeps failing
    // until it is set aside.
    let mut stored = 0;
    for _ in 0..12 {
        match round(&mut w, &transport) {
            ReadRound::Handled(report) => {
                assert_eq!(report.chunks, 1, "one chunk per call from now on");
                stored += report.stored;
            }
            ReadRound::Idle => w.clock.advance_millis(1_500),
        }
    }
    assert_eq!(stored, 2);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.chunk_reads WHERE abstained = 'no_fit'"),
        2
    );
    let counts = w.store.work_counts(Purpose::ChunkRead).unwrap();
    assert_eq!((counts.ready, counts.leased, counts.failed), (0, 0, 1));
    assert_eq!(counts.problems, [("reply_invalid".to_string(), 1)]);
    assert_eq!(
        w.count("SELECT attempts::bigint FROM core_memory.work WHERE state = 'failed'"),
        3,
        "set aside after max_attempts"
    );
}

#[test]
fn a_chunk_the_model_declines_on_its_own_is_not_sent_again() {
    let mut w = reading("");
    w.accept(&chat("1", "declined whenever it is sent"));
    let declined = || {
        Err(TransportError::Refused {
            message: "the model declined".into(),
        })
    };
    let transport = ScriptedTransport::new(vec![declined()]);
    // Sent alone, the same request would be declined again. The chunk is
    // set aside at once, whatever attempts are left.
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.calls, report.problem), (1, Some("model_refused")));
    w.clock.advance_millis(600_000);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    assert_eq!(transport.requests().len(), 1);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE state = 'failed' AND attempts = 1 AND problem = 'model_refused'"
        ),
        1
    );

    // In a batch the model may have declined any one chunk, so each is
    // sent once more, alone. The one it declines is then set aside.
    let mut w = reading("");
    for i in 0..2 {
        w.accept(&chat(&format!("{i}"), &format!("message number {i}")));
    }
    let declines_one = FnTransport(|request: &ModelRequest| {
        if shown(request)
            .iter()
            .any(|(_, _, text)| text.ends_with('1'))
        {
            declined()
        } else {
            decide_all(request)
        }
    });
    let report = handled(round(&mut w, &declines_one));
    assert_eq!((report.chunks, report.problem), (2, Some("model_refused")));
    let (mut stored, mut calls) = (0, 1);
    for _ in 0..6 {
        match round(&mut w, &declines_one) {
            ReadRound::Handled(report) => {
                stored += report.stored;
                calls += report.calls;
            }
            ReadRound::Idle => w.clock.advance_millis(1_500),
        }
    }
    assert_eq!((stored, calls), (1, 3));
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE state = 'failed' AND attempts = 2 AND problem = 'model_refused'"
        ),
        1
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 1);
}

#[test]
fn an_attempt_counts_once_a_call_was_made_for_it() {
    // The day's budget allows one request, and the model never answers
    // the chunk: each day one call is made and its repair is refused.
    let mut w =
        reading("[binding.budget]\nmax_requests_per_day = 1\nmax_tokens_per_day = 1000000\n");
    w.accept(&chat("1", "never answered"));
    let transport = FnTransport(|_: &ModelRequest| reply(vec![]));
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
              WHERE state = 'failed' AND attempts = 3 AND problem = 'over_budget'"
        ),
        1
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        3
    );
}

#[test]
fn a_chunk_is_handed_out_only_as_often_as_it_may_be_tried() {
    let mut w = reading("");
    let (_, chunk_ids) = w.accept(&chat("1", "to be read"));
    // A worker takes the chunk and dies with it, three times over.
    for attempt in 1..=3 {
        let now = w.clock.now();
        let batch = w
            .store
            .claim_reads(4, 3, now, now.plus_millis(60_000))
            .unwrap()
            .unwrap();
        assert_eq!(
            (batch.chunks[0].chunk_id, batch.chunks[0].attempts),
            (chunk_ids[0], attempt)
        );
        w.clock.advance_millis(60_001);
    }
    // It is not handed out a fourth time. It is set aside, with the reason.
    let now = w.clock.now();
    let claimed = w.store.claim_reads(4, 3, now, now.plus_millis(60_000));
    assert!(claimed.unwrap().is_none());
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE state = 'failed' AND attempts = 3 AND leased_until IS NULL
                AND problem = 'attempts_spent'"
        ),
        1
    );
    // A chunk that was read in time is not touched: only work that is
    // still owed is set aside.
    w.accept(&chat("2", "read at once"));
    let transport = FnTransport(decide_all);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    let counts = w.store.work_counts(Purpose::ChunkRead).unwrap();
    assert_eq!((counts.ready, counts.failed), (0, 1));
    assert_eq!(counts.problems, [("attempts_spent".to_string(), 1)]);
    // An operator puts it back, and it is read.
    let now = w.clock.now();
    assert_eq!(w.store.retry_failed_reads(Some("acme"), now).unwrap(), 1);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
}

#[test]
fn the_end_of_a_call_is_recorded_once_and_a_wild_token_count_is_no_obstacle() {
    let mut w = reading("");
    let (_, chunk_ids) = w.accept(&chat("1", "read once"));
    let reader = w.engine.reader().unwrap().reader_ref.clone();
    let vocabulary = w.engine.vocabulary().unwrap();
    let now = w.clock.now();
    let Permit::Granted { call_id, .. } = w
        .store
        .permit(&PermitRequest {
            scope: "acme",
            chunk_ids: &chunk_ids,
            purpose: Purpose::ChunkRead,
            destination: DESTINATION,
            reader: &reader,
            attempt: 1,
            repairs: None,
            sending: &|_| {
                Some(Sending {
                    bytes: 9,
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
    let reads = [ReadWrite {
        chunk_id: chunk_ids[0],
        decision: ReadDecision::Typed {
            type_ref: vocabulary.get("decision").unwrap().clone(),
        },
        spans: Vec::new(),
        spans_rejected: 0,
    }];
    let finish = CallFinish {
        call_id,
        scope: "acme",
        result: CallResult::Accepted,
        // A provider's count of its own tokens is a number from outside.
        usage: Some(Usage {
            input_tokens: u64::MAX,
            output_tokens: 7,
        }),
        reads: &reads,
        tool_outcome: None,
        vocabulary: Some(vocabulary.catalog()),
        reader: &reader,
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
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_reads"), 1);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.outbound_results
              WHERE input_tokens = 9223372036854775807 AND output_tokens = 7"
        ),
        1
    );
}

#[test]
fn nothing_is_stored_about_a_chunk_whose_message_was_erased() {
    let mut w = reading("");
    let (_, kept) = w.accept(&chat("1", "kept"));
    let (gone, gone_chunks) = w.accept(&chat("2", "erased after it was read"));
    let transport = FnTransport(decide_all);
    assert_eq!(handled(round(&mut w, &transport)).stored, 2);
    w.store.erase_event(&gone, "user_request", "owner").unwrap();

    // The engine checks this where it stores a read. The table checks it
    // too, for a statement that does not: even the table's owner cannot
    // add a row about an erased message, which no erase would reach again.
    let mut admin = w.db.connect();
    let mut forged = |chunk: uuid::Uuid| {
        admin.execute(
            "INSERT INTO core_memory.chunk_reads (
                 workspace_id, seq, chunk_id, scope, call_id, status, type_key, type_rev,
                 abstained, vocabulary_id, vocabulary_hash, binding_id, binding_rev, model,
                 prompt_rev, spans_rejected, read_at
             )
             SELECT r.workspace_id, 900001, $1, r.scope, r.call_id, r.status, r.type_key,
                    r.type_rev, r.abstained, r.vocabulary_id, r.vocabulary_hash, r.binding_id,
                    r.binding_rev, r.model, r.prompt_rev, r.spans_rejected, r.read_at
               FROM core_memory.chunk_reads AS r LIMIT 1",
            &[&chunk],
        )
    };
    let refused = forged(gone_chunks[0].0).unwrap_err();
    let refused = refused.as_db_error().unwrap().message().to_string();
    assert!(
        refused.contains("only for an event that is live"),
        "{refused}"
    );
    // For a message that is live, the same statement is taken.
    assert_eq!(forged(kept[0].0).unwrap(), 1);
    assert_eq!(
        w.count(&format!(
            "SELECT count(*) FROM core_memory.chunk_reads WHERE chunk_id = '{}'",
            gone_chunks[0].0
        )),
        0
    );
}

#[test]
fn a_call_that_fails_is_recorded_and_tried_again_later() {
    let mut w = reading("");
    w.accept(&chat("1", "to be read eventually"));
    let failures = vec![
        (
            TransportError::Transient {
                message: "overloaded".into(),
                retry_after_ms: Some(5_000),
            },
            "not_sent",
            "provider_unavailable",
            5_000,
        ),
        (
            TransportError::InDoubt {
                message: "timed out after sending".into(),
            },
            "in_doubt",
            "call_in_doubt",
            1_000,
        ),
    ];
    for (i, (error, result, problem, wait_ms)) in failures.into_iter().enumerate() {
        let transport = ScriptedTransport::new(vec![Err(error)]);
        let report = handled(round(&mut w, &transport));
        assert_eq!(
            (report.problem, report.calls, report.stored),
            (Some(problem), 1, 0)
        );
        assert_eq!(
            w.count(&format!(
                "SELECT count(*) FROM core_memory.outbound_results WHERE result = '{result}'"
            )),
            1
        );
        // The provider's message is not kept anywhere.
        assert_eq!(
            w.count(&format!(
                "SELECT count(*) FROM core_memory.work
                  WHERE problem = '{problem}' AND attempts = {} AND NOT alone",
                i + 1
            )),
            1
        );
        w.clock.advance_millis(wait_ms - 1);
        assert_eq!(round(&mut w, &transport), ReadRound::Idle, "{problem}");
        w.clock.advance_millis(2);
    }
    // A model that declines the content gets each chunk alone next time.
    let declines = ScriptedTransport::new(vec![Err(TransportError::Refused {
        message: "declined".into(),
    })]);
    assert_eq!(
        handled(round(&mut w, &declines)).problem,
        Some("model_refused")
    );
    // That was the third attempt, the last this configuration allows.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE state = 'failed' AND problem = 'model_refused'"),
        1
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        3
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_reads"), 0);
}

#[test]
fn a_binding_the_provider_turns_down_backs_off_for_longer() {
    let mut w = reading("");
    w.accept(&chat("1", "waiting for a working binding"));
    let transport = ScriptedTransport::new(vec![Err(TransportError::Permanent {
        message: "no such model".into(),
    })]);
    assert_eq!(
        handled(round(&mut w, &transport)).problem,
        Some("binding_rejected")
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_results WHERE result = 'failed'"),
        1
    );
    w.clock.advance_millis(60_000);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    w.clock.advance_millis(600_000);
    assert_eq!(handled(round(&mut w, &FnTransport(decide_all))).stored, 1);
}

#[test]
fn a_days_budget_stops_calls_until_the_next_day() {
    let mut w =
        reading("[binding.budget]\nmax_requests_per_day = 1\nmax_tokens_per_day = 1000000\n");
    w.store.enroll_scope("beta", "w1").unwrap();
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.grant_destination('beta', '{DESTINATION}')"
        ))
        .unwrap();
    w.accept(&chat("1", "read today"));
    w.accept(&chat_in("beta", "1", "read tomorrow"));
    let transport = FnTransport(decide_all);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    // The budget is the workspace's, whichever scope asks.
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.problem, report.calls), (Some("over_budget"), 0));
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
    w.clock.advance_millis(3_600_000);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    // T0 is 07:33 UTC. Past midnight the budget is whole again.
    w.clock.advance_millis(16 * 3_600_000);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);

    let tight = reading("[binding.budget]\nmax_requests_per_day = 100\nmax_tokens_per_day = 600\n");
    let mut tight = tight;
    tight.accept(&chat(
        "1",
        "the instructions alone are more than 600 tokens",
    ));
    assert_eq!(
        handled(round(&mut tight, &transport)).problem,
        Some("over_budget")
    );
}

#[test]
fn a_batch_is_one_scope_and_one_profile_and_a_profile_narrows_the_types() {
    let mut w = reading("");
    w.store.enroll_scope("beta", "w1").unwrap();
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.grant_destination('beta', '{DESTINATION}')"
        ))
        .unwrap();
    // Work is taken oldest first.
    w.accept(&chat("1", "a message"));
    w.clock.advance_millis(1);
    w.accept(&crm("opp-1", "closed_won", 100));
    w.clock.advance_millis(1);
    w.accept(&chat_in("beta", "1", "another connection's message"));
    w.clock.advance_millis(1);
    w.accept(&chat("2", "a second message"));

    let batches = Arc::new(Mutex::new(Vec::new()));
    let seen = Arc::clone(&batches);
    let transport = FnTransport(move |request: &ModelRequest| {
        let user: Value = serde_json::from_str(&request.user).unwrap();
        let types: Vec<String> = user["types"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["key"].as_str().unwrap().to_string())
            .collect();
        let texts: Vec<String> = shown(request).into_iter().map(|c| c.2).collect();
        seen.lock().unwrap().push((types.clone(), texts));
        reply(
            shown(request)
                .iter()
                .map(|(handle, _, _)| json!({"chunk": handle, "type": types[0]}))
                .collect(),
        )
    });
    while round(&mut w, &transport) != ReadRound::Idle {}
    let both = vec!["decision".to_string(), "outcome".to_string()];
    assert_eq!(
        *batches.lock().unwrap(),
        [
            (
                both.clone(),
                vec!["a message".to_string(), "a second message".to_string()]
            ),
            // The CRM profile lists `outcome` only.
            (
                vec!["outcome".to_string()],
                vec![
                    r#"{"Amount":100,"Id":"006AAAAAAAAAAAA","StageName":"closed_won"}"#.to_string()
                ]
            ),
            (both, vec!["another connection's message".to_string()]),
        ]
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_reads"), 4);

    // A type outside a profile's list is not a listed type for it.
    w.accept(&crm("opp-2", "open", 5));
    let wrong_type = ScriptedTransport::new(vec![
        reply(vec![json!({"chunk": "C1", "type": "decision"})]),
        reply(vec![json!({"chunk": "C1", "type": "decision"})]),
    ]);
    assert_eq!(
        handled(round(&mut w, &wrong_type)).problem,
        Some("reply_invalid")
    );
}

#[test]
fn a_permit_covers_only_the_text_of_its_own_scope() {
    let mut w = reading("");
    w.store.enroll_scope("beta", "w1").unwrap();
    let (_, acme_chunks) = w.accept(&chat("1", "acme's text"));
    let (_, beta_chunks) = w.accept(&chat_in("beta", "1", "beta's text"));
    let reader = w.engine.reader().unwrap().reader_ref.clone();
    // A request under acme's consent that names beta's chunk too.
    let permit = w
        .store
        .permit(&PermitRequest {
            scope: "acme",
            chunk_ids: &[acme_chunks[0], beta_chunks[0]],
            purpose: Purpose::ChunkRead,
            destination: DESTINATION,
            reader: &reader,
            attempt: 1,
            repairs: None,
            // The reader is asked about the chunks it will be handed, and
            // those are acme's alone.
            sending: &|chunks| {
                Some(Sending {
                    bytes: chunks.iter().map(|c| c.text.len() as u64).sum(),
                    reserved_tokens: 0,
                })
            },
            budget: None,
            at: w.clock.now(),
        })
        .unwrap();
    let Permit::Granted { chunks, .. } = permit else {
        panic!("{permit:?}");
    };
    assert_eq!(chunks.len(), 1);
    assert_eq!(chunks[0].text, "acme's text");
    assert_eq!(
        w.count("SELECT bytes_sent FROM core_memory.outbound_calls"),
        "acme's text".len() as i64
    );
    assert_eq!(
        w.count("SELECT cardinality(chunk_ids)::bigint FROM core_memory.outbound_calls"),
        1,
        "the receipt names only what was handed out"
    );
}

#[test]
fn without_a_reader_nothing_is_queued_and_no_call_is_made() {
    let mut w = World::new();
    w.accept(&chat("1", "stored and never sent"));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    let transport = ScriptedTransport::new(vec![]);
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    assert!(transport.requests().is_empty());
    assert!(w.engine.reader().is_none());
}

#[test]
fn an_abstention_is_stored_as_a_judgment_not_retried() {
    let mut w = reading("");
    w.accept(&chat("1", "ok"));
    let transport = ScriptedTransport::new(vec![reply(vec![
        json!({"chunk": "C1", "abstain": "insufficient_evidence"}),
    ])]);
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    let Some(Change::Read { read: stored, .. }) = w.changes().pop() else {
        panic!("the last change is the read");
    };
    assert_eq!(
        stored.decision,
        ReadDecision::Abstained {
            reason: Abstention::InsufficientEvidence
        }
    );
    assert_eq!(round(&mut w, &transport), ReadRound::Idle);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
}

#[test]
fn the_engine_role_reads_but_cannot_grant_itself_consent_or_rewrite_receipts() {
    let mut w = reading("");
    w.accept(&chat("1", "read with least privilege"));
    let mut admin = w.db.connect();
    admin
        .batch_execute(
            "DROP ROLE IF EXISTS cm_t_reader_engine;
             CREATE ROLE cm_t_reader_engine LOGIN PASSWORD 'synthetic';
             GRANT core_memory_engine TO cm_t_reader_engine;",
        )
        .unwrap();
    let (scheme, rest) = w.db.url.split_once("://").unwrap();
    let (_, host) = rest.split_once('@').unwrap();
    let url = format!("{scheme}://cm_t_reader_engine:synthetic@{host}");
    let mut store = PostgresStore::new(&ConnectConfig::new(url.clone())).unwrap();
    let round = w
        .engine
        .read_once(&mut store, &FnTransport(decide_all))
        .unwrap();
    assert_eq!(handled(round).stored, 1);

    let mut raw = postgres::Client::connect(&url, postgres::NoTls).unwrap();
    for sql in [
        "SELECT core_memory.grant_destination('acme', 'anywhere.test')",
        "INSERT INTO core_memory.scope_destinations VALUES ('acme', 'anywhere.test', 'granted', now(), NULL)",
        "UPDATE core_memory.scope_destinations SET state = 'granted'",
        "UPDATE core_memory.outbound_calls SET destination = 'elsewhere.test'",
        "DELETE FROM core_memory.outbound_calls",
        "DELETE FROM core_memory.outbound_results",
        "UPDATE core_memory.chunk_reads SET type_key = 'outcome'",
        "DELETE FROM core_memory.chunk_reads",
        "DELETE FROM core_memory.chunk_read_spans",
    ] {
        let message = match raw.batch_execute(sql) {
            Ok(()) => panic!("`{sql}` was allowed"),
            Err(e) => e
                .as_db_error()
                .map_or(e.to_string(), |d| d.message().to_string()),
        };
        assert!(message.contains("permission denied"), "{sql}: {message}");
    }
    // Even the owner cannot change a receipt or a read.
    for sql in [
        "UPDATE core_memory.outbound_calls SET destination = 'elsewhere.test'",
        "DELETE FROM core_memory.outbound_results",
        "UPDATE core_memory.chunk_reads SET type_key = 'outcome'",
        "DELETE FROM core_memory.chunk_reads",
        "TRUNCATE core_memory.outbound_calls CASCADE",
        "TRUNCATE core_memory.work",
    ] {
        let message = match admin.batch_execute(sql) {
            Ok(()) => panic!("`{sql}` was allowed"),
            Err(e) => e
                .as_db_error()
                .map_or(e.to_string(), |d| d.message().to_string()),
        };
        assert!(message.contains("cannot be"), "{sql}: {message}");
    }
    drop(raw);
    drop(store);
    admin
        .batch_execute("DROP OWNED BY cm_t_reader_engine; DROP ROLE cm_t_reader_engine")
        .unwrap();
}

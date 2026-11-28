//! Giving chunks vectors and recalling by meaning, against PostgreSQL and a
//! stand-in embedder: consent, receipts, retries, erasure, and the search
//! itself where the server has the `vector` extension.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use std::sync::atomic::{AtomicU32, Ordering};

use common::{CONFIG, World, behind_a_migration, chat, chat_in, engine_with, tool_call};
use core_memory_contract::{
    MatchedBy, RecallAnswer, RecallFound, RecallMeaning, RecallRequest, RecallSearch, RecallVia,
};
use core_memory_core::embed::{EmbedReport, EmbedRound};
use core_memory_core::model::{EmbedRequest, EmbedResponse, FnEmbedTransport, TransportError};
use core_memory_core::store::{
    CallResult, EmbedFinish, MeaningReady, Permit, PermitRequest, Purpose, Sending, Store,
    VectorWrite,
};
use core_memory_core::{
    Clock, EmbedTransport, MeaningLeg, QuestionVector, RecallQuery, ScopeGrants, Search,
};
use core_memory_store_postgres::testing::unique_name;
use core_memory_store_postgres::{ConnectConfig, PostgresStore, QueuedReads, Reread};
use postgres::Client;
use serde_json::json;

const DESTINATION: &str = "vectors.test:8080";

fn embed_config(extra: &str) -> String {
    format!(
        r#"{CONFIG}
[embedder]
binding = "embed"
batch_size = 4
max_attempts = 3
retry_backoff_ms = 1000
{extra}

[[binding]]
id = "embed"
provider = "stand-in"
endpoint = "http://Vectors.Test:8080/v1"
model = "stand-in-embed-1"
"#
    )
}

/// A world whose engine embeds, with the embedder's destination granted to
/// scope `acme`.
fn embedding(extra: &str) -> World {
    let w = World::with_config(&embed_config(extra));
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.grant_destination('acme', '{DESTINATION}')"
        ))
        .unwrap();
    w
}

/// What a text is about, as a stand-in for a model: one number for each of
/// a few subjects, counting the subject's words in the text, and a last
/// one so that no vector is all zeros.
fn about(text: &str) -> Vec<f32> {
    const SUBJECTS: [&[&str]; 3] = [
        &["price", "pricing", "fee", "subscription", "charge"],
        &["deploy", "release", "rollout", "ship"],
        &["cat", "dog", "kitten", "pet"],
    ];
    let text = text.to_lowercase();
    let mut vector: Vec<f32> = SUBJECTS
        .iter()
        .map(|words| words.iter().filter(|word| text.contains(**word)).count() as f32)
        .collect();
    vector.push(0.05);
    vector
}

fn vectors(request: &EmbedRequest) -> Result<EmbedResponse, TransportError> {
    Ok(EmbedResponse {
        vectors: request.inputs.iter().map(|text| about(text)).collect(),
        input_tokens: 9,
    })
}

/// A stand-in embedder that also counts its calls.
fn counting(calls: &AtomicU32) -> impl EmbedTransport + '_ {
    FnEmbedTransport(move |request: &EmbedRequest| {
        calls.fetch_add(1, Ordering::SeqCst);
        vectors(request)
    })
}

fn handled(round: EmbedRound) -> EmbedReport {
    match round {
        EmbedRound::Handled(report) => report,
        EmbedRound::Idle => panic!("nothing was due"),
    }
}

fn round(w: &mut World, transport: &dyn EmbedTransport) -> EmbedRound {
    w.engine.embed_once(&mut w.store, transport).unwrap()
}

/// Embeds everything that is due.
fn embed_all(w: &mut World) {
    while let EmbedRound::Handled(report) = round(w, &FnEmbedTransport(vectors)) {
        assert_eq!(report.problem, None);
    }
}

fn refused(client: &mut Client, sql: &str) -> String {
    match client.batch_execute(sql) {
        Ok(()) => panic!("`{sql}` was allowed"),
        Err(e) => e
            .as_db_error()
            .map_or(e.to_string(), |d| d.message().to_string()),
    }
}

/// Installs the `vector` extension in the world's database, in `schema`.
/// `false`, having said so, on a server that has none; a run that must
/// cover the search sets `CORE_MEMORY_TEST_PGVECTOR=required`, and fails
/// there instead.
#[allow(clippy::print_stderr)]
fn install_vectors(w: &World, schema: &str) -> bool {
    let mut owner = w.db.connect();
    let available: bool = owner
        .query_one(
            "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')",
            &[],
        )
        .unwrap()
        .get(0);
    if !available {
        let required = std::env::var("CORE_MEMORY_TEST_PGVECTOR").is_ok_and(|v| v == "required");
        assert!(
            !required,
            "this server has no `vector` extension, and the run requires it"
        );
        eprintln!("skipped: this server has no `vector` extension");
        return false;
    }
    owner
        .batch_execute(&format!(
            "CREATE SCHEMA IF NOT EXISTS {schema};
             GRANT USAGE ON SCHEMA {schema} TO core_memory_engine;
             CREATE EXTENSION vector SCHEMA {schema};"
        ))
        .unwrap();
    true
}

fn request(query: &str) -> RecallRequest {
    RecallRequest {
        query: query.into(),
        scopes: None,
        limit: None,
        follow: Some(0),
        include_ended: false,
        search: None,
    }
}

fn recall(w: &mut World, ask: &dyn EmbedTransport, request: &RecallRequest) -> RecallAnswer {
    let query = RecallQuery::from_request(request, &ScopeGrants::All).unwrap();
    w.engine.recall(&mut w.store, Some(ask), query).unwrap()
}

/// Each bead's first chunk of text, with how it matched.
fn told(answer: &RecallAnswer) -> Vec<(String, MatchedBy)> {
    answer
        .beads
        .iter()
        .map(|bead| {
            let RecallVia::Match { by, .. } = &bead.via else {
                panic!("not a match: {:?}", bead.via)
            };
            (bead.chunks[0].text.clone(), *by)
        })
        .collect()
}

// ---- the lane ----

#[test]
fn a_chunk_gets_a_vector_and_its_call_a_receipt() {
    let mut w = embedding("document_prefix = \"passage: \"");
    let text = "The subscription fee goes up in March.";
    let (_, chunk_ids) = w.accept(&chat("1", text));
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE kind = 'embed'"),
        1
    );

    let sent = std::sync::Mutex::new(Vec::new());
    let transport = FnEmbedTransport(|request: &EmbedRequest| {
        sent.lock().unwrap().push(request.clone());
        vectors(request)
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (
            report.chunks,
            report.stored,
            report.dropped,
            report.calls,
            report.input_tokens,
            report.problem
        ),
        (1, 1, 0, 1, 9, None)
    );
    // The text went out whole, after the prefix the model is told by.
    assert_eq!(
        *sent.lock().unwrap(),
        vec![EmbedRequest {
            inputs: vec![format!("passage: {text}")],
            dimensions: None,
        }]
    );

    let embedder = w.engine.embedder().unwrap();
    let mut owner = w.db.connect();
    let row = owner
        .query_one(
            "SELECT v.chunk_id, v.scope, v.embedder_rev, v.binding_id, v.model, v.dimensions,
                    v.input_bytes, v.vector, v.seq, o.purpose, o.destination, o.bytes_sent,
                    o.chunk_ids, r.result, r.input_tokens, r.output_tokens, o.seq
               FROM core_memory.chunk_vectors AS v
               JOIN core_memory.outbound_calls AS o ON o.call_id = v.call_id
               JOIN core_memory.outbound_results AS r ON r.call_id = v.call_id",
            &[],
        )
        .unwrap();
    assert_eq!(row.get::<_, uuid::Uuid>(0), chunk_ids[0].0);
    assert_eq!(row.get::<_, &str>(1), "acme");
    assert_eq!(
        row.get::<_, &[u8]>(2),
        embedder.embedder_ref.binding_rev.as_bytes()
    );
    assert_eq!(row.get::<_, &str>(3), "embed");
    assert_eq!(row.get::<_, &str>(4), "stand-in-embed-1");
    assert_eq!(row.get::<_, i32>(5), 4);
    assert_eq!(row.get::<_, i64>(6), text.len() as i64);
    assert_eq!(row.get::<_, Vec<f32>>(7), about(text));
    // The receipt is for the call that embeds, and counts the chunk's own
    // bytes: the prefix is not what a source said.
    assert_eq!(row.get::<_, &str>(9), "embed");
    assert_eq!(row.get::<_, &str>(10), DESTINATION);
    assert_eq!(row.get::<_, i64>(11), text.len() as i64);
    assert_eq!(row.get::<_, Vec<uuid::Uuid>>(12), vec![chunk_ids[0].0]);
    assert_eq!(row.get::<_, &str>(13), "accepted");
    assert_eq!(
        (row.get::<_, Option<i64>>(14), row.get::<_, Option<i64>>(15)),
        (Some(9), Some(0))
    );
    // Both rows are numbered from the workspace's counter, the receipt
    // first: it was committed before anything was sent.
    assert!(row.get::<_, i64>(16) < row.get::<_, i64>(8));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    assert_eq!(round(&mut w, &transport), EmbedRound::Idle);
}

#[test]
fn nothing_is_sent_without_consent_and_a_tool_call_gets_no_vector() {
    let mut w = World::with_config(&embed_config(""));
    w.accept(&chat("1", "The subscription fee goes up in March."));
    w.accept(&tool_call(
        "t1",
        "shell",
        json!({"command": "ls"}),
        "pricing.md",
        None,
    ));
    // A tool call's text is never sent to a model whole, so it is owed no
    // vector.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE kind = 'embed'"),
        1
    );

    let calls = AtomicU32::new(0);
    let report = handled(round(&mut w, &counting(&calls)));
    assert_eq!(report.problem, Some("no_consent"));
    assert_eq!((report.calls, report.stored), (0, 0));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    // A refusal writes no receipt and uses no attempt.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        0
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'embed' AND state = 'ready' AND attempts = 0
                AND problem = 'no_consent'"
        ),
        1
    );

    // Once the scope allows the destination, the work is done when it is
    // next due.
    assert!(w.store.grant_destination("acme", DESTINATION).unwrap());
    w.clock.advance_millis(300_000);
    assert_eq!(handled(round(&mut w, &counting(&calls))).stored, 1);
    assert_eq!(calls.load(Ordering::SeqCst), 1);
}

#[test]
fn a_failed_call_is_made_again_by_the_queue_until_its_attempts_are_spent() {
    let mut w = embedding("");
    w.accept(&chat("1", "The subscription fee goes up in March."));
    let down = FnEmbedTransport(|_: &EmbedRequest| {
        Err(TransportError::Transient {
            message: "503".into(),
            retry_after_ms: None,
        })
    });
    for attempt in 1..=3 {
        let report = handled(round(&mut w, &down));
        assert_eq!(report.problem, Some("provider_unavailable"));
        assert_eq!((report.calls, report.stored), (1, 0));
        // Every attempt has a receipt and a recorded end of its own.
        assert_eq!(
            w.count(
                "SELECT count(*) FROM core_memory.outbound_calls AS o
                   JOIN core_memory.outbound_results AS r ON r.call_id = o.call_id
                  WHERE o.purpose = 'embed' AND r.result = 'not_sent'"
            ),
            attempt
        );
        // It waits out the backoff before it is due again.
        assert_eq!(round(&mut w, &down), EmbedRound::Idle);
        w.clock.advance_millis(1_000);
    }
    // Three attempts, and it is set aside for an operator.
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'embed' AND state = 'failed' AND attempts = 3
                AND problem = 'provider_unavailable'"
        ),
        1
    );
    assert_eq!(round(&mut w, &FnEmbedTransport(vectors)), EmbedRound::Idle);

    // An operator puts it back, and a provider that answers embeds it.
    let now = w.clock.now();
    assert_eq!(w.store.retry_failed_reads(None, now).unwrap(), 1);
    assert_eq!(handled(round(&mut w, &FnEmbedTransport(vectors))).stored, 1);
}

#[test]
fn a_reply_that_is_not_a_vector_for_each_chunk_stores_nothing() {
    let mut w = embedding("dimensions = 4");
    w.accept(&chat("1", "The subscription fee goes up in March."));
    w.accept(&chat("2", "The rollout is on Friday."));
    for (name, reply) in [
        ("one vector for two chunks", vec![about("fee")]),
        (
            "another length than was asked for",
            vec![vec![1.0, 0.0], vec![0.0, 1.0]],
        ),
        (
            "not a number",
            vec![about("fee"), vec![f32::NAN, 0.0, 0.0, 0.0]],
        ),
        ("all zeros", vec![about("fee"), vec![0.0; 4]]),
    ] {
        let wrong = FnEmbedTransport(|_: &EmbedRequest| {
            Ok(EmbedResponse {
                vectors: reply.clone(),
                input_tokens: 3,
            })
        });
        let report = handled(round(&mut w, &wrong));
        assert_eq!(report.problem, Some("reply_invalid"), "{name}");
        assert_eq!(report.stored, 0, "{name}");
        w.db.connect()
            .batch_execute(
                "UPDATE core_memory.work SET attempts = 0, not_before = '2020-01-01Z'
                  WHERE kind = 'embed'",
            )
            .unwrap();
    }
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_vectors"), 0);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_results WHERE result = 'rejected'"),
        4
    );

    // A provider that turns the binding down is not asked again for ten
    // minutes, and one whose reply never came is recorded as in doubt.
    let rejected = FnEmbedTransport(|_: &EmbedRequest| {
        Err(TransportError::Permanent {
            message: "401".into(),
        })
    });
    assert_eq!(
        handled(round(&mut w, &rejected)).problem,
        Some("binding_rejected")
    );
    w.clock.advance_millis(599_000);
    assert_eq!(round(&mut w, &rejected), EmbedRound::Idle);
    w.clock.advance_millis(1_000);
    let lost = FnEmbedTransport(|_: &EmbedRequest| {
        Err(TransportError::InDoubt {
            message: "timeout".into(),
        })
    });
    assert_eq!(handled(round(&mut w, &lost)).problem, Some("call_in_doubt"));
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_results WHERE result = 'in_doubt'"),
        1
    );
}

#[test]
fn a_long_chunk_is_embedded_by_its_start_and_its_vector_says_how_much() {
    let mut w = embedding("max_input_bytes = 16");
    // Cut between characters: the sixteenth byte is inside `é`.
    let text = "Pricing notes: é and the rest of a long message.";
    w.accept(&chat("1", text));
    let sent = std::sync::Mutex::new(Vec::new());
    let transport = FnEmbedTransport(|request: &EmbedRequest| {
        sent.lock().unwrap().extend(request.inputs.clone());
        vectors(request)
    });
    assert_eq!(handled(round(&mut w, &transport)).stored, 1);
    assert_eq!(*sent.lock().unwrap(), vec!["Pricing notes: ".to_string()]);
    assert_eq!(
        w.count("SELECT input_bytes FROM core_memory.chunk_vectors"),
        15
    );
    assert_eq!(
        w.count("SELECT bytes_sent FROM core_memory.outbound_calls"),
        15
    );
}

#[test]
fn erasing_a_message_removes_its_vectors_and_nothing_else_changes_one() {
    let mut w = embedding("");
    let (erased, _) = w.accept(&chat("1", "The subscription fee goes up in March."));
    w.accept(&chat("2", "The rollout is on Friday."));
    w.accept(&chat("3", "Not embedded yet."));
    embed_all(&mut w);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_vectors"), 3);

    // Even the database owner is refused.
    let mut owner = w.db.connect();
    for sql in [
        "UPDATE core_memory.chunk_vectors SET dimensions = 3",
        "UPDATE core_memory.chunk_vectors SET vector = ARRAY[1, 2, 3, 4]::real[]",
        "DELETE FROM core_memory.chunk_vectors",
        "TRUNCATE core_memory.chunk_vectors",
        "TRUNCATE core_memory.chunk_text CASCADE",
        "SET LOCAL core_memory.erasing = 'on'; DELETE FROM core_memory.chunk_vectors",
        // A vector is as long as it says, and holds numbers only.
        "INSERT INTO core_memory.chunk_vectors
         SELECT workspace_id, seq + 1000, chunk_id, scope, call_id, sha256('other'), binding_id,
                model, 7, input_bytes, vector, embedded_at
           FROM core_memory.chunk_vectors LIMIT 1",
        "INSERT INTO core_memory.chunk_vectors
         SELECT workspace_id, seq + 1000, chunk_id, scope, call_id, sha256('other'), binding_id,
                model, 2, input_bytes, ARRAY[1, NULL]::real[], embedded_at
           FROM core_memory.chunk_vectors LIMIT 1",
        // One vector for a chunk and an embedder.
        "INSERT INTO core_memory.chunk_vectors
         SELECT workspace_id, seq + 1000, chunk_id, scope, call_id, embedder_rev, binding_id,
                model, dimensions, input_bytes, vector, embedded_at
           FROM core_memory.chunk_vectors LIMIT 1",
    ] {
        let message = refused(&mut owner, sql);
        assert!(
            message.contains("cannot be")
                || message.contains("violates")
                || message.contains("cannot truncate"),
            "{sql}: {message}"
        );
    }

    w.store
        .erase_event(&erased, "user_request", "owner")
        .unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_vectors"), 2);
    assert_eq!(
        w.count(&format!(
            "SELECT count(*) FROM core_memory.chunk_vectors AS v
               JOIN core_memory.chunks AS c ON c.chunk_id = v.chunk_id
              WHERE c.event_id = '{}'",
            erased.0
        )),
        0
    );
    // A vector for a message that is erased is refused, whoever offers it.
    let message = refused(
        &mut owner,
        &format!(
            "INSERT INTO core_memory.chunk_vectors
             SELECT v.workspace_id, v.seq + 2000, c.chunk_id, v.scope, v.call_id, v.embedder_rev,
                    v.binding_id, v.model, v.dimensions, v.input_bytes, v.vector, v.embedded_at
               FROM core_memory.chunk_vectors AS v, core_memory.chunks AS c
              WHERE c.event_id = '{}' LIMIT 1",
            erased.0
        ),
    );
    assert!(
        message.contains("violates") || message.contains("only for an event that is live"),
        "{message}"
    );

    // Erasing a message that waits for its vector removes the work too.
    let (waiting, _) = w.accept(&chat("4", "Erased before it was embedded."));
    w.store
        .erase_event(&waiting, "user_request", "owner")
        .unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
}

#[test]
fn a_vector_is_stored_once_and_only_for_a_chunk_its_receipt_names() {
    let mut w = embedding("");
    let (_, first) = w.accept(&chat("1", "The subscription fee goes up in March."));
    let (_, other) = w.accept(&chat("2", "The rollout is on Friday."));
    let embedder = w.engine.embedder().unwrap().clone();
    let now = w.clock.now();
    let permit = |store: &mut PostgresStore| {
        let request = PermitRequest {
            scope: "acme",
            chunk_ids: &first,
            purpose: Purpose::Embed,
            destination: DESTINATION,
            reader: &embedder.embedder_ref,
            attempt: 1,
            repairs: None,
            sending: &|_| {
                Some(Sending {
                    bytes: 10,
                    reserved_tokens: 3,
                })
            },
            budget: None,
            at: now,
        };
        match store.permit(&request).unwrap() {
            Permit::Granted { call_id, .. } => call_id,
            other => panic!("not permitted: {other:?}"),
        }
    };
    let call_id = permit(&mut w.store);
    let offered = [
        VectorWrite {
            chunk_id: first[0],
            input_bytes: 10,
            vector: vec![1.0, 0.0],
        },
        // Not a chunk the call was permitted to send.
        VectorWrite {
            chunk_id: other[0],
            input_bytes: 10,
            vector: vec![0.0, 1.0],
        },
    ];
    let finish = EmbedFinish {
        call_id,
        scope: "acme",
        result: CallResult::Accepted,
        input_tokens: Some(4),
        vectors: &offered,
        embedder: &embedder.embedder_ref,
        work: &[],
        at: now,
    };
    let finished = w.store.finish_embedding(&finish).unwrap();
    assert_eq!((finished.stored, finished.dropped), (1, 1));
    // The end of a call is recorded once. Offered again, it says what the
    // first time stored and stores nothing more.
    let again = w.store.finish_embedding(&finish).unwrap();
    assert_eq!((again.stored, again.dropped), (1, 1));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_vectors"), 1);
    // The chunk that got its vector is owed none; the other still is.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE kind = 'embed'"),
        1
    );

    // A second call for the same chunk, as when a lease ran out and two
    // workers both embedded it, leaves the first vector as it is.
    let second = permit(&mut w.store);
    let finished = w
        .store
        .finish_embedding(&EmbedFinish {
            call_id: second,
            vectors: &[VectorWrite {
                chunk_id: first[0],
                input_bytes: 10,
                vector: vec![0.5, 0.5],
            }],
            ..finish.clone()
        })
        .unwrap();
    assert_eq!((finished.stored, finished.dropped), (0, 1));
    let kept: Vec<f32> =
        w.db.connect()
            .query_one("SELECT vector FROM core_memory.chunk_vectors", &[])
            .unwrap()
            .get(0);
    assert_eq!(kept, vec![1.0, 0.0]);

    // Nothing is stored for a scope that stopped being active while the
    // model worked.
    let (_, paused) = w.accept(&chat("3", "Embedded while the scope was paused."));
    let request = PermitRequest {
        scope: "acme",
        chunk_ids: &paused,
        purpose: Purpose::Embed,
        destination: DESTINATION,
        reader: &embedder.embedder_ref,
        attempt: 1,
        repairs: None,
        sending: &|_| {
            Some(Sending {
                bytes: 10,
                reserved_tokens: 3,
            })
        },
        budget: None,
        at: now,
    };
    let Permit::Granted { call_id, .. } = w.store.permit(&request).unwrap() else {
        panic!("not permitted")
    };
    w.db.connect()
        .batch_execute("SELECT core_memory.set_scope_state('acme', 'paused')")
        .unwrap();
    let finished = w
        .store
        .finish_embedding(&EmbedFinish {
            call_id,
            vectors: &[VectorWrite {
                chunk_id: paused[0],
                input_bytes: 10,
                vector: vec![1.0, 1.0],
            }],
            ..finish
        })
        .unwrap();
    assert_eq!((finished.stored, finished.dropped), (0, 1));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_vectors"), 1);
}

#[test]
fn what_was_stored_before_an_embedder_is_queued_and_a_new_revision_is_queued_again() {
    let mut w = embedding("");
    // Stored by an engine with no embedder.
    let plain = engine_with(CONFIG, &w.clock);
    for (id, text) in [("1", "The subscription fee goes up."), ("2", "Ship it.")] {
        plain
            .admit(&mut w.store, &ScopeGrants::All, &chat(id, text))
            .unwrap();
    }
    plain
        .admit(
            &mut w.store,
            &ScopeGrants::All,
            &tool_call("t1", "shell", json!({"command": "ls"}), "a.md", None),
        )
        .unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);

    let now = w.clock.now();
    let rev = w.engine.embedder().unwrap().embedder_ref.binding_rev;
    let queue = |store: &mut PostgresStore, rev| {
        let reread = Reread {
            vectors: rev,
            ..Reread::default()
        };
        match store.queue_reads("acme", reread, now).unwrap() {
            QueuedReads::Queued { embeddings, .. } => embeddings,
            other => panic!("{other:?}"),
        }
    };
    // Without an embedder nothing is queued for one.
    assert_eq!(queue(&mut w.store, None), 0);
    // The two messages, and not the tool call.
    assert_eq!(queue(&mut w.store, Some(&rev)), 2);
    assert_eq!(queue(&mut w.store, Some(&rev)), 0);
    embed_all(&mut w);
    assert_eq!(queue(&mut w.store, Some(&rev)), 0);

    // Another model, another length or another prefix is another
    // embedder: its vectors are not these, so every chunk is owed one.
    let other = engine_with(&embed_config("dimensions = 4"), &w.clock);
    let other_rev = other.embedder().unwrap().embedder_ref.binding_rev;
    assert_ne!(other_rev, rev);
    assert_eq!(queue(&mut w.store, Some(&other_rev)), 2);
    while let EmbedRound::Handled(report) = other
        .embed_once(&mut w.store, &FnEmbedTransport(vectors))
        .unwrap()
    {
        assert_eq!(report.problem, None);
    }
    // Both stay: a row is never changed, and a query is compared only with
    // the vectors of its own embedder.
    assert_eq!(
        w.count("SELECT count(DISTINCT embedder_rev) FROM core_memory.chunk_vectors"),
        2
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_vectors"), 4);
}

#[test]
fn the_planner_keeps_no_sample_of_a_vector() {
    let mut w = embedding("");
    for n in 0..40 {
        w.accept(&chat(&n.to_string(), &format!("pricing note number {n}")));
    }
    embed_all(&mut w);
    let mut owner = w.db.connect();
    owner
        .batch_execute("ANALYZE core_memory.chunk_vectors")
        .unwrap();
    assert_eq!(
        w.count(
            "SELECT count(*) FROM pg_stats
              WHERE schemaname = 'core_memory' AND tablename = 'chunk_vectors'
                AND attname = 'vector'"
        ),
        0
    );
}

#[test]
fn every_embedding_call_waits_for_a_migration_holding_nothing_else() {
    let mut w = embedding("");
    w.accept(&chat("1", "The subscription fee goes up in March."));
    let mut store = w.db.store();
    {
        let (db, engine) = (&w.db, &w.engine);
        let report = behind_a_migration(db, "embed_once", || {
            engine.embed_once(&mut store, &FnEmbedTransport(vectors))
        });
        assert_eq!(handled(report.unwrap()).stored, 1);
    }

    // Then each step that is the embedder's own.
    let (_, ids) = w.accept(&chat("2", "claimed behind a migration"));
    let (db, engine) = (&w.db, &w.engine);
    let now = w.clock.now();
    let claimed = behind_a_migration(db, "claim_embeddings", || {
        store.claim_embeddings(4, 5, now, now.plus_millis(60_000))
    });
    assert_eq!(claimed.unwrap().unwrap().chunks.len(), 1);
    let embedder = engine.embedder().unwrap();
    let request = PermitRequest {
        scope: "acme",
        chunk_ids: &ids,
        purpose: Purpose::Embed,
        destination: DESTINATION,
        reader: &embedder.embedder_ref,
        attempt: 1,
        repairs: None,
        sending: &|_| {
            Some(Sending {
                bytes: 10,
                reserved_tokens: 3,
            })
        },
        budget: None,
        at: now,
    };
    let Permit::Granted { call_id, .. } = store.permit(&request).unwrap() else {
        panic!("not permitted")
    };
    let offered = [VectorWrite {
        chunk_id: ids[0],
        input_bytes: 10,
        vector: about("fee"),
    }];
    let finish = EmbedFinish {
        call_id,
        scope: "acme",
        result: CallResult::Accepted,
        input_tokens: None,
        vectors: &offered,
        embedder: &embedder.embedder_ref,
        work: &[],
        at: now,
    };
    let finished = behind_a_migration(db, "finish_embedding", || store.finish_embedding(&finish));
    assert_eq!(finished.unwrap().stored, 1);
    let ready = behind_a_migration(db, "meaning_ready", || {
        store.meaning_ready(
            &ScopeGrants::All,
            DESTINATION,
            &embedder.embedder_ref.binding_rev,
        )
    });
    // Whether this server can compare vectors or not, the call answered.
    assert!(matches!(
        ready.unwrap(),
        MeaningReady::NoVectorType | MeaningReady::Ready
    ));
}

// ---- recall ----

#[test]
fn without_the_vector_type_vectors_are_stored_and_recall_answers_from_the_words() {
    let mut w = embedding("");
    w.accept(&chat("1", "The subscription fee goes up in March."));
    embed_all(&mut w);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_vectors"), 1);
    assert_eq!(
        w.store
            .meaning_ready(
                &ScopeGrants::All,
                DESTINATION,
                &w.engine.embedder().unwrap().embedder_ref.binding_rev
            )
            .unwrap(),
        MeaningReady::NoVectorType
    );

    let calls = AtomicU32::new(0);
    let answer = recall(&mut w, &counting(&calls), &request("subscription fee"));
    assert_eq!(answer.found, RecallFound::Matched);
    assert_eq!(answer.meaning, RecallMeaning::NotInstalled);
    assert_eq!(told(&answer)[0].1, MatchedBy::Words);
    // The query was sent nowhere: there was nothing to compare it with.
    assert_eq!(calls.load(Ordering::SeqCst), 0);

    // Asked for meaning alone, the answer says nothing was searched.
    let only = RecallRequest {
        search: Some(RecallSearch::Meaning),
        ..request("what do we charge")
    };
    let answer = recall(&mut w, &counting(&calls), &only);
    assert_eq!(answer.found, RecallFound::NotSearched);
    assert_eq!(answer.meaning, RecallMeaning::NotInstalled);
    assert!(answer.beads.is_empty());
}

#[test]
fn an_engine_with_no_embedder_recalls_by_the_words_and_says_so() {
    let mut w = World::new();
    w.accept(&chat("1", "The subscription fee goes up in March."));
    let calls = AtomicU32::new(0);
    let answer = recall(&mut w, &counting(&calls), &request("subscription"));
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::Matched, RecallMeaning::NotConfigured)
    );
    let words = RecallRequest {
        search: Some(RecallSearch::Words),
        ..request("subscription")
    };
    let answer = recall(&mut w, &counting(&calls), &words);
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::Matched, RecallMeaning::NotAsked)
    );
    let meaning = RecallRequest {
        search: Some(RecallSearch::Meaning),
        ..request("subscription")
    };
    let answer = recall(&mut w, &counting(&calls), &meaning);
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::NotSearched, RecallMeaning::NotConfigured)
    );
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}

#[test]
fn a_question_finds_what_means_the_same_in_other_words() {
    let mut w = embedding("");
    if !install_vectors(&w, "public") {
        return;
    }
    w.accept(&chat("1", "The subscription fee goes up in March."));
    w.accept(&chat("2", "The rollout is on Friday, after the freeze."));
    w.accept(&chat("3", "Mo's kitten chewed through the charger cable."));
    w.accept(&chat("4", "What we charge is under review this quarter."));
    embed_all(&mut w);

    // No chunk holds both words, so the words alone find nothing.
    let asked = request("pricing customers");
    let words = RecallRequest {
        search: Some(RecallSearch::Words),
        ..asked.clone()
    };
    let calls = AtomicU32::new(0);
    let answer = recall(&mut w, &counting(&calls), &words);
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::NoMatch, RecallMeaning::NotAsked)
    );
    // Asked for the words only, the query was sent nowhere.
    assert_eq!(calls.load(Ordering::SeqCst), 0);

    // By meaning, the chunks about what is charged come first, whatever
    // their words.
    let meaning = RecallRequest {
        search: Some(RecallSearch::Meaning),
        limit: Some(2),
        ..asked.clone()
    };
    let answer = recall(&mut w, &counting(&calls), &meaning);
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::Matched, RecallMeaning::Used)
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    let found = told(&answer);
    assert_eq!(found.len(), 2);
    assert!(found.iter().all(|(text, by)| {
        *by == MatchedBy::Meaning && (text.contains("subscription") || text.contains("charge"))
    }));
    // Each says how far its vector is from the query's, nearest first.
    let distances: Vec<f32> = answer
        .beads
        .iter()
        .map(|bead| match bead.via {
            RecallVia::Match {
                distance: Some(distance),
                ..
            } => distance,
            _ => panic!("a match by meaning has a distance"),
        })
        .collect();
    assert!(
        distances[0] <= distances[1] && distances[1] < 0.2,
        "{distances:?}"
    );

    // Asked plainly, both searches run, and the answer says which found
    // each match.
    let answer = recall(&mut w, &counting(&calls), &request("charge review"));
    assert_eq!(answer.meaning, RecallMeaning::Used);
    let found = told(&answer);
    assert_eq!(
        found[0],
        (
            "What we charge is under review this quarter.".to_string(),
            MatchedBy::Both
        )
    );
    assert!(found[1..].iter().all(|(_, by)| *by == MatchedBy::Meaning));
    let RecallVia::Match { rank, .. } = answer.beads[0].via else {
        panic!()
    };
    let RecallVia::Match { rank: next, .. } = answer.beads[1].via else {
        panic!()
    };
    assert!(rank > next);

    // A query with no word to look up still has a meaning to look for.
    let answer = recall(&mut w, &counting(&calls), &request("what about it"));
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::Matched, RecallMeaning::Used)
    );
    let words = RecallRequest {
        search: Some(RecallSearch::Words),
        ..request("what about it")
    };
    let answer = recall(&mut w, &counting(&calls), &words);
    assert_eq!(answer.found, RecallFound::NoTerms);
}

#[test]
fn only_what_is_live_allowed_and_asked_for_is_compared() {
    let mut w = embedding("max_distance = 0.5");
    // In a schema of its own, with a name that must be quoted.
    if !install_vectors(&w, "\"Vector Types\"") {
        return;
    }
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.enroll_scope('other', 'w1');
             SELECT core_memory.grant_destination('other', '{DESTINATION}');"
        ))
        .unwrap();
    let (erased, _) = w.accept(&chat("1", "The subscription fee goes up in March."));
    w.accept(&chat("2", "Our pricing page lists every fee."));
    w.accept(&chat_in("other", "3", "They charge a setup fee as well."));
    w.accept(&chat("4", "The rollout is on Friday."));
    embed_all(&mut w);
    let meaning = RecallRequest {
        search: Some(RecallSearch::Meaning),
        ..request("what do we charge")
    };
    let texts = |answer: &RecallAnswer| -> Vec<String> {
        let mut texts: Vec<String> = told(answer).into_iter().map(|(text, _)| text).collect();
        texts.sort();
        texts
    };
    let calls = AtomicU32::new(0);

    // The three chunks about what is charged, and not the one that is far
    // from the question: the embedder's limit leaves it out.
    let answer = recall(&mut w, &counting(&calls), &meaning);
    assert_eq!(
        texts(&answer),
        [
            "Our pricing page lists every fee.",
            "The subscription fee goes up in March.",
            "They charge a setup fee as well."
        ]
    );

    // An erased message is found by nothing.
    w.store
        .erase_event(&erased, "user_request", "owner")
        .unwrap();
    let answer = recall(&mut w, &counting(&calls), &meaning);
    assert_eq!(
        texts(&answer),
        [
            "Our pricing page lists every fee.",
            "They charge a setup fee as well."
        ]
    );

    // Only the scopes named are searched.
    let named = RecallRequest {
        scopes: Some(vec!["acme".into()]),
        ..meaning.clone()
    };
    let answer = recall(&mut w, &counting(&calls), &named);
    assert_eq!(texts(&answer), ["Our pricing page lists every fee."]);

    // A scope that takes its consent back is no longer searched this way,
    // though its vectors are still stored.
    assert!(w.store.revoke_destination("acme", DESTINATION).unwrap());
    let answer = recall(&mut w, &counting(&calls), &meaning);
    assert_eq!(texts(&answer), ["They charge a setup fee as well."]);
    // Asked of that scope alone, the query is sent nowhere.
    let before = calls.load(Ordering::SeqCst);
    let answer = recall(&mut w, &counting(&calls), &named);
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::NotSearched, RecallMeaning::NoVectors)
    );
    assert_eq!(calls.load(Ordering::SeqCst), before);
    // Its words are still searched.
    let plain = RecallRequest {
        scopes: Some(vec!["acme".into()]),
        ..request("pricing page")
    };
    let answer = recall(&mut w, &counting(&calls), &plain);
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::Matched, RecallMeaning::NoVectors)
    );
    assert_eq!(calls.load(Ordering::SeqCst), before);
}

#[test]
fn an_embedder_that_does_not_answer_is_asked_again_and_then_the_words_answer() {
    let mut w = embedding("");
    if !install_vectors(&w, "public") {
        return;
    }
    w.accept(&chat("1", "The subscription fee goes up in March."));
    embed_all(&mut w);
    let asked = request("subscription");
    let calls = AtomicU32::new(0);

    // Twice down, then up: the third try answers.
    let flaky = FnEmbedTransport(|request: &EmbedRequest| {
        if calls.fetch_add(1, Ordering::SeqCst) < 2 {
            return Err(TransportError::Transient {
                message: "503".into(),
                retry_after_ms: None,
            });
        }
        vectors(request)
    });
    let answer = recall(&mut w, &flaky, &asked);
    assert_eq!(answer.meaning, RecallMeaning::Used);
    assert_eq!(calls.load(Ordering::SeqCst), 3);

    // Down for good: three tries, and the answer is from the words and
    // says so.
    calls.store(0, Ordering::SeqCst);
    let down = FnEmbedTransport(|_: &EmbedRequest| {
        calls.fetch_add(1, Ordering::SeqCst);
        Err(TransportError::InDoubt {
            message: "timeout".into(),
        })
    });
    let answer = recall(&mut w, &down, &asked);
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::Matched, RecallMeaning::Unavailable)
    );
    assert_eq!(told(&answer)[0].1, MatchedBy::Words);
    assert_eq!(calls.load(Ordering::SeqCst), 3);

    // A failure that would repeat is not tried again: a key turned down,
    // a provider that says when to come back, a reply of the wrong shape.
    type Fails = Box<dyn Fn() -> Result<EmbedResponse, TransportError> + Send + Sync>;
    let repeats: [Fails; 3] = [
        Box::new(|| {
            Err(TransportError::Permanent {
                message: "401".into(),
            })
        }),
        Box::new(|| {
            Err(TransportError::Transient {
                message: "429".into(),
                retry_after_ms: Some(20_000),
            })
        }),
        Box::new(|| {
            Ok(EmbedResponse {
                vectors: vec![vec![0.0; 4]],
                input_tokens: 1,
            })
        }),
    ];
    for fails in repeats {
        calls.store(0, Ordering::SeqCst);
        let once = FnEmbedTransport(|_: &EmbedRequest| {
            calls.fetch_add(1, Ordering::SeqCst);
            fails()
        });
        let answer = recall(&mut w, &once, &asked);
        assert_eq!(answer.meaning, RecallMeaning::Unavailable);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
    // Nothing of a query is ever stored: no receipt, no work.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
}

#[test]
fn a_comparison_that_runs_out_of_time_leaves_the_words_to_answer() {
    let mut w = embedding("");
    if !install_vectors(&w, "public") {
        return;
    }
    w.accept(&chat("1", "The subscription fee goes up in March."));
    embed_all(&mut w);
    let embedder = w.engine.embedder().unwrap();
    let mut query = RecallQuery::from_request(&request("subscription"), &ScopeGrants::All).unwrap();
    query.meaning = MeaningLeg::Vector(QuestionVector {
        vector: about("what do we charge"),
        embedder_rev: embedder.embedder_ref.binding_rev,
        destination: DESTINATION.into(),
        max_distance: None,
    });

    // A store that gives a statement a third of a second, and a table
    // that cannot be read in that time: another session holds it.
    let mut config = w.db.config();
    config.statement_timeout = std::time::Duration::from_millis(300);
    let mut store = PostgresStore::new(&config).unwrap();
    assert_eq!(store.recall(&query).unwrap().meaning, RecallMeaning::Used);
    let mut owner = w.db.connect();
    let mut held = owner.transaction().unwrap();
    held.batch_execute("LOCK TABLE core_memory.chunk_vectors IN ACCESS EXCLUSIVE MODE")
        .unwrap();

    // The search by meaning is given up, and the recall is not: the words
    // answer, and the answer says what happened.
    let answer = store.recall(&query).unwrap();
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::Matched, RecallMeaning::Unavailable)
    );
    assert_eq!(told(&answer)[0].1, MatchedBy::Words);
    query.search = Search::Meaning;
    let answer = store.recall(&query).unwrap();
    assert_eq!(
        (answer.found, answer.meaning),
        (RecallFound::NotSearched, RecallMeaning::Unavailable)
    );
    held.rollback().unwrap();
    assert_eq!(store.recall(&query).unwrap().meaning, RecallMeaning::Used);
}

#[test]
fn the_engine_role_embeds_and_recalls_by_meaning_and_can_change_no_vector() {
    let mut w = embedding("query_prefix = \"query: \"");
    if !install_vectors(&w, "public") {
        return;
    }
    w.accept(&chat("1", "The subscription fee goes up in March."));
    let role = unique_name("cm_t_embed_engine");
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
    let mut store = PostgresStore::new(&ConnectConfig::new(url.clone())).unwrap();
    let report = w
        .engine
        .embed_once(&mut store, &FnEmbedTransport(vectors))
        .unwrap();
    assert_eq!(handled(report).stored, 1);

    let sent = std::sync::Mutex::new(Vec::new());
    let transport = FnEmbedTransport(|request: &EmbedRequest| {
        sent.lock().unwrap().extend(request.inputs.clone());
        vectors(request)
    });
    let query =
        RecallQuery::from_request(&request("what do we charge"), &ScopeGrants::All).unwrap();
    let answer = w
        .engine
        .recall(&mut store, Some(&transport), query)
        .unwrap();
    assert_eq!(answer.meaning, RecallMeaning::Used);
    assert_eq!(answer.beads.len(), 1);
    // A query goes out after the prefix the model is told by.
    assert_eq!(
        *sent.lock().unwrap(),
        vec!["query: what do we charge".to_string()]
    );
    drop(store);

    let mut engine = Client::connect(&url, postgres::NoTls).unwrap();
    for sql in [
        "UPDATE core_memory.chunk_vectors SET dimensions = 1",
        "DELETE FROM core_memory.chunk_vectors",
        "TRUNCATE core_memory.chunk_vectors",
    ] {
        let message = refused(&mut engine, sql);
        assert!(message.contains("permission denied"), "{sql}: {message}");
    }
    drop(engine);
    admin
        .batch_execute(&format!("DROP OWNED BY {role}; DROP ROLE {role};"))
        .unwrap();
}

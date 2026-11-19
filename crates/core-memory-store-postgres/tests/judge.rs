//! Judging how beads relate, against PostgreSQL and a stand-in judge: what
//! a judge is shown, consent bead by bead, verdicts for every pair, chains,
//! groups, name links, the second read, repair, retries and erasure.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use std::sync::Mutex;

use common::{CONFIG, World, behind_a_migration, kind_of};
use core_memory_contract::{BeadId, EventId, SourceEvent};
use core_memory_core::model::{
    FnTransport, ModelRequest, ModelResponse, ModelTransport, ScriptedTransport, TransportError,
    Usage,
};
use core_memory_core::store::{
    Change, JudgeClaim, JudgePermit, JudgePermitRequest, JudgeWork, Purpose, Sending, Store,
    WindowOf, Withheld,
};
use core_memory_core::{Clock, JudgeReport, JudgeRound};
use core_memory_store_postgres::{ConnectConfig, PostgresStore, QueuedReads};
use serde_json::{Value, json};

const DESTINATION: &str = "judge.test:8080";

fn judge_config(judge: &str, extra: &str) -> String {
    format!(
        r#"{CONFIG}
[judge]
binding = "careful"
debounce_ms = 0
max_attempts = 3
retry_backoff_ms = 1000
{judge}

[[binding]]
id = "careful"
provider = "stand-in"
endpoint = "http://Judge.Test:8080/v1"
model = "stand-in-1"
max_output_tokens = 2000
{extra}
"#
    )
}

/// A world whose engine judges, with the judge's destination granted to
/// scope `acme`. Labels are written at once unless a test turns the second
/// read on.
fn judging(judge: &str) -> World {
    let judge = if judge.contains("second_read") {
        judge.to_string()
    } else {
        format!("second_read = false\n{judge}")
    };
    let w = World::with_config(&judge_config(&judge, ""));
    allow(&w, "acme");
    w
}

fn allow(w: &World, scope: &str) {
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.grant_destination('{scope}', '{DESTINATION}')"
        ))
        .unwrap();
}

/// A message written `minutes` after noon, whenever it is admitted.
fn said_in(scope: &str, id: &str, minutes: u32, text: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": scope,
        "source_ns": "slack/T01",
        "external_id": id,
        "profile_id": "core.chat_transcript",
        "occurred_at": {
            "at": format!("2026-10-26T{:02}:{:02}:00Z", 12 + minutes / 60, minutes % 60),
            "precision": "second"
        },
        "actor": {"id": "U1", "kind": "human", "role": "user"},
        "media_type": "text/plain",
        "content": {"text": text},
        "parser_contract_version": "slack-export/1"
    }))
    .unwrap()
}

fn said(id: &str, minutes: u32, text: &str) -> SourceEvent {
    said_in("acme", id, minutes, text)
}

/// Admits a one-chunk message and returns its bead.
fn bead(w: &mut World, event: &SourceEvent) -> BeadId {
    let (_, chunks) = w.accept(event);
    assert_eq!(chunks.len(), 1);
    BeadId(chunks[0].0)
}

/// What a request shows the judge: the listed memories and the new ones.
fn shown(request: &ModelRequest) -> (Vec<Value>, Vec<Value>) {
    let user: Value = serde_json::from_str(
        request
            .user
            .split("\n\nYour previous reply")
            .next()
            .unwrap(),
    )
    .unwrap();
    (
        user["listed"].as_array().unwrap().clone(),
        user["new"].as_array().unwrap().clone(),
    )
}

/// The handle of the memory whose text holds `part`.
fn handle(memories: &[Value], part: &str) -> Option<String> {
    memories
        .iter()
        .find(|memory| {
            memory["text"]
                .as_str()
                .is_some_and(|text| text.contains(part))
        })
        .map(|memory| memory["id"].as_str().unwrap().to_string())
}

fn answer(reply: Value) -> Result<ModelResponse, TransportError> {
    Ok(ModelResponse {
        text: reply.to_string(),
        usage: Usage {
            input_tokens: 500,
            output_tokens: 40,
        },
    })
}

/// A judge that relates nothing, whatever it is shown.
fn nothing(request: &ModelRequest) -> Result<ModelResponse, TransportError> {
    let (_, new) = shown(request);
    answer(json!({
        "new": new.iter().map(|m| json!({"id": m["id"], "related": [], "unsure": []})).collect::<Vec<_>>(),
        "names": [],
    }))
}

/// A judge that says what `relates` says: for each new memory, the pairs
/// it relates, as `(text of the other memory, code, label)`.
fn judge_saying(
    relates: &'static [(&'static str, &'static str, &'static str, &'static str)],
) -> impl Fn(&ModelRequest) -> Result<ModelResponse, TransportError> + Send + Sync {
    move |request| {
        let (listed, new) = shown(request);
        let all: Vec<Value> = listed.iter().chain(&new).cloned().collect();
        let entries: Vec<Value> = new
            .iter()
            .map(|memory| {
                let text = memory["text"].as_str().unwrap_or_default();
                let related: Vec<Value> = relates
                    .iter()
                    .filter(|(of, ..)| text.contains(of))
                    .filter_map(|(_, other, rel, label)| {
                        let to = handle(&all, other)?;
                        Some(if label.is_empty() {
                            json!({"to": to, "rel": rel})
                        } else {
                            json!({"to": to, "rel": rel, "label": label})
                        })
                    })
                    .collect();
                json!({"id": memory["id"], "related": related, "unsure": []})
            })
            .collect();
        answer(json!({"new": entries, "names": []}))
    }
}

fn handled(round: JudgeRound) -> JudgeReport {
    match round {
        JudgeRound::Handled(report) => report,
        JudgeRound::Idle => panic!("nothing was due"),
    }
}

fn round(w: &mut World, transport: &dyn ModelTransport) -> JudgeRound {
    w.engine.judge_once(&mut w.store, transport).unwrap()
}

/// Rows of a query as strings, for comparing what a table holds.
fn rows(w: &World, sql: &str) -> Vec<Vec<String>> {
    w.db.connect()
        .query(sql, &[])
        .unwrap()
        .iter()
        .map(|row| {
            (0..row.len())
                .map(|column| row.get::<_, Option<String>>(column).unwrap_or_default())
                .collect()
        })
        .collect()
}

/// The verdicts stored, as `(earlier, later, verdict, direction, label, note)`
/// with each bead named by the text of its chunk.
fn verdicts(w: &World) -> Vec<Vec<String>> {
    rows(
        w,
        "SELECT a.text, b.text, v.verdict, v.direction, coalesce(v.label, ''),
                coalesce(v.note, '')
           FROM core_memory.verdicts AS v
           JOIN core_memory.chunk_text AS a ON a.chunk_id = v.earlier_bead
           JOIN core_memory.chunk_text AS b ON b.chunk_id = v.later_bead
          ORDER BY v.seq",
    )
}

fn row(cells: &[&str]) -> Vec<String> {
    cells.iter().map(|cell| (*cell).to_string()).collect()
}

#[test]
fn new_beads_are_judged_against_the_beads_near_them_and_every_pair_gets_a_verdict() {
    let mut w = judging("");
    let thursday = bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    let export = bead(&mut w, &said("2", 5, "The export still takes three hours."));
    let slipped = bead(&mut w, &said("3", 9, "So the launch slips a day."));
    // Each bead is owed to the judge from the moment it is stored.
    assert_eq!(w.store.work_counts(Purpose::Judge).unwrap().ready, 3);

    let requests: Mutex<Vec<ModelRequest>> = Mutex::new(Vec::new());
    let says = judge_saying(&[
        ("launch slips", "export still takes", "LEADSTO>", ""),
        ("launch slips", "launch is on Thursday", "NEAR", ""),
    ]);
    let transport = FnTransport(|request: &ModelRequest| {
        requests.lock().unwrap().push(request.clone());
        says(request)
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        report,
        JudgeReport {
            workspace: "w1".into(),
            second_read: false,
            new_beads: 3,
            window_beads: 0,
            judged: 3,
            verdicts: 3,
            edges: 2,
            calls: 1,
            input_tokens: 500,
            output_tokens: 40,
            ..JudgeReport::default()
        }
    );
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);

    // The three were new together, in the order of their times.
    let (listed, new) = shown(&requests.lock().unwrap()[0]);
    assert!(listed.is_empty());
    assert_eq!(
        new.iter()
            .map(|m| m["text"].as_str().unwrap())
            .collect::<Vec<_>>(),
        [
            "The launch is on Thursday.",
            "The export still takes three hours.",
            "So the launch slips a day.",
        ]
    );
    assert_eq!(new[0]["at"], "2026-10-26T12:00:00Z");
    assert_eq!(
        (new[0]["from"].as_str(), new[0]["by"].as_str()),
        (Some("S1"), Some("A1"))
    );

    // A row for every pair that was read, a no included.
    assert_eq!(
        verdicts(&w),
        [
            row(&[
                "The launch is on Thursday.",
                "The export still takes three hours.",
                "none",
                "undirected",
                "",
                ""
            ]),
            row(&[
                "The launch is on Thursday.",
                "So the launch slips a day.",
                "near",
                "undirected",
                "",
                ""
            ]),
            row(&[
                "The export still takes three hours.",
                "So the launch slips a day.",
                "leadsto",
                "earlier_to_later",
                "",
                ""
            ]),
        ]
    );
    // Relationships hold IDs and nothing else. A near edge has no
    // direction and is stored from the smaller ID.
    let edges: Vec<(String, uuid::Uuid, uuid::Uuid)> =
        w.db.connect()
            .query(
                "SELECT edge_type, from_bead, to_bead FROM core_memory.edges ORDER BY seq",
                &[],
            )
            .unwrap()
            .iter()
            .map(|row| (row.get(0), row.get(1), row.get(2)))
            .collect();
    assert_eq!(
        edges,
        [
            (
                "near".to_string(),
                thursday.0.min(slipped.0),
                thursday.0.max(slipped.0)
            ),
            ("leadsto".to_string(), export.0, slipped.0),
        ]
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);

    // The call has a receipt that names every bead shown and no scope,
    // and a read that names the new ones.
    let receipt = &rows(
        &w,
        "SELECT o.purpose, o.scope, o.destination, cardinality(o.bead_ids)::text,
                cardinality(o.chunk_ids)::text, o.bytes_sent::text, r.result,
                cardinality(j.new_beads)::text, cardinality(j.window_beads)::text,
                j.read_no::text
           FROM core_memory.outbound_calls AS o
           JOIN core_memory.outbound_results AS r USING (call_id)
           JOIN core_memory.judge_reads AS j USING (call_id)",
    )[0];
    let bytes = "The launch is on Thursday.".len()
        + "The export still takes three hours.".len()
        + "So the launch slips a day.".len();
    assert_eq!(
        receipt,
        &row(&[
            "judge",
            "",
            DESTINATION,
            "3",
            "3",
            &bytes.to_string(),
            "accepted",
            "3",
            "0",
            "1"
        ])
    );

    // A bead that comes later is read against these three, and they are
    // not read against each other again.
    w.clock.advance_millis(60_000);
    bead(&mut w, &said("4", 12, "Friday it is, then."));
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.new_beads, report.window_beads, report.verdicts),
        (1, 3, 3)
    );
    let (listed, new) = shown(&requests.lock().unwrap()[1]);
    assert_eq!(listed.len(), 3);
    assert_eq!(new[0]["text"], "Friday it is, then.");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 6);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM (SELECT DISTINCT earlier_bead, later_bead
                                     FROM core_memory.verdicts) AS pairs"
        ),
        6
    );
    // What a judge wrote is part of the workspace's changes, in order.
    let kinds: Vec<&str> = w.changes().iter().map(kind_of).collect();
    assert_eq!(
        kinds,
        [
            "event", "event", "event", "verdict", "verdict", "verdict", "edge", "edge", "event",
            "verdict", "verdict", "verdict"
        ]
    );
}

#[test]
fn a_bead_is_shown_only_while_its_own_scope_allows_the_judge() {
    let mut w = judging("");
    w.store.enroll_scope("beta", "w1").unwrap();
    w.store.enroll_scope("gamma", "w1").unwrap();
    allow(&w, "gamma");
    let ours = bead(&mut w, &said("1", 0, "Ours, and allowed."));
    let theirs = bead(&mut w, &said_in("beta", "1", 1, "Theirs, and not allowed."));
    bead(&mut w, &said_in("gamma", "1", 2, "A third scope, allowed."));

    let seen: Mutex<Vec<String>> = Mutex::new(Vec::new());
    let transport = FnTransport(|request: &ModelRequest| {
        seen.lock().unwrap().push(request.user.clone());
        nothing(request)
    });
    // The bead whose scope gave no consent is left out and put off. The
    // other two are judged, across their two scopes.
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (
            report.new_beads,
            report.judged,
            report.verdicts,
            report.problem
        ),
        (3, 2, 1, None)
    );
    assert!(!seen.lock().unwrap()[0].contains("Theirs"));
    assert!(seen.lock().unwrap()[0].contains(r#""from":"S2""#));
    assert_eq!(
        rows(
            &w,
            "SELECT state, problem, attempts::text FROM core_memory.work"
        ),
        [row(&["ready", "no_consent", "0"])]
    );
    let named: Vec<uuid::Uuid> =
        w.db.connect()
            .query_one("SELECT bead_ids FROM core_memory.outbound_calls", &[])
            .unwrap()
            .get(0);
    assert!(named.contains(&ours.0) && !named.contains(&theirs.0) && named.len() == 2);

    // Nothing is sent for it alone either, until its scope allows it.
    w.clock.advance_millis(300_000);
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.calls, report.problem), (0, Some("no_consent")));
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
    allow(&w, "beta");
    w.clock.advance_millis(300_000);
    // Then it is read against the others, which are near it in time.
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.new_beads, report.window_beads, report.verdicts),
        (1, 2, 2)
    );

    // A scope that is paused, or withdraws its consent, is not shown from
    // then on: not as a new bead, and not beside one.
    w.store
        .set_scope_state("gamma", core_memory_core::store::ScopeState::Paused)
        .unwrap();
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.revoke_destination('beta', '{DESTINATION}')"
        ))
        .unwrap();
    bead(&mut w, &said("2", 3, "Ours again."));
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.new_beads, report.window_beads, report.verdicts),
        (1, 1, 1)
    );
    let last = seen.lock().unwrap().last().unwrap().clone();
    assert!(
        last.contains("Ours, and allowed.") && !last.contains("Theirs") && !last.contains("third")
    );
}

/// Labels, as `(label, text of the later statement, state of its bead)`.
fn labels(w: &World) -> Vec<Vec<String>> {
    rows(
        w,
        "SELECT l.label, t.text, b.state
           FROM core_memory.labels AS l
           JOIN core_memory.beads AS b ON b.bead_id = l.bead_id
           LEFT JOIN core_memory.chunk_text AS t ON t.chunk_id = l.source_bead
          ORDER BY b.seq",
    )
}

/// `supersedes` edges, as `(text of the later statement, text of the bead it
/// follows)`.
fn chain(w: &World) -> Vec<Vec<String>> {
    rows(
        w,
        "SELECT s.text, t.text
           FROM core_memory.edges AS e
           JOIN core_memory.labels AS l ON l.bead_id = e.from_bead
           JOIN core_memory.chunk_text AS s ON s.chunk_id = l.source_bead
           JOIN core_memory.chunk_text AS t ON t.chunk_id = e.to_bead
          WHERE e.edge_type = 'supersedes'
          ORDER BY e.seq",
    )
}

#[test]
fn a_label_that_changes_what_is_current_waits_for_a_second_read_that_agrees() {
    let mut w = judging("second_read = true");
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 9, "The launch moves to Friday."));
    let requests: Mutex<Vec<ModelRequest>> = Mutex::new(Vec::new());
    let says = judge_saying(&[(
        "moves to Friday",
        "is on Thursday",
        "SUPERSEDES<",
        "supersede",
    )]);
    let transport = FnTransport(|request: &ModelRequest| {
        requests.lock().unwrap().push(request.clone());
        says(request)
    });

    // The first read gives the label. It is held: nothing is superseded.
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (
            report.verdicts,
            report.labels,
            report.held,
            report.second_read
        ),
        (1, 0, 1, false)
    );
    assert_eq!(
        verdicts(&w),
        [row(&[
            "The launch is on Thursday.",
            "The launch moves to Friday.",
            "supersedes",
            "later_to_earlier",
            "supersede",
            "awaits_second_read"
        ])]
    );
    assert!(labels(&w).is_empty() && chain(&w).is_empty());
    assert_eq!(
        rows(&w, "SELECT kind, state FROM core_memory.work"),
        [row(&["second_read", "ready"])]
    );
    assert_eq!(
        rows(
            &w,
            "SELECT label, cardinality(head_beads)::text FROM core_memory.pending_labels"
        ),
        [row(&["supersede", "1"])]
    );

    // The second read is of those two beads alone, and is not told what
    // the first one said.
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (
            report.second_read,
            report.new_beads,
            report.window_beads,
            report.verdicts,
            report.labels,
            report.held
        ),
        (true, 1, 1, 1, 1, 0)
    );
    let second = requests.lock().unwrap()[1].clone();
    let (listed, new) = shown(&second);
    assert_eq!((listed.len(), new.len()), (1, 1));
    assert_eq!(listed[0]["text"], "The launch is on Thursday.");
    assert_eq!(new[0]["text"], "The launch moves to Friday.");
    assert!(!second.user.contains("supersede") && !second.user.contains("label"));
    // It agrees, so the label is written: a bead of its own that stands
    // for the later statement, and an edge to the bead it follows.
    assert_eq!(
        labels(&w),
        [row(&["supersede", "The launch moves to Friday.", "live"])]
    );
    assert_eq!(
        chain(&w),
        [row(&[
            "The launch moves to Friday.",
            "The launch is on Thursday."
        ])]
    );
    assert_eq!(
        verdicts(&w)[1],
        row(&[
            "The launch is on Thursday.",
            "The launch moves to Friday.",
            "supersedes",
            "later_to_earlier",
            "supersede",
            ""
        ])
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.pending_labels"),
        0
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    assert_eq!(
        rows(
            &w,
            "SELECT b.kind, b.created_by, (b.scope IS NULL AND b.event_id IS NULL)::text, j.read_no::text,
                    o.purpose
               FROM core_memory.beads AS b
               JOIN core_memory.judge_reads AS j ON j.call_id = b.call_id
               JOIN core_memory.outbound_calls AS o ON o.call_id = b.call_id
              WHERE b.kind = 'label'"
        )
        .iter()
        .map(|r| r.iter().map(String::as_str).collect::<Vec<_>>().join(" "))
        .collect::<Vec<_>>(),
        ["label judge true 2 second_read"]
    );

    // From now on the judge is shown which of the two is current, and
    // what the later one said of the earlier.
    bead(&mut w, &said("3", 12, "Friday works for everyone."));
    handled(round(
        &mut w,
        &FnTransport(|request: &ModelRequest| {
            requests.lock().unwrap().push(request.clone());
            nothing(request)
        }),
    ));
    let (listed, _) = shown(requests.lock().unwrap().last().unwrap());
    assert_eq!(listed[0]["current"], false);
    assert_eq!(listed[1]["label"], "supersede");
    assert!(listed[1].get("current").is_none() && listed[0].get("label").is_none());
}

#[test]
fn a_second_read_that_differs_changes_nothing_and_both_verdicts_are_kept() {
    let mut w = judging("second_read = true");
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 9, "Maybe Friday would be better?"));
    let first = FnTransport(judge_saying(&[(
        "Maybe Friday",
        "is on Thursday",
        "SUPERSEDES<",
        "supersede",
    )]));
    assert_eq!(handled(round(&mut w, &first)).held, 1);
    // Read again, the judge sees a question and not a decision.
    let second = FnTransport(judge_saying(&[(
        "Maybe Friday",
        "is on Thursday",
        "NEAR",
        "",
    )]));
    let report = handled(round(&mut w, &second));
    assert_eq!(
        (
            report.second_read,
            report.labels,
            report.edges,
            report.verdicts
        ),
        (true, 0, 0, 1)
    );
    assert_eq!(
        verdicts(&w),
        [
            row(&[
                "The launch is on Thursday.",
                "Maybe Friday would be better?",
                "supersedes",
                "later_to_earlier",
                "supersede",
                "awaits_second_read"
            ]),
            row(&[
                "The launch is on Thursday.",
                "Maybe Friday would be better?",
                "near",
                "undirected",
                "",
                ""
            ]),
        ]
    );
    // Nothing is superseded, no label is held any more, and the second
    // read wrote no relationship of its own.
    assert!(labels(&w).is_empty());
    assert_eq!(w.count("SELECT count(*) FROM core_memory.edges"), 0);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.pending_labels"),
        0
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);

    // The same when it gives another label: the two reads do not agree.
    bead(&mut w, &said("3", 20, "It is Friday. Decided."));
    let first = FnTransport(judge_saying(&[(
        "Decided",
        "is on Thursday",
        "SUPERSEDES<",
        "supersede",
    )]));
    assert_eq!(handled(round(&mut w, &first)).held, 1);
    let second = FnTransport(judge_saying(&[(
        "Decided",
        "is on Thursday",
        "SUPERSEDES<",
        "conflict",
    )]));
    assert_eq!(handled(round(&mut w, &second)).labels, 0);
    assert_eq!(
        verdicts(&w).last().unwrap(),
        &row(&[
            "The launch is on Thursday.",
            "It is Friday. Decided.",
            "supersedes",
            "later_to_earlier",
            "conflict",
            "second_read_differs"
        ])
    );
    assert!(labels(&w).is_empty());
}

#[test]
fn a_chain_has_one_head_and_grows_only_there() {
    let mut w = judging("");
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 5, "The launch moves to Friday."));
    let transport = FnTransport(judge_saying(&[
        (
            "moves to Friday",
            "is on Thursday",
            "SUPERSEDES<",
            "supersede",
        ),
        (
            "Friday is confirmed",
            "moves to Friday",
            "SUPERSEDES<",
            "reaffirm",
        ),
        // A statement that arrives late and is older than the one that
        // is current: the current one is the later statement.
        (
            "was Wednesday at first",
            "Friday is confirmed",
            "SUPERSEDES>",
            "reaffirm",
        ),
        (
            "Saturday, says ops",
            "Friday is confirmed",
            "SUPERSEDES<",
            "conflict",
        ),
    ]));
    // With no second read, a label is written by the read that gives it.
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.labels, report.held), (1, 0));
    bead(&mut w, &said("3", 9, "Friday is confirmed."));
    assert_eq!(handled(round(&mut w, &transport)).labels, 1);
    assert_eq!(
        chain(&w),
        [
            row(&["The launch moves to Friday.", "The launch is on Thursday."]),
            row(&["Friday is confirmed.", "The launch moves to Friday."]),
        ]
    );

    // The late arrival is followed by the statement that is current,
    // which already carries a label of that kind and follows one bead:
    // its label now follows two.
    bead(&mut w, &said("4", 2, "It was Wednesday at first."));
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.labels, report.labels_dropped), (1, 0));
    assert_eq!(
        chain(&w)[2],
        row(&["Friday is confirmed.", "It was Wednesday at first."])
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.labels"), 2);
    assert_eq!(
        verdicts(&w).last().unwrap(),
        &row(&[
            "It was Wednesday at first.",
            "Friday is confirmed.",
            "supersedes",
            "later_to_earlier",
            "reaffirm",
            ""
        ])
    );

    // A conflict follows the head like any label, and is then the head,
    // with its label for all to see.
    bead(&mut w, &said("5", 15, "Saturday, says ops."));
    assert_eq!(handled(round(&mut w, &transport)).labels, 1);
    assert_eq!(
        labels(&w).last().unwrap(),
        &row(&["conflict", "Saturday, says ops.", "live"])
    );
    // Every bead but the last statement has been superseded, each once.
    assert_eq!(
        rows(
            &w,
            "SELECT t.text FROM core_memory.beads AS b
               JOIN core_memory.chunk_text AS t ON t.chunk_id = b.chunk_id
              WHERE NOT EXISTS (SELECT 1 FROM core_memory.edges AS e
                                 WHERE e.to_bead = b.bead_id AND e.edge_type = 'supersedes')"
        ),
        [row(&["Saturday, says ops."])]
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM (SELECT to_bead FROM core_memory.edges
                                    WHERE edge_type = 'supersedes'
                                    GROUP BY to_bead HAVING count(*) > 1) AS twice"
        ),
        0
    );
}

#[test]
fn a_label_its_source_cannot_carry_is_not_written_and_the_verdict_says_so() {
    let mut w = judging("");
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 1, "The budget is forty thousand."));
    bead(&mut w, &said("3", 2, "The owner is Priya."));
    bead(
        &mut w,
        &said("4", 9, "Launch Friday, budget fifty, owner Sam."),
    );
    let transport = FnTransport(judge_saying(&[
        (
            "Launch Friday, budget",
            "is on Thursday",
            "SUPERSEDES<",
            "supersede",
        ),
        (
            "Launch Friday, budget",
            "budget is forty",
            "SUPERSEDES<",
            "supersede",
        ),
        // Later reads: the same statement is found to follow a third bead,
        // and to carry another label.
        ("Now about the owner", "owner is Priya", "NEAR", ""),
    ]));
    assert_eq!(handled(round(&mut w, &transport)).labels, 1);
    assert_eq!(chain(&w).len(), 2);

    // A judge that now says the same statement follows a third bead: its
    // label follows two already. The verdict is stored, and says why no
    // label was written.
    let third = FnTransport(|request: &ModelRequest| {
        let (listed, new) = shown(request);
        let source = handle(&listed, "Launch Friday, budget").unwrap();
        answer(
            json!({"new": [{"id": new[0]["id"], "unsure": [], "related": [
            {"to": source, "rel": "SUPERSEDES>", "label": "supersede"}]}], "names": []}),
        )
    });
    bead(&mut w, &said("5", 3, "The owner was Priya until now."));
    let report = handled(round(&mut w, &third));
    assert_eq!(
        (report.labels, report.labels_dropped, report.verdicts),
        (0, 1, 4)
    );
    assert_eq!(
        verdicts(&w).last().unwrap(),
        &row(&[
            "The owner was Priya until now.",
            "Launch Friday, budget fifty, owner Sam.",
            "supersedes",
            "later_to_earlier",
            "supersede",
            "label_taken"
        ])
    );
    assert_eq!(chain(&w).len(), 2);

    // And one that gives it another label than the one it carries.
    let other = FnTransport(|request: &ModelRequest| {
        let (listed, new) = shown(request);
        let source = handle(&listed, "Launch Friday, budget").unwrap();
        answer(
            json!({"new": [{"id": new[0]["id"], "unsure": [], "related": [
            {"to": source, "rel": "SUPERSEDES>", "label": "retract"}]}], "names": []}),
        )
    });
    bead(&mut w, &said("6", 4, "Sam was never the owner before."));
    let report = handled(round(&mut w, &other));
    assert_eq!((report.labels, report.labels_dropped), (0, 1));
    assert_eq!(verdicts(&w).last().unwrap()[5], "label_taken");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.labels"), 1);
}

#[test]
fn chunks_of_one_memory_become_a_group_and_a_later_part_joins_it() {
    let mut w = judging("");
    // A note the chunker cuts in three.
    let (_, parts) = w.accept(&common::note(
        "plan",
        "Phase one moves read traffic.\n\nPhase two moves the writes.\n\nPhase three retires it.",
    ));
    assert_eq!(parts.len(), 3);
    allow(&w, "acme");
    let requests: Mutex<Vec<ModelRequest>> = Mutex::new(Vec::new());
    let says = judge_saying(&[
        ("Phase two", "Phase one", "CONTAINS", ""),
        ("Phase three", "Phase one", "CONTAINS", ""),
        ("Phase three", "Phase two", "CONTAINS", ""),
        ("Phase four", "Phase one", "CONTAINS", ""),
        ("Phase four", "Phase two", "CONTAINS", ""),
        ("Phase four", "Phase three", "CONTAINS", ""),
    ]);
    let transport = FnTransport(|request: &ModelRequest| {
        requests.lock().unwrap().push(request.clone());
        says(request)
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (
            report.new_beads,
            report.verdicts,
            report.groups,
            report.edges
        ),
        (3, 3, 1, 0)
    );
    // The judge was shown that the three are pieces of one message.
    let (_, new) = shown(&requests.lock().unwrap()[0]);
    assert_eq!(
        new.iter()
            .map(|m| (m["message"].as_str().unwrap(), m["part"].as_str().unwrap()))
            .collect::<Vec<_>>(),
        [("M1", "1 of 3"), ("M1", "2 of 3"), ("M1", "3 of 3")]
    );
    // One group bead, which contains each of them.
    let groups = |w: &World| {
        rows(
            w,
            "SELECT g.bead_id::text, count(*)::text, g.state
               FROM core_memory.beads AS g
               JOIN core_memory.edges AS e ON e.from_bead = g.bead_id AND e.edge_type = 'contains'
              WHERE g.kind = 'group'
              GROUP BY g.bead_id, g.state",
        )
    };
    let made = groups(&w);
    assert_eq!(
        (made.len(), made[0][1].as_str(), made[0][2].as_str()),
        (1, "3", "live")
    );

    // A message that continues the same account joins that group. It is
    // shown the group, and no second one is made.
    w.clock.advance_millis(60_000);
    bead(
        &mut w,
        &said("later", 30, "Phase four was added afterwards."),
    );
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.window_beads, report.groups), (3, 1));
    let (listed, _) = shown(requests.lock().unwrap().last().unwrap());
    assert!(listed.iter().all(|memory| memory["group"] == "G1"));
    let joined = groups(&w);
    assert_eq!(joined.len(), 1);
    assert_eq!(
        (joined[0][0].as_str(), joined[0][1].as_str()),
        (made[0][0].as_str(), "4")
    );
}

#[test]
fn a_name_link_is_two_ranges_and_points_into_both_chunks() {
    let mut w = judging("");
    let first = bead(&mut w, &said("1", 0, "Ask Priya Raman about the export."));
    let second = bead(&mut w, &said("2", 4, "Done, PR says an hour."));
    let transport = FnTransport(|request: &ModelRequest| {
        let (_, new) = shown(request);
        answer(json!({"new": [
            {"id": new[0]["id"], "related": [], "unsure": []},
            {"id": new[1]["id"], "related": [], "unsure": ["N1"]},
        ], "names": [{"a": "N2", "a_name": "PR", "b": "N1", "b_name": "Priya Raman"}]}))
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.names, report.names_rejected, report.verdicts),
        (1, 0, 1)
    );
    let link =
        w.db.connect()
            .query_one(
                "SELECT a_bead, a_start, a_end, b_bead, b_start, b_end FROM core_memory.name_links",
                &[],
            )
            .unwrap();
    assert_eq!(
        (
            link.get::<_, uuid::Uuid>(0),
            link.get::<_, i64>(1),
            link.get::<_, i64>(2),
            link.get::<_, uuid::Uuid>(3),
            link.get::<_, i64>(4),
            link.get::<_, i64>(5),
        ),
        // A link's ends are in the order of their beads' IDs.
        if first < second {
            (first.0, 4, 15, second.0, 6, 8)
        } else {
            (second.0, 6, 8, first.0, 4, 15)
        }
    );
    // No name is copied anywhere: the link is IDs and offsets.
    assert_eq!(
        verdicts(&w),
        [row(&[
            "Ask Priya Raman about the export.",
            "Done, PR says an hour.",
            "unsure",
            "undirected",
            "",
            ""
        ])]
    );
}

/// Admits a one-chunk message and returns its event and its bead.
fn message(w: &mut World, event: &SourceEvent) -> (EventId, BeadId) {
    let (event_id, chunks) = w.accept(event);
    assert_eq!(chunks.len(), 1);
    (event_id, BeadId(chunks[0].0))
}

fn erase(w: &World, event: EventId) {
    w.db.connect()
        .execute(
            "SELECT core_memory.erase_event($1, 'user_request', 'owner')",
            &[&event.0],
        )
        .unwrap();
}

/// The texts of the beads nothing has superseded.
fn current(w: &World) -> Vec<Vec<String>> {
    rows(
        w,
        "SELECT t.text FROM core_memory.beads AS b
           JOIN core_memory.chunk_text AS t ON t.chunk_id = b.chunk_id
          WHERE b.state = 'live'
            AND NOT EXISTS (SELECT 1 FROM core_memory.edges AS e
                             WHERE e.to_bead = b.bead_id AND e.edge_type = 'supersedes')
          ORDER BY b.seq",
    )
}

#[test]
fn erasing_a_message_removes_what_was_said_about_it_and_leaves_the_chain_as_it_was() {
    let mut w = judging("");
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    let (middle, _) = message(&mut w, &said("2", 5, "The launch moves to Friday."));
    let (head, _) = message(&mut w, &said("3", 9, "Friday is confirmed."));
    let requests: Mutex<Vec<ModelRequest>> = Mutex::new(Vec::new());
    let says = judge_saying(&[
        (
            "moves to Friday",
            "is on Thursday",
            "SUPERSEDES<",
            "supersede",
        ),
        (
            "Friday is confirmed",
            "moves to Friday",
            "SUPERSEDES<",
            "reaffirm",
        ),
        ("Friday is confirmed", "is on Thursday", "NEAR", ""),
    ]);
    let transport = FnTransport(|request: &ModelRequest| {
        requests.lock().unwrap().push(request.clone());
        says(request)
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.verdicts, report.labels, report.edges), (3, 2, 1));
    assert_eq!(current(&w), [row(&["Friday is confirmed."])]);

    // The middle of the chain is erased. Every verdict about it goes: its
    // columns were computed from what it said.
    erase(&w, middle);
    assert_eq!(
        verdicts(&w),
        [row(&[
            "The launch is on Thursday.",
            "Friday is confirmed.",
            "near",
            "undirected",
            "",
            ""
        ])]
    );
    // Its label bead is erased with it and keeps its place: the edges
    // stay, as IDs, and what it superseded is not current again.
    assert_eq!(
        labels(&w),
        [
            row(&["supersede", "", "erased"]),
            row(&["reaffirm", "Friday is confirmed.", "live"])
        ]
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.edges"), 3);
    assert_eq!(current(&w), [row(&["Friday is confirmed."])]);
    // A consumer is told of the erasure. It still gets the edges, which
    // name a bead it no longer has, and no bead record for a label whose
    // statement is gone.
    let changes = w.changes();
    let kinds: Vec<&str> = changes.iter().map(kind_of).collect();
    assert_eq!(
        kinds,
        [
            "event", "event", "verdict", "edge", "bead", "edge", "edge", "erasure"
        ]
    );
    let Some(Change::Bead(kept)) = changes.iter().find(|c| kind_of(c) == "bead") else {
        panic!("no bead record");
    };
    assert_eq!(
        serde_json::to_value(&kept.made).unwrap()["label"],
        "reaffirm"
    );

    // The next read is shown what is left: the first statement, still
    // marked as replaced, and the one that is current.
    bead(&mut w, &said("4", 12, "When is the launch again?"));
    handled(round(&mut w, &transport));
    let (listed, _) = shown(requests.lock().unwrap().last().unwrap());
    assert_eq!(
        listed
            .iter()
            .map(|m| (m["text"].as_str().unwrap(), m["current"].as_bool()))
            .collect::<Vec<_>>(),
        [
            ("The launch is on Thursday.", Some(false)),
            ("Friday is confirmed.", None)
        ]
    );

    // The head is erased. The chain's head is now a bead that is gone,
    // and nothing it stood on comes back as current.
    erase(&w, head);
    assert_eq!(
        labels(&w),
        [
            row(&["supersede", "", "erased"]),
            row(&["reaffirm", "", "erased"])
        ]
    );
    assert_eq!(current(&w), [row(&["When is the launch again?"])]);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.edges"), 3);
    assert_eq!(
        verdicts(&w),
        [row(&[
            "The launch is on Thursday.",
            "When is the launch again?",
            "none",
            "undirected",
            "",
            ""
        ])]
    );
    // No table holds a word of what was erased.
    for word in ["moves to Friday", "confirmed"] {
        assert_eq!(
            w.count(&format!(
                "SELECT count(*) FROM core_memory.chunk_text WHERE text LIKE '%{word}%'"
            )),
            0
        );
    }
}

#[test]
fn erasing_reaches_name_links_held_labels_and_groups() {
    let mut w = judging("second_read = true");
    let (asked, _) = message(&mut w, &said("1", 0, "Ask Priya Raman about the export."));
    let (answered, _) = message(&mut w, &said("2", 1, "PR says the export takes an hour."));
    let (corrected, _) = message(&mut w, &said("3", 2, "PR says the export takes two hours."));
    let transport = FnTransport(|request: &ModelRequest| {
        let (_, new) = shown(request);
        if new.len() < 3 {
            return nothing(request);
        }
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "related": [{"to": "N1", "rel": "CONTAINS"}], "unsure": []},
            {"id": "N3", "unsure": [], "related": [
                {"to": "N2", "rel": "SUPERSEDES<", "label": "supersede"}]},
        ], "names": [{"a": "N2", "a_name": "PR", "b": "N1", "b_name": "Priya Raman"},
                     {"a": "N3", "a_name": "PR", "b": "N1", "b_name": "Priya Raman"}]}))
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.names, report.groups, report.held), (2, 1, 1));
    let counts = |w: &World| {
        (
            w.count("SELECT count(*) FROM core_memory.name_links"),
            w.count("SELECT count(*) FROM core_memory.pending_labels"),
            w.count("SELECT count(*) FROM core_memory.work WHERE kind = 'second_read'"),
            w.count(
                "SELECT count(*) FROM core_memory.beads WHERE kind = 'group' AND state = 'live'",
            ),
        )
    };
    assert_eq!(counts(&w), (2, 1, 1, 1));

    // A bead a held label follows is erased: the label is no longer held,
    // and its second read no longer owed. One name link pointed into the
    // erased chunk. The group keeps its other member.
    erase(&w, answered);
    assert_eq!(counts(&w), (1, 0, 0, 1));
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);
    // The group goes with its last live member, and the other link with
    // the chunk it pointed into.
    erase(&w, asked);
    assert_eq!(counts(&w), (0, 0, 0, 0));
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.beads WHERE kind = 'group' AND state = 'erased'"),
        1
    );
    // The edges of the group stay, as IDs.
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.edges WHERE edge_type = 'contains'"),
        2
    );
    let _ = corrected;
}

#[test]
fn nothing_is_stored_about_a_bead_erased_or_stopped_while_the_judge_was_reading() {
    let mut w = judging("");
    w.store.enroll_scope("beta", "w1").unwrap();
    allow(&w, "beta");
    let (gone, _) = message(&mut w, &said("1", 0, "Erased while it was read."));
    bead(&mut w, &said("2", 1, "Kept."));
    bead(
        &mut w,
        &said_in("beta", "1", 2, "Its scope is paused meanwhile."),
    );
    let url = w.db.url.clone();
    // The judge relates all three. While it does, one is erased and the
    // scope of another is paused.
    let transport = FnTransport(move |_: &ModelRequest| {
        let mut host = postgres::Client::connect(&url, postgres::NoTls).unwrap();
        host.execute(
            "SELECT core_memory.erase_event($1, 'user_request', 'owner')",
            &[&gone.0],
        )
        .unwrap();
        host.batch_execute("SELECT core_memory.set_scope_state('beta', 'paused')")
            .unwrap();
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "related": [{"to": "N1", "rel": "NEAR"}], "unsure": []},
            {"id": "N3", "related": [{"to": "N1", "rel": "NEAR"}, {"to": "N2", "rel": "NEAR"}],
             "unsure": []},
        ], "names": [{"a": "N2", "a_name": "Kept", "b": "N1", "b_name": "Erased"}]}))
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (
            report.judged,
            report.verdicts,
            report.dropped,
            report.edges,
            report.names
        ),
        (1, 0, 3, 0, 0)
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 0);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.edges"), 0);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.name_links"), 0);
    // The call is on record as made, and as answered.
    assert_eq!(
        rows(&w, "SELECT r.result FROM core_memory.outbound_results AS r"),
        [row(&["accepted"])]
    );
    // The paused scope's bead keeps its work, put back in the queue: a
    // lease left on it would keep every judge out of the workspace. It is
    // judged once its scope is active again, against the bead that was
    // kept.
    assert_eq!(
        rows(
            &w,
            "SELECT scope, state, attempts::text, problem FROM core_memory.work"
        ),
        [row(&["beta", "ready", "0", "scope_not_active"])]
    );
    w.store
        .set_scope_state("beta", core_memory_core::store::ScopeState::Active)
        .unwrap();
    let report = handled(round(&mut w, &FnTransport(nothing)));
    assert_eq!(
        (report.new_beads, report.window_beads, report.verdicts),
        (1, 1, 1)
    );
}

#[test]
fn a_reply_that_breaks_the_rules_is_repaired_once_and_then_each_bead_is_read_alone() {
    let mut w = judging("");
    for (id, text) in [("1", "One."), ("2", "Two.")] {
        bead(&mut w, &said(id, 0, text));
    }
    let both = json!({"new": [
        {"id": "N1", "related": [], "unsure": []},
        {"id": "N2", "related": [{"to": "N1", "rel": "NEAR"}], "unsure": []},
    ], "names": []});
    let transport = ScriptedTransport::new(vec![
        // The second bead is not answered for.
        answer(json!({"new": [{"id": "N1", "related": [], "unsure": []}], "names": []})),
        answer(both),
    ]);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.calls, report.judged, report.verdicts, report.problem),
        (2, 2, 1, None)
    );
    let requests = transport.requests();
    assert!(requests[1].user.starts_with(&requests[0].user));
    assert!(
        requests[1]
            .user
            .contains("Your previous reply was rejected:\n- no entry for N2")
    );
    assert_eq!(
        rows(
            &w,
            "SELECT o.attempt::text, (o.repairs IS NOT NULL)::text, r.result
               FROM core_memory.outbound_calls AS o
               JOIN core_memory.outbound_results AS r USING (call_id) ORDER BY o.seq"
        ),
        [
            row(&["1", "false", "rejected"]),
            row(&["2", "true", "accepted"])
        ]
    );

    // A judge that never answers for its beads. After the repair fails,
    // each bead is read on its own, so that one cannot hold another back.
    w.clock.advance_millis(60_000);
    for (id, text) in [("3", "Three."), ("4", "Four.")] {
        bead(&mut w, &said(id, 30, text));
    }
    let silent = FnTransport(|_: &ModelRequest| answer(json!({"new": [], "names": []})));
    let report = handled(round(&mut w, &silent));
    assert_eq!(
        (report.new_beads, report.calls, report.problem),
        (2, 2, Some("reply_invalid"))
    );
    assert_eq!(
        rows(
            &w,
            "SELECT state, alone::text, attempts::text, problem FROM core_memory.work"
        ),
        [
            row(&["ready", "true", "1", "reply_invalid"]),
            row(&["ready", "true", "1", "reply_invalid"])
        ]
    );
    assert_eq!(round(&mut w, &silent), JudgeRound::Idle);
    let mut alone = 0;
    for _ in 0..12 {
        match round(&mut w, &silent) {
            JudgeRound::Handled(report) => {
                assert_eq!(report.new_beads, 1, "one bead to a read from now on");
                alone += 1;
            }
            JudgeRound::Idle => w.clock.advance_millis(1_500),
        }
    }
    // Two more attempts for each, and then they are set aside.
    assert_eq!(alone, 4);
    let counts = w.store.work_counts(Purpose::Judge).unwrap();
    assert_eq!((counts.ready, counts.leased, counts.failed), (0, 0, 2));
    assert_eq!(counts.problems, [("reply_invalid".to_string(), 2)]);
    // No judgment was stored for them, and no pair of theirs is a no.
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 1);
    // An operator puts them back.
    let now = w.clock.now();
    assert_eq!(w.store.retry_failed_reads(Some("acme"), now).unwrap(), 2);
    let report = handled(round(&mut w, &FnTransport(nothing)));
    assert_eq!((report.new_beads, report.judged), (1, 1));
}

#[test]
fn a_call_that_fails_is_recorded_and_a_bead_the_judge_declines_alone_is_set_aside() {
    let mut w = judging("");
    // A bead from before, so that a bead read alone has one to be read
    // with. By itself it had none, and no call was made for it.
    bead(&mut w, &said("0", 0, "Zero."));
    let report = handled(round(&mut w, &FnTransport(nothing)));
    assert_eq!(
        (report.calls, report.problem),
        (0, Some("nothing_to_compare"))
    );
    bead(&mut w, &said("1", 1, "One."));
    bead(&mut w, &said("2", 2, "Two."));
    let declined = || {
        Err(TransportError::Refused {
            message: "the model declined".into(),
        })
    };
    let transport = ScriptedTransport::new(vec![
        Err(TransportError::Transient {
            message: "overloaded".into(),
            retry_after_ms: Some(5_000),
        }),
        // Declined with two new beads: either may be the reason, so each
        // is tried alone.
        declined(),
        declined(),
        nothing(&ModelRequest {
            system: String::new(),
            user: r#"{"listed":[],"new":[{"id":"N1"}]}"#.into(),
            response_schema: Value::Null,
            max_output_tokens: 0,
        }),
    ]);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.calls, report.problem),
        (1, Some("provider_unavailable"))
    );
    w.clock.advance_millis(4_000);
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);
    w.clock.advance_millis(1_000);
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.new_beads, report.problem),
        (2, Some("model_refused"))
    );
    w.clock.advance_millis(1_000);
    // Alone, the first is declined again and is set aside at once.
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.new_beads, report.problem),
        (1, Some("model_refused"))
    );
    // The second is judged, alone: the first is waiting no more, but it
    // was not judged either, so it is not shown beside it.
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (
            report.new_beads,
            report.window_beads,
            report.judged,
            report.problem
        ),
        (1, 1, 1, None)
    );
    assert_eq!(
        rows(
            &w,
            "SELECT state, attempts::text, problem FROM core_memory.work"
        ),
        [row(&["failed", "3", "model_refused"])]
    );
    assert_eq!(
        rows(
            &w,
            "SELECT r.result FROM core_memory.outbound_calls AS o
               JOIN core_memory.outbound_results AS r USING (call_id) ORDER BY o.seq"
        ),
        [
            row(&["not_sent"]),
            row(&["refused"]),
            row(&["refused"]),
            row(&["accepted"])
        ]
    );
}

fn claim(w: &World, max_new: u32) -> JudgeClaim {
    let now = w.clock.now();
    JudgeClaim {
        now,
        lease_until: now.plus_millis(60_000),
        max_new,
        max_attempts: 3,
        within_ms: 3_600_000,
        wait_for_reads_ms: None,
    }
}

#[test]
fn one_judge_reads_in_a_workspace_at_a_time_and_a_bead_is_handed_out_only_so_often() {
    let mut w = judging("");
    for (id, text) in [("1", "One."), ("2", "Two."), ("3", "Three.")] {
        bead(&mut w, &said(id, 0, text));
    }
    w.clock.advance_millis(10);
    w.store.create_workspace("w2").unwrap();
    w.store.enroll_scope("other", "w2").unwrap();
    allow(&w, "other");
    bead(&mut w, &said_in("other", "1", 0, "In another workspace."));

    // The work that has waited longest is the first workspace's. Two of
    // its beads are taken, and the third must wait: a second judge in the
    // same workspace would read pairs the first is reading.
    let first = w.store.claim_judgment(&claim(&w, 2)).unwrap().unwrap();
    let JudgeWork::Window(taken) = &first.work else {
        panic!("not a window read");
    };
    assert_eq!(
        (first.workspace.as_str(), taken.len(), taken[0].attempts),
        ("w1", 2, 1)
    );
    let second = w.store.claim_judgment(&claim(&w, 2)).unwrap().unwrap();
    assert_eq!(second.workspace, "w2");
    // Each workspace has its own key for the IDs of what a judge makes.
    assert_ne!(first.id_key, second.id_key);
    assert!(w.store.claim_judgment(&claim(&w, 2)).unwrap().is_none());
    assert_eq!(w.store.work_counts(Purpose::Judge).unwrap().ready, 1);

    // Both judges die. Their work is handed out again when the lease has
    // run out, three times in all, and then set aside.
    for attempt in 2..=3 {
        w.clock.advance_millis(60_001);
        let again = w.store.claim_judgment(&claim(&w, 2)).unwrap().unwrap();
        let JudgeWork::Window(taken) = &again.work else {
            panic!("not a window read");
        };
        assert!(
            taken.iter().any(|bead| bead.attempts == attempt),
            "{taken:?}"
        );
        w.store.claim_judgment(&claim(&w, 2)).unwrap().unwrap();
    }
    w.clock.advance_millis(60_001);
    // What is left to hand out is the bead that was never taken with the
    // other two.
    let rest = w.store.claim_judgment(&claim(&w, 2)).unwrap();
    let handed: usize = match rest.map(|batch| batch.work) {
        Some(JudgeWork::Window(taken)) => taken.len(),
        _ => 0,
    };
    assert!(handed <= 1);
    assert!(
        w.count(
            "SELECT count(*) FROM core_memory.work
              WHERE kind = 'judge' AND state = 'failed' AND attempts = 3
                AND problem = 'attempts_spent'"
        ) >= 3
    );
}

#[test]
fn a_bead_that_arrives_late_meets_the_beads_after_it_and_no_pair_is_read_twice() {
    // One new bead to a read and a window of an hour, so that each step
    // can be told apart.
    let mut w = judging("max_new_beads = 1\nwindow_ms = 3600000");
    let requests: Mutex<Vec<ModelRequest>> = Mutex::new(Vec::new());
    let transport = FnTransport(|request: &ModelRequest| {
        requests.lock().unwrap().push(request.clone());
        nothing(request)
    });
    let texts = |memories: &[Value]| -> Vec<String> {
        memories
            .iter()
            .map(|m| m["text"].as_str().unwrap().to_string())
            .collect()
    };
    let read = |w: &mut World| {
        let report = handled(round(w, &transport));
        let (listed, new) = shown(requests.lock().unwrap().last().unwrap());
        (texts(&listed), texts(&new), report.verdicts)
    };
    // A bead with no other to be read with is settled without a call.
    let alone = |w: &mut World| {
        let sent = requests.lock().unwrap().len();
        let report = handled(round(w, &transport));
        assert_eq!(
            (report.calls, report.verdicts, report.problem),
            (0, 0, Some("nothing_to_compare"))
        );
        assert_eq!(requests.lock().unwrap().len(), sent);
    };

    bead(&mut w, &said("a", 10, "a"));
    bead(&mut w, &said("b", 20, "b"));
    // The first comes up while the second still waits its turn, so it has
    // none to be read with. The second is then shown the first.
    alone(&mut w);
    assert_eq!(
        read(&mut w),
        (vec!["a".to_string()], vec!["b".to_string()], 1)
    );

    // A message written between the two arrives after both. It is read
    // against the one before it and the one after it.
    bead(&mut w, &said("c", 15, "c"));
    assert_eq!(
        read(&mut w),
        (
            vec!["a".to_string(), "b".to_string()],
            vec!["c".to_string()],
            2
        )
    );
    // In the verdict the pair is in the order of the times, whichever of
    // the two was new.
    assert_eq!(
        verdicts(&w).last().unwrap(),
        &row(&["c", "b", "none", "undirected", "", ""])
    );
    // A message more than the window away from all of them meets none.
    bead(&mut w, &said("d", 200, "d"));
    alone(&mut w);
    bead(&mut w, &said("e", 30, "e"));
    assert_eq!(
        read(&mut w),
        (
            vec!["a".to_string(), "c".to_string(), "b".to_string()],
            vec!["e".to_string()],
            3
        )
    );
    // Every pair within the window was read, and each once.
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 6);
    assert_eq!(
        w.count(
            "SELECT count(*) FROM (
                 SELECT DISTINCT least(earlier_bead, later_bead), greatest(earlier_bead, later_bead)
                   FROM core_memory.verdicts) AS pairs"
        ),
        6
    );
}

#[test]
fn the_window_is_the_nearest_beads_up_to_its_limits_and_open_conflicts_come_first() {
    let mut w = judging("max_new_beads = 1\nmax_window_beads = 3\nmax_conflict_beads = 2");
    let requests: Mutex<Vec<ModelRequest>> = Mutex::new(Vec::new());
    let says = judge_saying(&[(
        "Saturday, says ops",
        "The launch is on Friday",
        "SUPERSEDES<",
        "conflict",
    )]);
    let transport = FnTransport(|request: &ModelRequest| {
        requests.lock().unwrap().push(request.clone());
        says(request)
    });
    let listed = |requests: &Mutex<Vec<ModelRequest>>| -> Vec<String> {
        shown(requests.lock().unwrap().last().unwrap())
            .0
            .iter()
            .map(|m| m["text"].as_str().unwrap().to_string())
            .collect()
    };
    // A conflict, early in the day.
    bead(&mut w, &said("1", 0, "The launch is on Friday."));
    handled(round(&mut w, &transport));
    bead(&mut w, &said("2", 1, "Saturday, says ops."));
    assert_eq!(handled(round(&mut w, &transport)).labels, 1);
    // Then five messages about other things, hours later.
    for n in 0..5u32 {
        bead(
            &mut w,
            &said(
                &format!("later-{n}"),
                300 + n,
                &format!("Other business {n}."),
            ),
        );
        handled(round(&mut w, &transport));
    }
    // The last of them was shown three beads: the conflict, both sides of
    // it, and of the rest only the nearest.
    assert_eq!(
        listed(&requests),
        [
            "The launch is on Friday.",
            "Saturday, says ops.",
            "Other business 3."
        ]
    );
    let (memories, _) = shown(requests.lock().unwrap().last().unwrap());
    assert_eq!(
        (
            memories[0]["current"].as_bool(),
            memories[1]["label"].as_str()
        ),
        (Some(false), Some("conflict"))
    );

    // With no room kept for conflicts, the three nearest in time.
    w.engine = common::engine_with(
        &judge_config(
            "second_read = false\nmax_new_beads = 1\nmax_window_beads = 3\nmax_conflict_beads = 0",
            "",
        ),
        &w.clock,
    );
    bead(&mut w, &said("last", 310, "One more."));
    handled(round(&mut w, &transport));
    assert_eq!(
        listed(&requests),
        [
            "Other business 2.",
            "Other business 3.",
            "Other business 4."
        ]
    );
    // And a limit on the bytes shown beside the new bead: the nearest
    // that fit, and no further.
    w.engine = common::engine_with(
        &judge_config(
            "second_read = false\nmax_new_beads = 1\nmax_conflict_beads = 0\nmax_window_bytes = 40",
            "",
        ),
        &w.clock,
    );
    bead(&mut w, &said("very last", 311, "And another."));
    handled(round(&mut w, &transport));
    assert_eq!(listed(&requests), ["Other business 4.", "One more."]);
    // The receipt counts the content that was shown, the new bead's too.
    assert_eq!(
        w.count("SELECT bytes_sent FROM core_memory.outbound_calls ORDER BY seq DESC LIMIT 1"),
        ("Other business 4.".len() + "One more.".len() + "And another.".len()) as i64
    );
}

#[test]
fn a_bead_waits_for_what_the_judge_is_shown_of_it() {
    // A reader of chunks, a reader of tool calls and a judge.
    let config = format!(
        "{}\n[reader]\nbinding = \"careful\"\n\n[tool_reader]\nbinding = \"careful\"\n",
        judge_config("second_read = false\nmax_wait_for_reads_ms = 60000", "")
    );
    let mut w = World::with_config(&config);
    allow(&w, "acme");
    let judged: Mutex<Vec<ModelRequest>> = Mutex::new(Vec::new());
    let judge = FnTransport(|request: &ModelRequest| {
        judged.lock().unwrap().push(request.clone());
        nothing(request)
    });
    let reads_chunks = FnTransport(|request: &ModelRequest| {
        let user: Value = serde_json::from_str(request.user.lines().next().unwrap()).unwrap();
        let items: Vec<Value> = user["chunks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|chunk| json!({"chunk": chunk["chunk"], "type": "decision"}))
            .collect();
        answer(json!({"items": items}))
    });
    let reads_tools = FnTransport(|_: &ModelRequest| {
        answer(json!({"action": "Ran the tests of the exporter",
                      "outcome": "48 passed and 3 failed", "status": "failed"}))
    });

    // A chunk's bead waits for the chunk to be read, so that the judge is
    // shown its type.
    bead(&mut w, &said("0", 0, "Which cutover do we go with?"));
    bead(&mut w, &said("1", 0, "We go with the phased cutover."));
    assert_eq!(round(&mut w, &judge), JudgeRound::Idle);
    w.engine.read_once(&mut w.store, &reads_chunks).unwrap();
    assert_eq!(handled(round(&mut w, &judge)).judged, 2);
    let (_, new) = shown(judged.lock().unwrap().last().unwrap());
    assert!(
        new.iter().all(|memory| memory["type"] == "decision"),
        "{new:?}"
    );

    // It does not wait for ever: after a minute it is judged without one.
    bead(&mut w, &said("2", 5, "Nobody reads this one in time."));
    w.clock.advance_millis(59_000);
    assert_eq!(round(&mut w, &judge), JudgeRound::Idle);
    w.clock.advance_millis(1_000);
    assert_eq!(handled(round(&mut w, &judge)).judged, 1);
    let (_, new) = shown(judged.lock().unwrap().last().unwrap());
    assert!(new[0].get("type").is_none());

    // A tool call's bead is owed to the judge only once a model has
    // written what the call did. That record is what the judge is shown,
    // and none of the call's own text.
    let (call, chunks) = w.accept(&common::tool_call(
        "toolu_01",
        "Bash",
        json!({"command": "cargo test -p exporter --quiet ZEBRAQUARTZ"}),
        "test result: FAILED. 48 passed; 3 failed ZEBRAQUARTZ",
        Some(true),
    ));
    assert_eq!(w.store.work_counts(Purpose::Judge).unwrap().ready, 0);
    w.clock.advance_millis(120_000);
    assert_eq!(round(&mut w, &judge), JudgeRound::Idle);
    w.engine.read_tool_once(&mut w.store, &reads_tools).unwrap();
    assert_eq!(w.store.work_counts(Purpose::Judge).unwrap().ready, 1);
    let report = handled(round(&mut w, &judge));
    assert_eq!(
        (report.new_beads, report.window_beads, report.verdicts),
        (1, 3, 3)
    );
    let request = judged.lock().unwrap().last().unwrap().clone();
    let (_, new) = shown(&request);
    assert_eq!(
        new[0],
        json!({"id": "N1", "at": new[0]["at"], "at_precision": "arrival", "from": "S1",
               "source": "operational", "by": "A2", "role": "assistant", "tool": "Bash",
               "action": "Ran the tests of the exporter",
               "outcome": "48 passed and 3 failed", "status": "failed"})
    );
    assert!(!request.user.contains("ZEBRAQUARTZ"));
    // The receipt names the call's bead, and no chunk of the call.
    let receipt =
        w.db.connect()
            .query_one(
                "SELECT bead_ids, chunk_ids FROM core_memory.outbound_calls
              WHERE purpose = 'judge' ORDER BY seq DESC LIMIT 1",
                &[],
            )
            .unwrap();
    let (beads, sent): (Vec<uuid::Uuid>, Vec<uuid::Uuid>) = (receipt.get(0), receipt.get(1));
    assert!(beads.contains(&call.0));
    assert!(chunks.iter().all(|chunk| !sent.contains(&chunk.0)));
    // Reading the call again does not have it judged again.
    let now = w.clock.now();
    let reader = w.engine.tool_reader().unwrap().reader_ref.clone();
    let mut other = reader.clone();
    other.binding_rev = core_memory_contract::Sha256Digest::of(b"another reader");
    w.store
        .queue_reads(
            "acme",
            core_memory_store_postgres::Reread {
                chunks: None,
                tool_calls: Some(&other),
            },
            now,
        )
        .unwrap();
    w.engine.read_tool_once(&mut w.store, &reads_tools).unwrap();
    assert_eq!(w.count("SELECT count(*) FROM core_memory.tool_outcomes"), 2);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE kind = 'judge'"),
        0
    );
}

#[test]
fn the_days_budget_stops_the_judge_until_the_next_day() {
    let mut w = World::with_config(&judge_config(
        "second_read = false\nmax_new_beads = 1",
        "[binding.budget]\nmax_requests_per_day = 1\nmax_tokens_per_day = 1000000\n",
    ));
    allow(&w, "acme");
    bead(&mut w, &said("0", 0, "Nothing to be read with yet."));
    bead(&mut w, &said("1", 1, "Judged today."));
    bead(&mut w, &said("2", 2, "Judged tomorrow."));
    let transport = FnTransport(nothing);
    // The first has no other to be read with: no call is made, and none
    // is counted against the budget.
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.calls, report.problem),
        (0, Some("nothing_to_compare"))
    );
    assert_eq!(handled(round(&mut w, &transport)).judged, 1);
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.calls, report.problem), (0, Some("over_budget")));
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
    w.clock.advance_millis(3_600_000);
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);
    // T0 is 07:33 UTC. Past midnight the budget is whole again.
    w.clock.advance_millis(16 * 3_600_000);
    assert_eq!(handled(round(&mut w, &transport)).judged, 1);
    // What a call may cost is counted from what it shows.
    assert!(w.count("SELECT min(reserved_tokens) FROM core_memory.outbound_calls") > 2_000);
}

/// A request for a permit to show `new` to the judge, with the beads near
/// them.
fn permit_request<'a>(
    at: core_memory_contract::Timestamp,
    reader: &'a core_memory_contract::ReaderRef,
    new: &'a [BeadId],
    sending: &'a (dyn Fn(&core_memory_core::store::Shown) -> Option<Sending> + Sync),
) -> JudgePermitRequest<'a> {
    JudgePermitRequest {
        workspace: "w1",
        purpose: Purpose::Judge,
        new,
        window: WindowOf::Near {
            within_ms: 3_600_000,
            max_beads: 10,
            max_bytes: 10_000,
            conflict_beads: 0,
        },
        destination: DESTINATION,
        reader,
        attempt: 1,
        repairs: None,
        sending,
        budget: None,
        at,
    }
}

fn some_cost(_: &core_memory_core::store::Shown) -> Option<Sending> {
    Some(Sending {
        bytes: 10,
        reserved_tokens: 10,
    })
}

#[test]
fn judging_waits_for_a_migration_holding_nothing_else() {
    let mut w = judging("");
    bead(&mut w, &said("1", 0, "One."));
    bead(&mut w, &said("2", 1, "Two."));
    let mut store = w.db.store();
    store.check().unwrap();

    // One whole round: the claim, the permit and the finish each start a
    // transaction of their own, and the first of them waits.
    let transport = FnTransport(nothing);
    {
        let (db, engine) = (&w.db, &w.engine);
        let report = behind_a_migration(db, "judge_once", || {
            engine.judge_once(&mut store, &transport)
        });
        assert_eq!(handled(report.unwrap()).judged, 2);
    }

    // Then each step by itself.
    let third = bead(&mut w, &said("3", 2, "Three."));
    let db = &w.db;
    let wanted = claim(&w, 4);
    let claimed = behind_a_migration(db, "claim_judgment", || store.claim_judgment(&wanted));
    let batch = claimed.unwrap().unwrap();
    assert!(matches!(&batch.work, JudgeWork::Window(beads) if beads[0].bead_id == third));
    let reader = w.engine.judge().unwrap().reader_ref.clone();
    let new = [third];
    let request = permit_request(w.clock.now(), &reader, &new, &some_cost);
    let permit = behind_a_migration(db, "permit_judgment", || store.permit_judgment(&request));
    let JudgePermit::Granted { call_id, shown, .. } = permit.unwrap() else {
        panic!("not permitted");
    };
    assert_eq!((shown.window.len(), shown.new.len()), (2, 1));
    let finish = core_memory_core::store::JudgmentFinish {
        call_id,
        workspace: "w1",
        lease: wanted.lease_until,
        result: core_memory_core::store::CallResult::Rejected,
        usage: None,
        judgment: None,
        settles: None,
        reader: &reader,
        work: &[],
        at: w.clock.now(),
    };
    let finished = behind_a_migration(db, "finish_judgment", || store.finish_judgment(&finish));
    assert_eq!(finished.unwrap().verdicts, 0);
    let now = w.clock.now();
    let queued = behind_a_migration(db, "queue_judgments", || {
        store.queue_judgments("acme", None, now)
    });
    assert_eq!(
        queued.unwrap(),
        QueuedReads::Queued {
            chunks: 0,
            tool_calls: 0
        }
    );
    // An export of what was judged waits the same way.
    let exported = behind_a_migration(db, "export", || store.export("w1", 0, 100, Some("acme")));
    assert!(
        exported
            .unwrap()
            .iter()
            .any(|change| kind_of(change) == "verdict")
    );
}

#[test]
fn a_permit_decides_bead_by_bead_and_says_what_it_withheld() {
    let mut w = judging("");
    w.store.enroll_scope("beta", "w1").unwrap();
    w.store.enroll_scope("gamma", "w1").unwrap();
    allow(&w, "gamma");
    w.store.create_workspace("w2").unwrap();
    w.store.enroll_scope("elsewhere", "w2").unwrap();
    allow(&w, "elsewhere");
    let ours = bead(&mut w, &said("1", 0, "Ours."));
    let also_ours = bead(&mut w, &said("3", 5, "Also ours."));
    let unconsenting = bead(&mut w, &said_in("beta", "1", 1, "No consent."));
    let paused = bead(&mut w, &said_in("gamma", "1", 2, "Paused."));
    let (erased_event, erased) = message(&mut w, &said("2", 3, "Erased."));
    let foreign = bead(&mut w, &said_in("elsewhere", "1", 4, "Another workspace."));
    w.store
        .set_scope_state("gamma", core_memory_core::store::ScopeState::Paused)
        .unwrap();
    erase(&w, erased_event);
    let reader = w.engine.judge().unwrap().reader_ref.clone();
    let withheld_ones = [
        (unconsenting, Withheld::NoConsent),
        (paused, Withheld::ScopeNotActive),
        // Nothing is left of an erased bead, and a bead of another
        // workspace is no bead of this one.
        (erased, Withheld::NothingToShow),
        (foreign, Withheld::NothingToShow),
    ];

    let asked = [ours, unconsenting, paused, erased, foreign, ours, also_ours];
    let request = permit_request(w.clock.now(), &reader, &asked, &some_cost);
    let JudgePermit::Granted {
        shown, withheld, ..
    } = w.store.permit_judgment(&request).unwrap()
    else {
        panic!("not permitted");
    };
    assert_eq!(
        shown.new.iter().map(|b| b.bead_id).collect::<Vec<_>>(),
        [ours, also_ours]
    );
    assert!(shown.window.is_empty());
    assert_eq!(withheld, withheld_ones);
    // One that may be shown, with nothing beside it, is no pair to read.
    // No receipt is written.
    let asked = [ours, unconsenting, paused, erased, foreign];
    let request = permit_request(w.clock.now(), &reader, &asked, &some_cost);
    assert_eq!(
        w.store.permit_judgment(&request).unwrap(),
        JudgePermit::NothingToCompare(withheld_ones.to_vec())
    );
    // With none that may be shown there is no permit and no receipt.
    let asked = [unconsenting, paused];
    let request = permit_request(w.clock.now(), &reader, &asked, &some_cost);
    let refused = w.store.permit_judgment(&request).unwrap();
    assert_eq!(
        refused,
        JudgePermit::NothingToJudge(vec![
            (unconsenting, Withheld::NoConsent),
            (paused, Withheld::ScopeNotActive)
        ])
    );
    // Nor when what would be shown cannot be made into a request.
    let asked = [ours, also_ours];
    let request = permit_request(w.clock.now(), &reader, &asked, &|_| None);
    assert_eq!(
        w.store.permit_judgment(&request).unwrap(),
        JudgePermit::Unsendable
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
    // The beads a caller names to be shown beside the new one are shown
    // all together or not at all, and the answer says which may not be.
    let asked = [ours];
    let these = [also_ours, unconsenting, paused, erased, foreign];
    let mut request = permit_request(w.clock.now(), &reader, &asked, &some_cost);
    request.window = WindowOf::These(&these);
    assert_eq!(
        w.store.permit_judgment(&request).unwrap(),
        JudgePermit::NothingToJudge(withheld_ones.to_vec())
    );
    let these = [also_ours];
    let mut request = permit_request(w.clock.now(), &reader, &asked, &some_cost);
    request.window = WindowOf::These(&these);
    let JudgePermit::Granted { shown, .. } = w.store.permit_judgment(&request).unwrap() else {
        panic!("not permitted");
    };
    assert_eq!(
        shown.window.iter().map(|b| b.bead_id).collect::<Vec<_>>(),
        [also_ours]
    );
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        2
    );
}

#[test]
fn the_end_of_a_judges_call_is_recorded_once() {
    let mut w = judging("");
    bead(&mut w, &said("1", 0, "One."));
    bead(&mut w, &said("2", 1, "Two."));
    let wanted = claim(&w, 4);
    let batch = w.store.claim_judgment(&wanted).unwrap().unwrap();
    let JudgeWork::Window(leased) = &batch.work else {
        panic!("not a window read");
    };
    let new: Vec<BeadId> = leased.iter().map(|bead| bead.bead_id).collect();
    let reader = w.engine.judge().unwrap().reader_ref.clone();
    let request = permit_request(w.clock.now(), &reader, &new, &some_cost);
    let JudgePermit::Granted { call_id, shown, .. } = w.store.permit_judgment(&request).unwrap()
    else {
        panic!("not permitted");
    };
    let judged = core_memory_core::judge::validate(
        r#"{"new": [{"id": "N1", "related": [], "unsure": []},
                    {"id": "N2", "related": [{"to": "N1", "rel": "NEAR"}], "unsure": []}],
            "names": []}"#,
        &shown,
        core_memory_core::judge::Names::MustBeFound,
    )
    .unwrap();
    let write = core_memory_core::judge::judgment(
        &shown,
        &judged,
        core_memory_core::judge::Recording {
            id_key: &batch.id_key,
            hold_labels: false,
        },
    );
    let finish = core_memory_core::store::JudgmentFinish {
        call_id,
        workspace: "w1",
        lease: wanted.lease_until,
        result: core_memory_core::store::CallResult::Accepted,
        // A provider's count of its own tokens is a number from outside.
        usage: Some(Usage {
            input_tokens: u64::MAX,
            output_tokens: 3,
        }),
        judgment: Some(&write),
        settles: None,
        reader: &reader,
        work: &[],
        at: w.clock.now(),
    };
    // A caller whose answer was lost on the way back asks again. Nothing
    // is stored twice, and it is told what was stored.
    for _ in 0..2 {
        let finished = w.store.finish_judgment(&finish).unwrap();
        assert_eq!(
            (finished.judged, finished.verdicts, finished.edges),
            (2, 1, 1)
        );
    }
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.edges"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.judge_reads"), 1);
    assert_eq!(
        w.count("SELECT input_tokens FROM core_memory.outbound_results"),
        i64::MAX
    );
    // A judgment offered with a call that is no judge's call of this
    // workspace stores nothing.
    let mut stray = finish.clone();
    stray.workspace = "w9";
    assert!(w.store.finish_judgment(&stray).is_err());
    let mut unknown = finish.clone();
    unknown.call_id = "00000000-0000-4000-8000-000000000001".parse().unwrap();
    assert_eq!(w.store.finish_judgment(&unknown).unwrap().verdicts, 0);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 1);
}

#[test]
fn what_a_judge_wrote_is_exported_and_one_scope_gets_only_what_lies_inside_it() {
    let mut w = judging("");
    w.store.enroll_scope("beta", "w1").unwrap();
    allow(&w, "beta");
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 5, "The launch moves to Friday."));
    bead(
        &mut w,
        &said_in("beta", "1", 9, "Marketing booked Thursday."),
    );
    let transport = FnTransport(|request: &ModelRequest| {
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "unsure": [], "related": [
                {"to": "N1", "rel": "SUPERSEDES<", "label": "supersede"}]},
            {"id": "N3", "unsure": ["N2"], "related": [{"to": "N1", "rel": "LEADSTO>"}]},
        ], "names": [{"a": "N2", "a_name": "moves", "b": "N1", "b_name": "is on"}]}))
        .inspect(|_| assert!(request.user.contains(r#""from":"S2""#)))
    });
    handled(round(&mut w, &transport));

    let kinds = |changes: &[Change]| changes.iter().map(kind_of).collect::<Vec<_>>();
    let all = w.changes();
    assert_eq!(
        kinds(&all),
        [
            "event",
            "event",
            "event",
            "verdict",
            "verdict",
            "verdict",
            "bead",
            "edge",
            "edge",
            "name link"
        ]
    );
    // Asked for one scope, a consumer gets what lies wholly inside it: not
    // a verdict, a relationship or a link that names a bead of another
    // scope, which would tell it that the other exists.
    let acme = w.store.export("w1", 0, 100, Some("acme")).unwrap();
    assert_eq!(
        kinds(&acme),
        ["event", "event", "verdict", "bead", "edge", "name link"]
    );
    let beta = w.store.export("w1", 0, 100, Some("beta")).unwrap();
    assert_eq!(kinds(&beta), ["event"]);
    // Paging by the change number misses nothing and repeats nothing.
    let mut paged = Vec::new();
    let mut after = 0;
    loop {
        let page = w.store.export("w1", after, 3, None).unwrap();
        let Some(last) = page.last() else { break };
        after = last.seq();
        paged.extend(page);
    }
    assert_eq!(paged, all);

    // The records as a consumer reads them. None holds a word of text.
    let records: Vec<Value> = all
        .into_iter()
        .filter_map(|change| {
            Some(match change {
                Change::Bead(bead) => core_memory_contract::Record::bead(*bead),
                Change::Edge(edge) => core_memory_contract::Record::edge(*edge),
                Change::Verdict(verdict) => core_memory_contract::Record::verdict(*verdict),
                Change::NameLink(link) => core_memory_contract::Record::name_link(*link),
                _ => return None,
            })
        })
        .map(|record| serde_json::to_value(record).unwrap())
        .collect();
    for record in &records {
        assert!(record.get("scope").is_none(), "{record}");
        let line = record.to_string();
        for word in ["launch", "Thursday", "Marketing"] {
            assert!(!line.contains(word), "{line}");
        }
    }
    let verdict = &records[0];
    assert_eq!(
        (
            &verdict["record_kind"],
            &verdict["verdict"],
            &verdict["direction"],
            &verdict["label"],
            &verdict["read_no"],
            &verdict["judge"]["binding_id"],
            &verdict["judge"]["rubric_rev"],
        ),
        (
            &json!("verdict"),
            &json!("supersedes"),
            &json!("later_to_earlier"),
            &json!("supersede"),
            &json!(1),
            &json!("careful"),
            &json!(core_memory_core::judge::RUBRIC_REV),
        )
    );
    assert_eq!(
        verdict["columns"],
        json!({
            "gap_ms": 300_000, "same_scope": true, "same_event": false, "same_thread": false,
            "same_session": false, "same_actor": true, "replies": false,
            "shared_identifiers": 0, "shared_urls": 0,
            "earlier_kind": "chunk", "later_kind": "chunk",
            "earlier_is_head": true, "later_is_head": true,
            "earlier_is_new": true, "later_is_new": true,
        })
    );
    assert_eq!(
        (
            &records[3]["record_kind"],
            &records[3]["bead_kind"],
            &records[3]["label"]
        ),
        (&json!("bead"), &json!("label"), &json!("supersede"))
    );
    assert_eq!(
        (
            &records[4]["record_kind"],
            &records[4]["edge_type"],
            &records[4]["from_bead"]
        ),
        (&json!("edge"), &json!("supersedes"), &records[3]["bead_id"])
    );
    assert_eq!(records[5]["edge_type"], "leadsto");
    // "is on" in the first message and "moves" in the second, in the
    // order of their beads' IDs.
    let link = &records[6];
    assert_eq!(link["record_kind"], "name_link");
    let mut ends = [
        (&link["a_start"], &link["a_end"]),
        (&link["b_start"], &link["b_end"]),
    ];
    ends.sort_by_key(|(start, _)| start.as_u64());
    assert_eq!(ends, [(&json!(11), &json!(16)), (&json!(11), &json!(16))]);
    assert!(link["a_bead"].as_str() < link["b_bead"].as_str());
}

#[test]
fn beads_stored_before_there_was_a_judge_are_queued_when_asked() {
    // Admitted by an engine that does not judge.
    let mut w = World::new();
    bead(&mut w, &said("1", 0, "Before there was a judge."));
    bead(&mut w, &said("2", 1, "Also before."));
    w.accept(&common::tool_call(
        "toolu_01",
        "Bash",
        json!({"command": "ls"}),
        "a\nb",
        None,
    ));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    w.engine = common::engine_with(&judge_config("second_read = false", ""), &w.clock);
    allow(&w, "acme");
    let transport = FnTransport(nothing);
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);

    // Asked to, the store queues them: the chunks' beads, and not the tool
    // call's, which no model has written about.
    let now = w.clock.now();
    let queued = QueuedReads::Queued {
        chunks: 2,
        tool_calls: 0,
    };
    assert_eq!(w.store.queue_judgments("acme", None, now).unwrap(), queued);
    let none = QueuedReads::Queued {
        chunks: 0,
        tool_calls: 0,
    };
    assert_eq!(w.store.queue_judgments("acme", None, now).unwrap(), none);
    assert_eq!(handled(round(&mut w, &transport)).judged, 2);
    // What a judge has read is not queued again,
    assert_eq!(w.store.queue_judgments("acme", None, now).unwrap(), none);
    // unless it is another judge that asks.
    let this = w.engine.judge().unwrap().reader_ref.clone();
    assert_eq!(
        w.store.queue_judgments("acme", Some(&this), now).unwrap(),
        none
    );
    let mut other = this.clone();
    other.binding_rev = core_memory_contract::Sha256Digest::of(b"another judge");
    assert_eq!(
        w.store.queue_judgments("acme", Some(&other), now).unwrap(),
        queued
    );
    // Nothing is queued for a scope that is not active.
    w.store
        .set_scope_state("acme", core_memory_core::store::ScopeState::Paused)
        .unwrap();
    assert_eq!(
        w.store.queue_judgments("acme", None, now).unwrap(),
        QueuedReads::ScopeNotActive(Some(core_memory_core::store::ScopeState::Paused))
    );
    assert_eq!(
        w.store.queue_judgments("nowhere", None, now).unwrap(),
        QueuedReads::ScopeNotActive(None)
    );
}

#[test]
fn the_engine_role_judges_and_cannot_rewrite_what_was_judged() {
    let w = judging("second_read = true");
    let mut admin = w.db.connect();
    admin
        .batch_execute(
            "DROP ROLE IF EXISTS cm_t_judge_engine;
             CREATE ROLE cm_t_judge_engine LOGIN PASSWORD 'synthetic';
             GRANT core_memory_engine TO cm_t_judge_engine;",
        )
        .unwrap();
    let (scheme, rest) = w.db.url.split_once("://").unwrap();
    let (_, host) = rest.split_once('@').unwrap();
    let url = format!("{scheme}://cm_t_judge_engine:synthetic@{host}");
    // Whole rounds with the engine's privileges and nothing more: beads
    // are admitted and judged, a label is held, read again and written,
    // and a group and a name link are made.
    let mut store = PostgresStore::new(&ConnectConfig::new(url.clone())).unwrap();
    for (id, minutes, text) in [
        ("1", 0, "The launch is on Thursday."),
        ("2", 5, "The launch moves to Friday."),
        ("3", 6, "More on why it moves to Friday."),
    ] {
        let admitted = w
            .engine
            .admit(
                &mut store,
                &core_memory_core::ScopeGrants::All,
                &said(id, minutes, text),
            )
            .unwrap();
        assert!(
            matches!(
                admitted,
                core_memory_contract::AdmissionOutcome::Accepted { .. }
            ),
            "{admitted:?}"
        );
    }
    let transport = FnTransport(|request: &ModelRequest| {
        let (listed, new) = shown(request);
        if new.len() == 1 {
            // The second read of the held label.
            return answer(json!({"new": [{"id": "N1", "unsure": [], "related": [
                {"to": listed[0]["id"], "rel": "SUPERSEDES<", "label": "supersede"}]}],
                "names": []}));
        }
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "unsure": [], "related": [
                {"to": "N1", "rel": "SUPERSEDES<", "label": "supersede"}]},
            {"id": "N3", "unsure": ["N1"], "related": [{"to": "N2", "rel": "CONTAINS"}]},
        ], "names": [{"a": "N3", "a_name": "More", "b": "N2", "b_name": "launch"}]}))
    });
    let first = handled(w.engine.judge_once(&mut store, &transport).unwrap());
    assert_eq!((first.held, first.groups, first.names), (1, 1, 1));
    let second = handled(w.engine.judge_once(&mut store, &transport).unwrap());
    assert_eq!((second.second_read, second.labels), (true, 1));

    let mut raw = postgres::Client::connect(&url, postgres::NoTls).unwrap();
    let refused = |client: &mut postgres::Client, sql: &str| -> String {
        match client.batch_execute(sql) {
            Ok(()) => panic!("`{sql}` was allowed"),
            Err(e) => e
                .as_db_error()
                .map_or(e.to_string(), |d| d.message().to_string()),
        }
    };
    for sql in [
        "UPDATE core_memory.verdicts SET verdict = 'none'",
        "DELETE FROM core_memory.verdicts",
        "UPDATE core_memory.edges SET edge_type = 'near'",
        "DELETE FROM core_memory.edges",
        "DELETE FROM core_memory.labels",
        "UPDATE core_memory.labels SET label = 'retract'",
        "DELETE FROM core_memory.name_links",
        "DELETE FROM core_memory.judge_reads",
        "UPDATE core_memory.pending_labels SET label = 'retract'",
        "UPDATE core_memory.beads SET state = 'erased' WHERE kind = 'label'",
        "SELECT core_memory.lock_workspace('w1')",
        "SELECT core_memory.take_seq('w1', 1)",
    ] {
        let message = refused(&mut raw, sql);
        assert!(message.contains("permission denied"), "{sql}: {message}");
    }
    // Even the owner cannot change what a judge wrote, or delete it while
    // the beads it is about are there.
    for (sql, why) in [
        (
            "UPDATE core_memory.verdicts SET verdict = 'none'",
            "cannot be changed",
        ),
        ("DELETE FROM core_memory.verdicts", "cannot be deleted"),
        ("TRUNCATE core_memory.verdicts", "cannot be truncated"),
        ("DELETE FROM core_memory.edges", "cannot be deleted"),
        (
            "UPDATE core_memory.edges SET to_bead = from_bead",
            "cannot be changed",
        ),
        ("DELETE FROM core_memory.labels", "cannot be deleted"),
        ("DELETE FROM core_memory.name_links", "cannot be deleted"),
        (
            "UPDATE core_memory.judge_reads SET read_no = 2",
            "cannot be changed",
        ),
    ] {
        let message = refused(&mut admin, sql);
        assert!(message.contains(why), "{sql}: {message}");
    }
    // The graph keeps its shape whoever writes to it.
    for (sql, why) in [
        (
            "INSERT INTO core_memory.edges (workspace_id, seq, edge_type, from_bead, to_bead,
                                            call_id, created_at)
             SELECT e.workspace_id, 900001, 'supersedes', e.to_bead, e.from_bead, e.call_id,
                    e.created_at
               FROM core_memory.edges AS e WHERE e.edge_type = 'supersedes'",
            "runs from a label bead",
        ),
        (
            "INSERT INTO core_memory.edges (workspace_id, seq, edge_type, from_bead, to_bead,
                                            call_id, created_at)
             SELECT e.workspace_id, 900002, 'supersedes', e.from_bead, e.to_bead, e.call_id,
                    e.created_at
               FROM core_memory.edges AS e WHERE e.edge_type = 'supersedes'",
            "duplicate key",
        ),
        (
            "INSERT INTO core_memory.edges (workspace_id, seq, edge_type, from_bead, to_bead,
                                            call_id, created_at)
             SELECT e.workspace_id, 900003, 'leadsto', e.from_bead, e.to_bead, e.call_id,
                    e.created_at
               FROM core_memory.edges AS e WHERE e.edge_type = 'contains' LIMIT 1",
            "joins two beads of source content",
        ),
    ] {
        let message = refused(&mut admin, sql);
        assert!(message.contains(why), "{sql}: {message}");
    }
    drop(raw);
    drop(store);
    admin
        .batch_execute("DROP OWNED BY cm_t_judge_engine; DROP ROLE cm_t_judge_engine")
        .unwrap();
}

#[test]
fn nothing_is_stored_about_a_bead_that_was_erased_whoever_writes_it() {
    let mut w = judging("");
    let (gone, _) = message(&mut w, &said("1", 0, "Erased after it was judged."));
    bead(&mut w, &said("2", 1, "Kept."));
    bead(&mut w, &said("3", 2, "Kept as well."));
    let transport = FnTransport(|_: &ModelRequest| {
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "related": [{"to": "N1", "rel": "NEAR"}], "unsure": []},
            {"id": "N3", "related": [{"to": "N2", "rel": "LEADSTO>"}], "unsure": []},
        ], "names": [{"a": "N2", "a_name": "Kept", "b": "N1", "b_name": "Erased"},
                     {"a": "N3", "a_name": "as well", "b": "N2", "b_name": "Kept"}]}))
    });
    handled(round(&mut w, &transport));
    erase(&w, gone);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.name_links"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.edges"), 2);

    // The engine checks this where it stores a judgment. The tables check
    // it too, for a statement that does not: even their owner cannot add
    // a row about an erased bead, which no erase would reach again.
    let mut admin = w.db.connect();
    for (table, sql) in [
        (
            "verdicts",
            "INSERT INTO core_memory.verdicts (workspace_id, seq, earlier_bead, later_bead,
                 verdict, direction, call_id, columns, columns_rev)
             SELECT v.workspace_id, 900001, b.bead_id, v.later_bead, 'none', 'undirected',
                    v.call_id, v.columns, 1
               FROM core_memory.verdicts AS v, core_memory.beads AS b
              WHERE b.state = 'erased'",
        ),
        (
            "name_links",
            "INSERT INTO core_memory.name_links (workspace_id, seq, a_bead, a_start, a_end,
                 b_bead, b_start, b_end, call_id, linked_at)
             SELECT n.workspace_id, 900002, n.a_bead, 0, 1, b.bead_id, 0, 1, n.call_id,
                    n.linked_at
               FROM core_memory.name_links AS n, core_memory.beads AS b
              WHERE b.state = 'erased'",
        ),
        (
            "edges",
            "INSERT INTO core_memory.edges (workspace_id, seq, edge_type, from_bead, to_bead,
                 call_id, created_at)
             SELECT e.workspace_id, 900003, 'express', e.from_bead, b.bead_id, e.call_id,
                    e.created_at
               FROM core_memory.edges AS e, core_memory.beads AS b
              WHERE e.edge_type = 'leadsto' AND b.state = 'erased'",
        ),
    ] {
        let refused = admin.batch_execute(sql).unwrap_err();
        let message = refused.as_db_error().unwrap().message().to_string();
        assert!(
            message.contains(&format!(
                "core_memory.{table} takes rows only about beads that are live"
            )),
            "{table}: {message}"
        );
    }
}

#[test]
fn what_another_judge_says_again_is_stored_once() {
    let mut w = judging("");
    bead(&mut w, &said("1", 0, "Ask Priya Raman about the export."));
    bead(&mut w, &said("2", 1, "PR says the export takes an hour."));
    bead(&mut w, &said("3", 2, "More from PR on the export."));
    bead(
        &mut w,
        &said("4", 3, "The export takes two hours, not one."),
    );
    let transport = FnTransport(|request: &ModelRequest| {
        // A judge is told which memories are no longer current, and may
        // not say again that one of them is superseded.
        let (_, new) = shown(request);
        let superseded = if new[1]["current"] == false {
            json!([])
        } else {
            json!([{"to": "N2", "rel": "SUPERSEDES<", "label": "supersede"}])
        };
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "related": [{"to": "N1", "rel": "LEADSTO>"}], "unsure": []},
            {"id": "N3", "unsure": [], "related": [
                {"to": "N2", "rel": "CONTAINS"}, {"to": "N1", "rel": "NEAR"}]},
            {"id": "N4", "unsure": [], "related": superseded},
        ], "names": [{"a": "N2", "a_name": "PR", "b": "N1", "b_name": "Priya Raman"}]}))
    });
    let first = handled(round(&mut w, &transport));
    assert_eq!(
        (
            first.verdicts,
            first.edges,
            first.labels,
            first.groups,
            first.names
        ),
        (6, 2, 1, 1, 1)
    );

    // The same beads, read again by a judge with another model.
    let other = judge_config("second_read = false", "").replace("stand-in-1", "stand-in-2");
    w.engine = common::engine_with(&other, &w.clock);
    let judge = w.engine.judge().unwrap().reader_ref.clone();
    let now = w.clock.now();
    assert_eq!(
        w.store.queue_judgments("acme", Some(&judge), now).unwrap(),
        QueuedReads::Queued {
            chunks: 4,
            tool_calls: 0
        }
    );
    let second = handled(round(&mut w, &transport));
    // Each of its verdicts is a row of its own, under its own name. The
    // relationships, the group and the name link it gave again are the
    // ones that were there.
    assert_eq!(second.problem, None);
    assert_eq!(
        (
            second.verdicts,
            second.edges,
            second.labels,
            second.groups,
            second.names
        ),
        (6, 0, 0, 0, 0)
    );
    assert_eq!(
        rows(
            &w,
            "SELECT count(*)::text FROM core_memory.verdicts AS v
               JOIN core_memory.judge_reads AS r ON r.call_id = v.call_id
              GROUP BY r.binding_rev ORDER BY min(v.seq)"
        ),
        [row(&["6"]), row(&["6"])]
    );
    // Of the pair one judge had superseded, the other was shown that the
    // earlier statement is no longer current, and its verdict says so.
    assert_eq!(
        rows(
            &w,
            "SELECT v.verdict, v.columns ->> 'earlier_is_head' FROM core_memory.verdicts AS v
               JOIN core_memory.chunk_text AS a ON a.chunk_id = v.earlier_bead
               JOIN core_memory.chunk_text AS b ON b.chunk_id = v.later_bead
              WHERE a.text LIKE 'PR says%' AND b.text LIKE 'The export takes%'
              ORDER BY v.seq"
        ),
        [row(&["supersedes", "true"]), row(&["none", "false"])]
    );
    for (what, count) in [
        ("verdicts", 12),
        ("edges WHERE edge_type IN ('leadsto', 'near')", 2),
        ("edges WHERE edge_type = 'supersedes'", 1),
        ("edges WHERE edge_type = 'contains'", 2),
        ("beads WHERE kind = 'label'", 1),
        ("beads WHERE kind = 'group'", 1),
        ("labels", 1),
        ("name_links", 1),
        ("judge_reads", 2),
    ] {
        assert_eq!(
            w.count(&format!("SELECT count(*) FROM core_memory.{what}")),
            count,
            "{what}"
        );
    }
    // Nothing is left to judge, and the first judge's own beads are not
    // queued for it again.
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);
    assert_eq!(
        w.store.queue_judgments("acme", Some(&judge), now).unwrap(),
        QueuedReads::Queued {
            chunks: 0,
            tool_calls: 0
        }
    );
}

#[test]
fn a_verdict_and_a_name_link_carry_the_scopes_of_their_beads() {
    let mut w = judging("");
    w.store.enroll_scope("beta", "w1").unwrap();
    allow(&w, "beta");
    bead(&mut w, &said("1", 0, "Ask Priya Raman about the export."));
    bead(
        &mut w,
        &said_in("beta", "1", 1, "PR says the export takes an hour."),
    );
    bead(&mut w, &said("2", 2, "Thanks."));
    let transport = FnTransport(|_: &ModelRequest| {
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "related": [{"to": "N1", "rel": "LEADSTO>"}], "unsure": []},
            {"id": "N3", "related": [], "unsure": []},
        ], "names": [{"a": "N2", "a_name": "PR", "b": "N1", "b_name": "Priya Raman"}]}))
    });
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.verdicts, report.names), (3, 1));

    // A judge reads across the scopes of a workspace. Each row says which
    // scopes its two beads belong to.
    assert_eq!(
        rows(
            &w,
            "SELECT a.text, v.earlier_scope, b.text, v.later_scope
               FROM core_memory.verdicts AS v
               JOIN core_memory.chunk_text AS a ON a.chunk_id = v.earlier_bead
               JOIN core_memory.chunk_text AS b ON b.chunk_id = v.later_bead
              ORDER BY v.seq"
        ),
        [
            row(&[
                "Ask Priya Raman about the export.",
                "acme",
                "PR says the export takes an hour.",
                "beta"
            ]),
            row(&[
                "Ask Priya Raman about the export.",
                "acme",
                "Thanks.",
                "acme"
            ]),
            row(&[
                "PR says the export takes an hour.",
                "beta",
                "Thanks.",
                "acme"
            ]),
        ]
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.name_links AS n
               JOIN core_memory.beads AS a ON a.bead_id = n.a_bead
               JOIN core_memory.beads AS b ON b.bead_id = n.b_bead
              WHERE a.scope = n.a_scope AND b.scope = n.b_scope AND n.a_scope <> n.b_scope"
        ),
        1
    );
    // The exported records say the same.
    let exported: Vec<(String, String)> = w
        .changes()
        .into_iter()
        .filter_map(|change| match change {
            Change::Verdict(verdict) => Some((verdict.earlier_scope, verdict.later_scope)),
            _ => None,
        })
        .collect();
    let pair = |a: &str, b: &str| (a.to_string(), b.to_string());
    assert_eq!(
        exported,
        [
            pair("acme", "beta"),
            pair("acme", "acme"),
            pair("beta", "acme")
        ]
    );

    // So a host can let a role that reads one scope read what was judged
    // inside it, with a policy that looks at the row and nothing else.
    let mut admin = w.db.connect();
    admin
        .batch_execute(
            "DROP ROLE IF EXISTS cm_t_verdict_reader;
             CREATE ROLE cm_t_verdict_reader;
             GRANT USAGE ON SCHEMA core_memory TO cm_t_verdict_reader;
             GRANT SELECT ON core_memory.verdicts, core_memory.name_links
                TO cm_t_verdict_reader;
             CREATE POLICY host_reads ON core_memory.verdicts FOR SELECT TO cm_t_verdict_reader
                 USING (earlier_scope = 'acme' AND later_scope = 'acme');
             CREATE POLICY host_reads ON core_memory.name_links FOR SELECT
                 TO cm_t_verdict_reader USING (a_scope = 'acme' AND b_scope = 'acme');
             SET ROLE cm_t_verdict_reader;",
        )
        .unwrap();
    let seen: i64 = admin
        .query_one("SELECT count(*) FROM core_memory.verdicts", &[])
        .unwrap()
        .get(0);
    let links: i64 = admin
        .query_one("SELECT count(*) FROM core_memory.name_links", &[])
        .unwrap()
        .get(0);
    assert_eq!((seen, links), (1, 0));
    admin.batch_execute("RESET ROLE").unwrap();

    // The scopes are read from the beads, whatever a statement gives, so a
    // row cannot say another scope than its beads have.
    admin
        .batch_execute(
            "INSERT INTO core_memory.verdicts (workspace_id, seq, earlier_bead, later_bead,
                 earlier_scope, later_scope, verdict, direction, call_id, columns, columns_rev)
             SELECT v.workspace_id, 900001, v.earlier_bead, v.later_bead, 'acme', 'acme',
                    'none', 'undirected', v.call_id, v.columns, 1
               FROM core_memory.verdicts AS v
              WHERE v.earlier_scope = 'acme' AND v.later_scope = 'beta'",
        )
        .unwrap();
    assert_eq!(
        rows(
            &w,
            "SELECT earlier_scope, later_scope FROM core_memory.verdicts WHERE seq = 900001"
        ),
        [row(&["acme", "beta"])]
    );
    // A bead a judge made belongs to no scope, and nothing is judged of it.
    let refused = admin
        .batch_execute(
            "INSERT INTO core_memory.beads (bead_id, workspace_id, scope, kind, state, event_id,
                 chunk_id, created_by, call_id, time_at, time_basis, created_at, seq)
             SELECT gen_random_uuid(), r.workspace_id, NULL, 'group', 'live', NULL, NULL,
                    'judge', r.call_id, r.judged_at, 'captured', r.judged_at, 900002
               FROM core_memory.judge_reads AS r;
             INSERT INTO core_memory.verdicts (workspace_id, seq, earlier_bead, later_bead,
                 verdict, direction, call_id, columns, columns_rev)
             SELECT v.workspace_id, 900003, g.bead_id, v.later_bead, 'none', 'undirected',
                    v.call_id, v.columns, 1
               FROM core_memory.verdicts AS v, core_memory.beads AS g
              WHERE v.seq = 900001 AND g.kind = 'group'",
        )
        .unwrap_err();
    let message = refused.as_db_error().unwrap().message().to_string();
    assert!(message.contains("earlier_scope"), "{message}");
    admin
        .batch_execute(
            "DROP POLICY host_reads ON core_memory.verdicts;
             DROP POLICY host_reads ON core_memory.name_links;
             DROP OWNED BY cm_t_verdict_reader;
             DROP ROLE cm_t_verdict_reader;",
        )
        .unwrap();
}

#[test]
fn the_planner_keeps_no_sample_of_what_was_computed_from_two_beads() {
    let mut w = judging("");
    for n in 0..24 {
        bead(
            &mut w,
            &said(
                &format!("{n}"),
                n,
                &format!("OPS-{n} is remark number {n}."),
            ),
        );
    }
    let transport = FnTransport(nothing);
    while let JudgeRound::Handled(report) = round(&mut w, &transport) {
        assert_eq!(report.problem, None);
    }
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 276);
    w.db.connect()
        .batch_execute("ANALYZE core_memory.verdicts")
        .unwrap();
    let sampled = |columns: &str| {
        w.count(&format!(
            "SELECT count(*) FROM pg_stats
              WHERE schemaname = 'core_memory' AND tablename = 'verdicts'
                AND attname IN ({columns})"
        ))
    };
    assert!(
        sampled("'seq', 'verdict', 'earlier_scope', 'columns_rev'") >= 4,
        "ANALYZE collected statistics"
    );
    // What code computed about a pair says what both beads hold: how many
    // identifiers they share, for one.
    assert_eq!(sampled("'columns'"), 0);
}

// ---- what a review of the judge found ----

#[test]
fn a_reply_that_comes_after_its_lease_ran_out_stores_nothing_for_work_another_worker_holds() {
    use core_memory_core::judge::{Names, Recording, judgment, validate};
    use core_memory_core::store::{CallResult, JudgmentFinish};

    let mut w = judging("");
    let reader = w.engine.judge().unwrap().reader_ref.clone();
    // A bead from before, with nothing to be read with: settled without a
    // call, so that each read below has a pair.
    bead(&mut w, &said("w", 0, "Said first."));
    handled(round(&mut w, &FnTransport(nothing)));
    // X is claimed by worker A, which is permitted to show it.
    let x = bead(&mut w, &said("x", 10, "X was said at ten past."));
    let a_claim = claim(&w, 4);
    let a_batch = w.store.claim_judgment(&a_claim).unwrap().unwrap();
    let a_new = [x];
    let request = permit_request(w.clock.now(), &reader, &a_new, &some_cost);
    let JudgePermit::Granted {
        call_id: a_call,
        shown: a_shown,
        ..
    } = w.store.permit_judgment(&request).unwrap()
    else {
        panic!("A was not permitted");
    };
    // Z was written before X and arrives after it. A's lease runs out and
    // worker B claims what is due: X again, and Z.
    let z = bead(&mut w, &said("z", 5, "Z was said at five past."));
    w.clock.advance_millis(60_001);
    let mut b_store = w.db.store();
    let b_claim = claim(&w, 4);
    let b_batch = b_store.claim_judgment(&b_claim).unwrap().unwrap();
    let JudgeWork::Window(b_leased) = &b_batch.work else {
        panic!("not a window read");
    };
    let b_new: Vec<BeadId> = b_leased.iter().map(|leased| leased.bead_id).collect();
    let request = permit_request(w.clock.now(), &reader, &b_new, &some_cost);
    let JudgePermit::Granted {
        call_id: b_call,
        shown: b_shown,
        ..
    } = b_store.permit_judgment(&request).unwrap()
    else {
        panic!("B was not permitted");
    };
    assert_eq!(
        b_shown.new.iter().map(|b| b.bead_id).collect::<Vec<_>>(),
        [z, x]
    );
    let finish = |store: &mut PostgresStore,
                  call_id,
                  lease,
                  shown: &core_memory_core::store::Shown,
                  id_key,
                  reply: &str| {
        let judged = validate(reply, shown, Names::MustBeFound).unwrap();
        let write = judgment(
            shown,
            &judged,
            Recording {
                id_key,
                hold_labels: false,
            },
        );
        store
            .finish_judgment(&JudgmentFinish {
                call_id,
                workspace: "w1",
                lease,
                result: CallResult::Accepted,
                usage: None,
                judgment: Some(&write),
                settles: None,
                reader: &reader,
                work: &[],
                at: w.clock.now(),
            })
            .unwrap()
    };

    // A's reply arrives, late. X is B's now: A stores nothing for it.
    let a_finished = finish(
        &mut w.store,
        a_call,
        a_claim.lease_until,
        &a_shown,
        &a_batch.id_key,
        r#"{"new": [{"id": "N1", "related": [], "unsure": []}], "names": []}"#,
    );
    assert_eq!((a_finished.judged, a_finished.verdicts), (0, 0));
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 2);
    // B's reply: Z and X are near each other. Every pair B was shown is
    // stored, and each pair once.
    let b_finished = finish(
        &mut b_store,
        b_call,
        b_claim.lease_until,
        &b_shown,
        &b_batch.id_key,
        r#"{"new": [{"id": "N1", "related": [], "unsure": []},
                    {"id": "N2", "related": [{"to": "N1", "rel": "NEAR"}], "unsure": []}],
            "names": []}"#,
    );
    assert_eq!(
        (b_finished.judged, b_finished.verdicts, b_finished.edges),
        (2, 3, 1)
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 3);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
}

#[test]
fn a_late_second_read_settles_no_label_it_did_not_read() {
    use core_memory_core::judge::{Names, second_judgment, validate};
    use core_memory_core::store::{CallResult, JudgmentFinish};

    let mut w = judging("second_read = true");
    let reader = w.engine.judge().unwrap().reader_ref.clone();
    let (h_event, h) = message(&mut w, &said("h", 0, "The launch is on Thursday."));
    let s = bead(&mut w, &said("s", 10, "The launch moves to Friday."));
    let says = judge_saying(&[(
        "moves to Friday",
        "is on Thursday",
        "SUPERSEDES<",
        "supersede",
    )]);
    assert_eq!(handled(round(&mut w, &FnTransport(says))).held, 1);

    // Worker A claims the second read and is permitted.
    let a_claim = claim(&w, 4);
    let a_batch = w.store.claim_judgment(&a_claim).unwrap().unwrap();
    let JudgeWork::SecondRead { label, heads, .. } = &a_batch.work else {
        panic!("not a second read: {:?}", a_batch.work);
    };
    assert_eq!(heads.as_slice(), [h]);
    let a_new = [s];
    let mut request = permit_request(w.clock.now(), &reader, &a_new, &some_cost);
    request.purpose = Purpose::SecondRead;
    request.window = WindowOf::These(heads);
    let JudgePermit::Granted {
        call_id: a_call,
        shown: a_shown,
        ..
    } = w.store.permit_judgment(&request).unwrap()
    else {
        panic!("the second read was not permitted");
    };
    // H is erased while A's call is out, which drops the held label.
    erase(&w, h_event);
    // A statement written before S arrives. A judge reads it and says S
    // supersedes it: a label of the same source, held in turn.
    let h2 = bead(&mut w, &said("h2", 5, "The launch is on Wednesday."));
    let mut b_store = w.db.store();
    let other = FnTransport(|_: &ModelRequest| {
        answer(json!({"new": [
            {"id": "N1", "unsure": [], "related": [
                {"to": "B1", "rel": "SUPERSEDES>", "label": "supersede"}]},
        ], "names": []}))
    });
    let report = handled(w.engine.judge_once(&mut b_store, &other).unwrap());
    assert_eq!(report.held, 1, "{report:?}");
    let held = |w: &World| {
        rows(
            w,
            "SELECT source_bead::text, head_beads::text FROM core_memory.pending_labels",
        )
    };
    assert_eq!(held(&w), [vec![s.0.to_string(), format!("{{{}}}", h2.0)]]);

    // A's reply arrives: its read of (S, H) agreed. It settles nothing it
    // did not read: the label of (S, H2) still waits for its own.
    let a_judged = validate(
        r#"{"new": [{"id": "N1", "unsure": [], "related": [
               {"to": "B1", "rel": "SUPERSEDES<", "label": "supersede"}]}], "names": []}"#,
        &a_shown,
        Names::MustBeFound,
    )
    .unwrap();
    let a_write = second_judgment(&a_shown, &a_judged, &a_batch.id_key, (*label, heads));
    let a_finished = w
        .store
        .finish_judgment(&JudgmentFinish {
            call_id: a_call,
            workspace: "w1",
            lease: a_claim.lease_until,
            result: CallResult::Accepted,
            usage: None,
            judgment: Some(&a_write),
            settles: Some(s),
            reader: &reader,
            work: &[],
            at: w.clock.now(),
        })
        .unwrap();
    assert_eq!((a_finished.verdicts, a_finished.labels), (0, 0));
    assert_eq!(held(&w).len(), 1);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.work WHERE kind = 'second_read'"),
        1
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.labels"), 0);
}

#[test]
fn a_chain_given_in_one_read_is_written_from_its_oldest_statement_on() {
    const CHAIN: &[(&str, &str, &str, &str)] = &[
        (
            "moves to Friday",
            "is on Thursday",
            "SUPERSEDES<",
            "supersede",
        ),
        (
            "Friday is confirmed",
            "moves to Friday",
            "SUPERSEDES<",
            "reaffirm",
        ),
    ];
    let mut w = judging("second_read = true");
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 5, "The launch moves to Friday."));
    bead(&mut w, &said("3", 9, "Friday is confirmed."));
    let transport = FnTransport(judge_saying(CHAIN));
    // The replacement waits for its second read. The reaffirmation, which
    // would need no second read of its own, waits as well: written now, it
    // would supersede the statement whose replacement waits, and that
    // could then never be written.
    let first = handled(round(&mut w, &transport));
    assert_eq!((first.labels, first.held), (0, 2));
    // They are read again in the order they were held: the oldest first.
    for _ in 0..2 {
        let report = handled(round(&mut w, &transport));
        assert_eq!((report.second_read, report.labels), (true, 1), "{report:?}");
    }
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);
    assert_eq!(
        chain(&w),
        [
            row(&["The launch moves to Friday.", "The launch is on Thursday."]),
            row(&["Friday is confirmed.", "The launch moves to Friday."]),
        ]
    );
    assert_eq!(current(&w), [row(&["Friday is confirmed."])]);

    // Two replacements in one chain, both held: whichever source has the
    // smaller ID, the older is read first, and both are written.
    const TWO: &[(&str, &str, &str, &str)] = &[
        (
            "moves to Friday",
            "is on Thursday",
            "SUPERSEDES<",
            "supersede",
        ),
        (
            "moves to Monday",
            "moves to Friday",
            "SUPERSEDES<",
            "supersede",
        ),
    ];
    let mut orders = std::collections::BTreeSet::new();
    for n in 0..8 {
        let mut w = judging("second_read = true");
        bead(
            &mut w,
            &said(&format!("a{n}"), 0, "The launch is on Thursday."),
        );
        let friday = bead(
            &mut w,
            &said(&format!("b{n}"), 5, "The launch moves to Friday."),
        );
        let monday = bead(
            &mut w,
            &said(&format!("c{n}"), 9, "The launch moves to Monday."),
        );
        orders.insert(friday < monday);
        let transport = FnTransport(judge_saying(TWO));
        while let JudgeRound::Handled(report) = round(&mut w, &transport) {
            assert_eq!(report.problem, None, "{report:?}");
        }
        assert_eq!(chain(&w).len(), 2, "run {n}");
        assert_eq!(
            current(&w),
            [row(&["The launch moves to Monday."])],
            "run {n}"
        );
    }
    assert_eq!(orders.len(), 2, "the IDs fell in one order only");
}

#[test]
fn a_held_label_that_can_no_longer_be_written_is_dropped_without_a_call() {
    let mut w = judging("second_read = true");
    let thursday = bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 5, "The launch moves to Friday."));
    let saturday = bead(&mut w, &said("3", 7, "Marketing moved it to Saturday."));
    let calls = Mutex::new(0u32);
    let says = judge_saying(&[(
        "moves to Friday",
        "is on Thursday",
        "SUPERSEDES<",
        "supersede",
    )]);
    let transport = FnTransport(|request: &ModelRequest| {
        *calls.lock().unwrap() += 1;
        says(request)
    });
    assert_eq!(handled(round(&mut w, &transport)).held, 1);
    // Something else supersedes Thursday meanwhile: here a label written
    // by the database's owner, as an older engine might have.
    w.db.connect()
        .execute(
            "WITH bead AS (
                 INSERT INTO core_memory.beads (bead_id, workspace_id, scope, kind, state,
                     event_id, chunk_id, created_by, call_id, time_at, time_basis, created_at,
                     seq)
                 SELECT gen_random_uuid(), 'w1', NULL, 'label', 'live', NULL, NULL, 'judge',
                        r.call_id, r.judged_at, 'captured', r.judged_at, 900001
                   FROM core_memory.judge_reads AS r
                 RETURNING bead_id, call_id, created_at)
             INSERT INTO core_memory.labels (bead_id, workspace_id, label, source_bead, call_id)
             SELECT bead_id, 'w1', 'supersede', $1, call_id FROM bead",
            &[&saturday.0],
        )
        .unwrap();
    w.db.connect()
        .execute(
            "INSERT INTO core_memory.edges (workspace_id, seq, edge_type, from_bead, to_bead,
                 call_id, created_at)
             SELECT 'w1', 900002, 'supersedes', l.bead_id, $2, l.call_id, now()
               FROM core_memory.labels AS l WHERE l.source_bead = $1",
            &[&saturday.0, &thursday.0],
        )
        .unwrap();
    // The held label can no longer be written. It is dropped when its
    // second read comes up, and no call is made for it.
    assert_eq!(round(&mut w, &transport), JudgeRound::Idle);
    assert_eq!(*calls.lock().unwrap(), 1);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.pending_labels"),
        0
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.work"), 0);
    // Its verdict still says what the first read said, and that it waited.
    assert_eq!(
        rows(
            &w,
            "SELECT verdict, note FROM core_memory.verdicts WHERE verdict = 'supersedes'"
        ),
        [row(&["supersedes", "awaits_second_read"])]
    );
}

#[test]
fn a_second_read_waits_without_a_receipt_while_a_bead_it_needs_may_not_be_shown() {
    let mut w = judging("second_read = true");
    w.store.enroll_scope("beta", "w1").unwrap();
    allow(&w, "beta");
    bead(
        &mut w,
        &said_in("beta", "1", 0, "The launch is on Thursday."),
    );
    bead(&mut w, &said("2", 9, "The launch moves to Friday."));
    let calls = Mutex::new(0u32);
    let says = judge_saying(&[(
        "moves to Friday",
        "is on Thursday",
        "SUPERSEDES<",
        "supersede",
    )]);
    let transport = FnTransport(|request: &ModelRequest| {
        *calls.lock().unwrap() += 1;
        says(request)
    });
    assert_eq!(handled(round(&mut w, &transport)).held, 1);
    // The scope of the earlier statement withdraws its consent. The two
    // cannot be read together now.
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.revoke_destination('beta', '{DESTINATION}')"
        ))
        .unwrap();
    for _ in 0..3 {
        let report = handled(round(&mut w, &transport));
        assert_eq!(
            (report.second_read, report.calls, report.problem),
            (true, 0, Some("no_consent"))
        );
        assert_eq!(round(&mut w, &transport), JudgeRound::Idle);
        w.clock.advance_millis(300_000);
    }
    // No receipt was written for a call that was not made, so nothing was
    // counted against a budget, and no attempt was spent.
    assert_eq!(*calls.lock().unwrap(), 1);
    assert_eq!(
        w.count("SELECT count(*) FROM core_memory.outbound_calls"),
        1
    );
    assert_eq!(
        rows(
            &w,
            "SELECT kind, state, attempts::text, problem FROM core_memory.work"
        ),
        [row(&["second_read", "ready", "0", "no_consent"])]
    );
    // Consent comes back, and the second read is made.
    allow(&w, "beta");
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.second_read, report.labels), (true, 1));
    assert_eq!(*calls.lock().unwrap(), 2);
}

#[test]
fn a_bead_withheld_from_the_first_call_keeps_its_reason_when_the_repair_is_not_sent() {
    for max_attempts in [3, 1] {
        let config = judge_config("second_read = false", "").replace(
            "max_attempts = 3",
            &format!("max_attempts = {max_attempts}"),
        );
        let mut w = World::with_config(&config);
        allow(&w, "acme");
        w.store.enroll_scope("beta", "w1").unwrap();
        let (gone, _) = w.accept(&said("1", 0, "One."));
        bead(&mut w, &said("2", 1, "Two."));
        // Its scope never allowed the judge: it is never shown.
        bead(&mut w, &said_in("beta", "1", 2, "Theirs."));
        let url = w.db.url.clone();
        let transport = FnTransport(|_: &ModelRequest| {
            // While the judge reads, a bead it was shown is erased, and the
            // reply is not valid: the repair cannot be made.
            let mut host = postgres::Client::connect(&url, postgres::NoTls).unwrap();
            host.execute(
                "SELECT core_memory.erase_event($1, 'user_request', 'owner')",
                &[&gone.0],
            )
            .unwrap();
            answer(json!({"new": [], "names": []}))
        });
        let report = handled(round(&mut w, &transport));
        assert_eq!((report.calls, report.problem), (1, Some("batch_changed")));
        // The bead that was never shown is put off for want of consent, as
        // it was when the first call was made: no attempt counted, and due
        // once its scope allows the judge.
        assert_eq!(
            rows(
                &w,
                "SELECT state, attempts::text, problem FROM core_memory.work
                  WHERE scope = 'beta'"
            ),
            [row(&["ready", "0", "no_consent"])],
            "max_attempts = {max_attempts}"
        );
    }
}

#[test]
fn beads_queued_after_later_ones_were_read_beside_them_are_not_read_with_them_again() {
    // Two messages stored before there was a judge.
    let mut w = World::new();
    bead(&mut w, &said("1", 0, "Before there was a judge."));
    bead(&mut w, &said("2", 1, "Also before."));
    w.engine = common::engine_with(&judge_config("second_read = false", ""), &w.clock);
    allow(&w, "acme");
    let transport = FnTransport(nothing);
    // A third arrives and is read beside the two.
    bead(&mut w, &said("3", 2, "After."));
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.new_beads, report.window_beads, report.verdicts),
        (1, 2, 2)
    );
    // The two are then queued. They are read with each other, and not
    // again with the third: this judge has read both of those pairs.
    let now = w.clock.now();
    w.store.queue_judgments("acme", None, now).unwrap();
    let report = handled(round(&mut w, &transport));
    assert_eq!(
        (report.new_beads, report.window_beads, report.verdicts),
        (2, 0, 1)
    );
    assert_eq!(
        w.count(
            "SELECT count(*) FROM (
                 SELECT least(earlier_bead, later_bead), greatest(earlier_bead, later_bead)
                   FROM core_memory.verdicts GROUP BY 1, 2 HAVING count(*) > 1) AS twice"
        ),
        0
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.verdicts"), 3);
    // Another judge reads them all again, as it was asked to.
    let mut other = w.engine.judge().unwrap().reader_ref.clone();
    other.binding_rev = core_memory_contract::Sha256Digest::of(b"another judge");
    let other_config = judge_config("second_read = false", "").replace("stand-in-1", "stand-in-2");
    w.engine = common::engine_with(&other_config, &w.clock);
    let this = w.engine.judge().unwrap().reader_ref.clone();
    let queued = w.store.queue_judgments("acme", Some(&this), now).unwrap();
    assert_eq!(
        queued,
        QueuedReads::Queued {
            chunks: 3,
            tool_calls: 0
        }
    );
    let report = handled(round(&mut w, &transport));
    assert_eq!((report.new_beads, report.verdicts), (3, 3));
}

#[test]
fn an_export_of_one_scope_gives_only_what_lies_inside_it_and_keeps_what_it_gave() {
    let mut w = judging("");
    w.store.enroll_scope("beta", "w1").unwrap();
    allow(&w, "beta");
    let kinds = |changes: &[Change]| changes.iter().map(kind_of).collect::<Vec<_>>();
    // A statement of `acme` takes the place of one of `beta`. The label
    // says so, and its edge points into `beta`: none of it is `acme`'s.
    bead(
        &mut w,
        &said_in("beta", "1", 0, "Marketing booked Thursday."),
    );
    bead(&mut w, &said("1", 5, "The launch moves to Friday."));
    let supersedes = FnTransport(|_: &ModelRequest| {
        answer(json!({"new": [
            {"id": "N1", "related": [], "unsure": []},
            {"id": "N2", "unsure": [], "related": [
                {"to": "N1", "rel": "SUPERSEDES<", "label": "supersede"}]},
        ], "names": []}))
    });
    assert_eq!(handled(round(&mut w, &supersedes)).labels, 1);
    let acme = w.store.export("w1", 0, 100, Some("acme")).unwrap();
    assert_eq!(kinds(&acme), ["event"]);
    let beta = w.store.export("w1", 0, 100, Some("beta")).unwrap();
    assert_eq!(kinds(&beta), ["event"]);

    // Two parts of one account in `acme` are one memory. A consumer of
    // `acme` is given the group.
    bead(&mut w, &said("2", 60, "First half of the account."));
    bead(&mut w, &said("3", 61, "Second half of the account."));
    let grouping = FnTransport(|request: &ModelRequest| {
        let (listed, new) = shown(request);
        let halves: Vec<Value> = listed
            .iter()
            .chain(&new)
            .filter(|m| m["text"].as_str().unwrap_or_default().contains("half"))
            .map(|m| m["id"].clone())
            .collect();
        let entries: Vec<Value> = new
            .iter()
            .map(|m| {
                let mine = m["text"].as_str().unwrap_or_default().contains("part")
                    || m["text"].as_str().unwrap_or_default().contains("half");
                let related: Vec<Value> = if mine {
                    halves
                        .iter()
                        .filter(|id| **id != m["id"])
                        .filter(|id| {
                            // A new memory is compared with the listed ones
                            // and the new ones before it.
                            let at = |handle: &Value| {
                                listed.iter().chain(&new).position(|x| x["id"] == *handle)
                            };
                            at(id) < at(&m["id"])
                        })
                        .map(|id| json!({"to": id, "rel": "CONTAINS"}))
                        .collect()
                } else {
                    Vec::new()
                };
                json!({"id": m["id"], "related": related, "unsure": []})
            })
            .collect();
        answer(json!({"new": entries, "names": []}))
    });
    assert_eq!(handled(round(&mut w, &grouping)).groups, 1);
    let before = w.store.export("w1", 0, 100, Some("acme")).unwrap();
    // The two halves and the earlier statement of `acme`: three pairs.
    // The pairs with `beta`'s statement are not `acme`'s.
    assert_eq!(
        kinds(&before)[1..],
        [
            "event", "event", "verdict", "verdict", "verdict", "bead", "edge", "edge"
        ]
    );
    let seen = before.last().unwrap().seq();
    // A third part arrives from `beta`, and joins the group. What `acme`
    // was given stays given: the group stays in its export, without the
    // member from `beta`, and nothing after the consumer's position names
    // `beta`'s bead.
    bead(
        &mut w,
        &said_in("beta", "2", 62, "A third half of the account."),
    );
    assert_eq!(handled(round(&mut w, &grouping)).groups, 1);
    let after = w.store.export("w1", 0, 100, Some("acme")).unwrap();
    assert_eq!(kinds(&after), kinds(&before));
    assert!(
        w.store
            .export("w1", seen, 100, Some("acme"))
            .unwrap()
            .is_empty()
    );
    // The whole workspace's export has all of it.
    let all = kinds(&w.changes());
    assert_eq!(all.iter().filter(|kind| **kind == "edge").count(), 4);
}

#[test]
fn the_tables_keep_a_chain_growing_at_its_head_whoever_writes_to_them() {
    let mut w = judging("");
    let thursday = bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    let friday = bead(&mut w, &said("2", 5, "The launch moves to Friday."));
    let saturday = bead(&mut w, &said("3", 9, "Ops says Saturday."));
    let (gone_event, gone) = message(&mut w, &said("4", 10, "Erased later."));
    let says = judge_saying(&[(
        "moves to Friday",
        "is on Thursday",
        "SUPERSEDES<",
        "supersede",
    )]);
    assert_eq!(handled(round(&mut w, &FnTransport(says))).labels, 1);
    erase(&w, gone_event);
    let mut admin = w.db.connect();
    // A label bead of `source`, written by the owner.
    let label_of = |admin: &mut postgres::Client, source: BeadId, seq: i64| -> uuid::Uuid {
        admin
            .query_one(
                "WITH bead AS (
                     INSERT INTO core_memory.beads (bead_id, workspace_id, scope, kind, state,
                         event_id, chunk_id, created_by, call_id, time_at, time_basis,
                         created_at, seq)
                     SELECT gen_random_uuid(), 'w1', NULL, 'label', 'live', NULL, NULL,
                            'judge', r.call_id, r.judged_at, 'captured', r.judged_at, $2
                       FROM core_memory.judge_reads AS r
                     RETURNING bead_id, call_id)
                 INSERT INTO core_memory.labels (bead_id, workspace_id, label, source_bead,
                     call_id)
                 SELECT bead_id, 'w1', 'supersede', $1, call_id FROM bead
                 RETURNING bead_id",
                &[&source.0, &seq],
            )
            .unwrap()
            .get(0)
    };
    let follows = |admin: &mut postgres::Client, label: uuid::Uuid, bead: BeadId, seq: i64| {
        admin
            .execute(
                "INSERT INTO core_memory.edges (workspace_id, seq, edge_type, from_bead,
                     to_bead, call_id, created_at)
                 SELECT 'w1', $3, 'supersedes', l.bead_id, $2, l.call_id, now()
                   FROM core_memory.labels AS l WHERE l.bead_id = $1",
                &[&label, &bead.0, &seq],
            )
            .map_err(|e| e.as_db_error().unwrap().message().to_string())
    };
    // A statement that was itself replaced takes the place of nothing more,
    // and so no two statements take each other's place.
    let of_thursday = label_of(&mut admin, thursday, 900001);
    let refused = follows(&mut admin, of_thursday, saturday, 900002).unwrap_err();
    assert!(
        refused.contains("label of a current statement"),
        "{refused}"
    );
    let refused = follows(&mut admin, of_thursday, friday, 900003).unwrap_err();
    assert!(
        refused.contains("label of a current statement"),
        "{refused}"
    );
    // Nor does a statement follow itself.
    let of_saturday = label_of(&mut admin, saturday, 900004);
    let refused = follows(&mut admin, of_saturday, saturday, 900005).unwrap_err();
    assert!(
        refused.contains("label of a current statement"),
        "{refused}"
    );
    // The current statement may.
    assert_eq!(follows(&mut admin, of_saturday, friday, 900006), Ok(1));

    let refused = |admin: &mut postgres::Client,
                   sql: &str,
                   params: &[&(dyn postgres::types::ToSql + Sync)]| {
        admin
            .execute(sql, params)
            .unwrap_err()
            .as_db_error()
            .unwrap()
            .message()
            .to_string()
    };
    // A held label about an erased bead.
    let told = refused(
        &mut admin,
        "INSERT INTO core_memory.pending_labels (source_bead, workspace_id, label, head_beads,
             ordinal, call_id, proposed_at)
         SELECT $1, 'w1', 'supersede', ARRAY[$2::uuid], 900007, r.call_id, now()
           FROM core_memory.judge_reads AS r",
        &[&saturday.0, &gone.0],
    );
    assert!(told.contains("only about beads that are live"), "{told}");
    // A verdict filed under a workspace that is not its beads'.
    w.store.create_workspace("w2").unwrap();
    let told = refused(
        &mut admin,
        "INSERT INTO core_memory.verdicts (workspace_id, seq, earlier_bead, later_bead,
             verdict, direction, call_id, columns, columns_rev)
         SELECT 'w2', 1, v.earlier_bead, v.later_bead, 'none', 'undirected', v.call_id,
                v.columns, 1
           FROM core_memory.verdicts AS v LIMIT 1",
        &[],
    );
    assert!(told.contains("only about beads that are live"), "{told}");
    // A name past the end of its chunk's text, and a name in a tool call,
    // which has no text of its own.
    let link = |a: uuid::Uuid, b: uuid::Uuid, end: i64| -> String {
        let (a, b) = (a.min(b), a.max(b));
        format!(
            "INSERT INTO core_memory.name_links (workspace_id, seq, a_bead, a_start, a_end,
                 b_bead, b_start, b_end, call_id, linked_at)
             SELECT 'w1', 900008, '{a}', 0, {end}, '{b}', 0, 1, r.call_id, now()
               FROM core_memory.judge_reads AS r"
        )
    };
    let told = refused(&mut admin, &link(thursday.0, friday.0, 100_000), &[]);
    assert!(
        told.contains("inside the text of each of its chunks"),
        "{told}"
    );
    let (call, _) = w.accept(&common::tool_call(
        "toolu_9",
        "Bash",
        json!({"command": "ls"}),
        "a\nb",
        None,
    ));
    let told = refused(&mut admin, &link(thursday.0, call.0, 1), &[]);
    assert!(
        told.contains("inside the text of each of its chunks"),
        "{told}"
    );
}

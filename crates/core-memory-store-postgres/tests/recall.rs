//! Recall against PostgreSQL: words find the chunks that hold them, what a
//! judge ended is answered by what ended it, disputes and arrows come with
//! the beads, and nothing is returned of a scope the caller did not name or
//! of a message that was erased.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use common::{CONFIG, World, tool_call};
use core_memory_contract::{
    BeadId, BeadKind, RecallAnswer, RecallFound, RecallVia, RecalledBead, SourceEvent,
};
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_core::store::Store;
use core_memory_core::{JudgeRound, RecallQuery, ScopeGrants};
use core_memory_store_postgres::{ConnectConfig, PostgresStore};
use serde_json::{Value, json};

const DESTINATION: &str = "judge.test:8080";

fn judge_config() -> String {
    format!(
        r#"{CONFIG}
[[profile]]
id = "big.notes"
rev = 1
source_type = "document"
content = "text"
max_event_bytes = 2000000
chunker = {{ id = "text.structural", rev = 1, max = 1000000 }}

[judge]
binding = "careful"
debounce_ms = 0
max_attempts = 3
retry_backoff_ms = 1000
second_read = false

[[binding]]
id = "careful"
provider = "stand-in"
endpoint = "http://Judge.Test:8080/v1"
model = "stand-in-1"
max_output_tokens = 2000
"#
    )
}

/// A world with a judge whose destination scopes `acme` and `other`, both
/// of workspace `w1`, have granted.
fn world() -> World {
    let w = World::with_config(&judge_config());
    w.db.connect()
        .batch_execute(&format!(
            "SELECT core_memory.enroll_scope('other', 'w1');
             SELECT core_memory.grant_destination('acme', '{DESTINATION}');
             SELECT core_memory.grant_destination('other', '{DESTINATION}');"
        ))
        .unwrap();
    w
}

/// A message written `minutes` after noon.
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

fn bead(w: &mut World, event: &SourceEvent) -> BeadId {
    let (_, chunks) = w.accept(event);
    assert_eq!(chunks.len(), 1);
    BeadId(chunks[0].0)
}

/// A judge that says what `relates` says: for each new memory, the pairs
/// it relates, as `(text of the new memory, text of the other, arrow)`.
fn judge(w: &mut World, relates: &'static [(&'static str, &'static str, &'static str)]) {
    let transport = FnTransport(move |request: &ModelRequest| {
        let user: Value = serde_json::from_str(&request.user).unwrap();
        let listed = user["listed"].as_array().unwrap();
        let new = user["new"].as_array().unwrap();
        let all: Vec<&Value> = listed.iter().chain(new).collect();
        let handle = |part: &str| {
            all.iter()
                .find(|m| m["text"].as_str().is_some_and(|t| t.contains(part)))
                .map(|m| m["id"].clone())
        };
        let entries: Vec<Value> = new
            .iter()
            .map(|memory| {
                let text = memory["text"].as_str().unwrap_or_default();
                let related: Vec<Value> = relates
                    .iter()
                    .filter(|(of, ..)| text.contains(of))
                    .filter_map(|(_, other, arrow)| {
                        Some(json!({"to": handle(other)?, "arrow": arrow}))
                    })
                    .collect();
                json!({"id": memory["id"], "related": related, "unsure": []})
            })
            .collect();
        Ok::<_, TransportError>(ModelResponse {
            text: json!({"new": entries, "names": []}).to_string(),
            usage: Usage {
                input_tokens: 500,
                output_tokens: 40,
                ..Usage::default()
            },
        })
    });
    while let JudgeRound::Handled(_) = w.engine.judge_once(&mut w.store, &transport).unwrap() {}
}

fn ask(query: &str) -> RecallQuery {
    RecallQuery {
        query: query.into(),
        scopes: ScopeGrants::All,
        limit: 10,
        follow: 1,
        include_ended: false,
    }
}

fn only(scopes: &[&str]) -> ScopeGrants {
    ScopeGrants::Only(scopes.iter().map(|s| (*s).to_string()).collect())
}

fn text(bead: &RecalledBead) -> String {
    bead.chunks.iter().map(|c| c.text.as_str()).collect()
}

/// The answer's beads as their text, with how each came into it.
fn told(answer: &RecallAnswer) -> Vec<(String, String)> {
    answer
        .beads
        .iter()
        .map(|b| {
            let via = match &b.via {
                RecallVia::Match { .. } => "match".to_string(),
                RecallVia::Successor { .. } => "successor".to_string(),
                RecallVia::Linked { steps } => format!("linked {steps}"),
            };
            (text(b), via)
        })
        .collect()
}

fn pair(a: &str, b: &str) -> (String, String) {
    (a.to_string(), b.to_string())
}

fn find<'a>(answer: &'a RecallAnswer, part: &str) -> &'a RecalledBead {
    answer
        .beads
        .iter()
        .find(|b| text(b).contains(part))
        .unwrap_or_else(|| panic!("no bead holds `{part}`"))
}

#[test]
fn words_find_the_chunks_that_hold_them_best_first() {
    let mut w = world();
    let thursday = bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 5, "The export still takes three hours."));
    let early = bead(
        &mut w,
        &said(
            "3",
            9,
            "Launching early means launching without the export.",
        ),
    );
    bead(
        &mut w,
        &said_in("other", "4", 10, "Another team's launch plan."),
    );

    // Words are compared as stems, and the chunk that holds more of them
    // comes first. Every scope is searched when none is named.
    let answer = w.store.recall(&ask("launches")).unwrap();
    assert_eq!(answer.found, RecallFound::Matched);
    assert_eq!(answer.beads.len(), 3);
    assert_eq!(answer.beads[0].bead_id, early);
    let first = &answer.beads[0];
    assert_eq!(first.kind, BeadKind::Chunk);
    assert_eq!(
        text(first),
        "Launching early means launching without the export."
    );
    let event = first.event.as_ref().unwrap();
    assert_eq!(
        (
            event.key.scope.as_str(),
            event.key.source_ns.as_str(),
            event.key.external_id.as_str()
        ),
        ("acme", "slack/T01", "3")
    );
    assert!(first.current && !first.disputed && !first.text_omitted);
    assert!(answer.arrows.is_empty());

    // Named scopes narrow the search, and limit the answer.
    let mut narrowed = ask("launch");
    narrowed.scopes = only(&["acme"]);
    let answer = w.store.recall(&narrowed).unwrap();
    let ids: Vec<BeadId> = answer.beads.iter().map(|b| b.bead_id).collect();
    assert_eq!(ids, [early, thursday]);
    narrowed.limit = 1;
    assert_eq!(w.store.recall(&narrowed).unwrap().beads.len(), 1);

    // All the words are required, a quoted phrase must stand together, and
    // a word after `-` leaves out what holds it.
    let ids = |w: &mut World, query: &str| -> Vec<BeadId> {
        let answer = w.store.recall(&ask(query)).unwrap();
        answer.beads.iter().map(|b| b.bead_id).collect()
    };
    assert_eq!(ids(&mut w, "launch export"), [early]);
    assert_eq!(ids(&mut w, "launch -export -plan"), [thursday]);
    let plan = w.store.recall(&ask("\"launch plan\"")).unwrap();
    assert_eq!(told(&plan), [pair("Another team's launch plan.", "match")]);
    assert!(ids(&mut w, "\"plan launch\"").is_empty());

    // Nothing found, and nothing that could be searched for, are told
    // apart.
    assert_eq!(
        w.store.recall(&ask("zeppelin")).unwrap().found,
        RecallFound::NoMatch
    );
    let common = w.store.recall(&ask("the and of")).unwrap();
    assert_eq!(common.found, RecallFound::NoTerms);
    assert!(common.beads.is_empty());
    // Words to leave out alone name nothing to look for: the index could
    // not answer, and every chunk would be read.
    for nothing_to_find in ["-export", "launch or -export", "\"\" -the"] {
        assert_eq!(
            w.store.recall(&ask(nothing_to_find)).unwrap().found,
            RecallFound::NoTerms,
            "{nothing_to_find}"
        );
    }
}

#[test]
fn a_statement_that_was_replaced_is_answered_by_what_replaced_it() {
    let mut w = world();
    let thursday = bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    let friday = bead(&mut w, &said("2", 5, "We moved it: Friday now."));
    judge(&mut w, &[("Friday now", "Thursday", "repl")]);

    // Only the old statement holds the word. What replaced it answers.
    let answer = w.store.recall(&ask("Thursday")).unwrap();
    assert_eq!(answer.found, RecallFound::Matched);
    assert_eq!(
        told(&answer),
        [pair("We moved it: Friday now.", "successor")]
    );
    let successor = &answer.beads[0];
    assert_eq!(successor.bead_id, friday);
    assert_eq!(successor.via, RecallVia::Successor { of: thursday });
    assert!(successor.current);

    // Asked for, the old statement comes too, marked as ended and by what,
    // with the arrow that ended it.
    let mut asked = ask("Thursday");
    asked.include_ended = true;
    let answer = w.store.recall(&asked).unwrap();
    assert_eq!(
        told(&answer),
        [
            pair("The launch is on Thursday.", "match"),
            pair("We moved it: Friday now.", "successor"),
        ]
    );
    assert!(!answer.beads[0].current);
    assert_eq!(answer.beads[0].ended_by, Some(friday));
    assert_eq!(answer.arrows.len(), 1);
    let arrow = &answer.arrows[0];
    assert_eq!(
        (arrow.from_bead, arrow.to_bead, arrow.arrow.as_str()),
        (friday, thursday, "repl")
    );

    // Without following arrows, the ended statement is not reached from
    // what replaced it either.
    let mut alone = ask("Friday");
    alone.follow = 0;
    assert_eq!(
        told(&w.store.recall(&alone).unwrap()),
        [pair("We moved it: Friday now.", "match")]
    );
    // One step along the arrows reaches it, marked as ended.
    let answer = w.store.recall(&ask("Friday")).unwrap();
    assert_eq!(
        told(&answer),
        [
            pair("We moved it: Friday now.", "match"),
            pair("The launch is on Thursday.", "linked 1"),
        ]
    );
    assert!(!answer.beads[1].current);
}

#[test]
fn a_dispute_is_open_while_both_sides_are_current() {
    let mut w = world();
    bead(&mut w, &said("1", 0, "The launch is on Thursday."));
    bead(&mut w, &said("2", 5, "No, the launch is on Friday."));
    judge(&mut w, &[("Friday", "Thursday", "!contradicts")]);

    let answer = w.store.recall(&ask("launch")).unwrap();
    assert_eq!(answer.beads.len(), 2);
    assert!(answer.beads.iter().all(|b| b.current && b.disputed));
    assert_eq!(answer.arrows.len(), 1);
    assert_eq!(answer.arrows[0].arrow.as_str(), "!contradicts");

    // Once one side is replaced, the other is no longer disputed.
    bead(&mut w, &said("3", 9, "Final word: Saturday."));
    judge(&mut w, &[("Saturday", "Friday", "repl")]);
    let mut asked = ask("launch");
    asked.include_ended = true;
    let answer = w.store.recall(&asked).unwrap();
    let thursday = find(&answer, "Thursday");
    assert!(thursday.current && !thursday.disputed);
    let friday = find(&answer, "Friday");
    assert!(!friday.current && !friday.disputed);
}

#[test]
fn arrows_are_followed_step_by_step_and_never_into_a_scope_not_named() {
    let mut w = world();
    bead(&mut w, &said("1", 0, "Alpha milestone is done."));
    bead(&mut w, &said("2", 5, "Beta work started."));
    bead(&mut w, &said("3", 9, "Gamma shipped."));
    bead(&mut w, &said_in("other", "4", 12, "Delta depends on it."));
    judge(
        &mut w,
        &[
            ("Beta", "Alpha", "enables"),
            ("Gamma", "Beta", "enables"),
            ("Delta", "Alpha", "cause"),
        ],
    );

    let mut asked = ask("alpha");
    asked.follow = 0;
    assert_eq!(
        told(&w.store.recall(&asked).unwrap()),
        [pair("Alpha milestone is done.", "match")]
    );
    asked.follow = 1;
    let answer = w.store.recall(&asked).unwrap();
    assert_eq!(
        told(&answer),
        [
            pair("Alpha milestone is done.", "match"),
            // Nearest first and, at one distance, newest first.
            pair("Delta depends on it.", "linked 1"),
            pair("Beta work started.", "linked 1"),
        ]
    );
    assert_eq!(answer.arrows.len(), 2);
    asked.follow = 2;
    let answer = w.store.recall(&asked).unwrap();
    assert_eq!(told(&answer)[3], pair("Gamma shipped.", "linked 2"));
    assert_eq!(answer.arrows.len(), 3);

    // A scope that was not named is neither searched nor reached, and no
    // arrow to it is shown.
    asked.scopes = only(&["acme"]);
    let answer = w.store.recall(&asked).unwrap();
    assert_eq!(
        told(&answer),
        [
            pair("Alpha milestone is done.", "match"),
            pair("Beta work started.", "linked 1"),
            pair("Gamma shipped.", "linked 2"),
        ]
    );
    assert_eq!(answer.arrows.len(), 2);
    assert!(
        answer
            .beads
            .iter()
            .all(|b| b.event.as_ref().unwrap().key.scope == "acme")
    );
}

#[test]
fn a_group_costs_no_step_and_its_members_come_one_step_from_a_match() {
    let mut w = world();
    bead(&mut w, &said("1", 0, "Rollout part one: staging."));
    bead(&mut w, &said("2", 5, "Rollout part two: production."));
    judge(&mut w, &[("part two", "part one", "same-memory")]);

    let answer = w.store.recall(&ask("staging")).unwrap();
    let kinds: Vec<(BeadKind, String)> = answer
        .beads
        .iter()
        .map(|b| {
            let via = match &b.via {
                RecallVia::Match { .. } => "match".to_string(),
                RecallVia::Linked { steps } => format!("linked {steps}"),
                RecallVia::Successor { .. } => "successor".to_string(),
            };
            (b.kind, via)
        })
        .collect();
    assert_eq!(
        kinds,
        [
            (BeadKind::Chunk, "match".to_string()),
            (BeadKind::Group, "linked 0".to_string()),
            (BeadKind::Chunk, "linked 1".to_string()),
        ]
    );
    let group = &answer.beads[1];
    assert!(group.chunks.is_empty() && group.event.is_none() && !group.text_omitted);
    assert_eq!(text(&answer.beads[2]), "Rollout part two: production.");
    assert_eq!(answer.arrows.len(), 2);
    assert!(answer.arrows.iter().all(|a| a.arrow.as_str() == "consists"));
}

#[test]
fn what_ended_a_statement_in_a_scope_not_named_is_not_shown() {
    let mut w = world();
    let mine = bead(&mut w, &said("1", 0, "The budget is ten thousand."));
    bead(&mut w, &said_in("other", "2", 5, "Revised figure: twelve."));
    judge(&mut w, &[("Revised figure", "budget", "repl")]);

    let mut asked = ask("budget");
    asked.scopes = only(&["acme"]);
    // Ended, and what ended it is in a scope the caller did not name.
    assert!(w.store.recall(&asked).unwrap().beads.is_empty());
    asked.include_ended = true;
    let answer = w.store.recall(&asked).unwrap();
    assert_eq!(answer.beads.len(), 1);
    let ended = &answer.beads[0];
    assert_eq!(ended.bead_id, mine);
    assert!(!ended.current);
    assert_eq!(ended.ended_by, None);
    assert!(answer.arrows.is_empty());
}

#[test]
fn a_group_next_to_a_bead_at_the_hop_limit_is_reached_for_it_costs_no_step() {
    let mut w = world();
    bead(&mut w, &said("1", 0, "Quartz marker begins."));
    bead(&mut w, &said("2", 5, "Rollout part one: staging."));
    bead(&mut w, &said("3", 10, "Rollout part two: production."));
    judge(
        &mut w,
        &[
            ("part one", "Quartz", "see"),
            ("part two", "part one", "same-memory"),
        ],
    );

    let answer = w.store.recall(&ask("quartz")).unwrap();
    let kinds: Vec<(BeadKind, String)> = answer
        .beads
        .iter()
        .map(|b| {
            let via = match &b.via {
                RecallVia::Match { .. } => "match".to_string(),
                RecallVia::Successor { .. } => "successor".to_string(),
                RecallVia::Linked { steps } => format!("linked {steps}"),
            };
            (b.kind, via)
        })
        .collect();
    assert_eq!(
        kinds,
        [
            (BeadKind::Chunk, "match".to_string()),
            (BeadKind::Chunk, "linked 1".to_string()),
            (BeadKind::Group, "linked 1".to_string()),
        ]
    );
    // Its other member is a second step away, past the limit of one.
    assert!(answer.beads.iter().all(|b| !text(b).contains("part two")));

    let mut asked = ask("quartz");
    asked.follow = 2;
    let answer = w.store.recall(&asked).unwrap();
    assert!(answer.beads.iter().any(|b| text(b).contains("part two")));
}

#[test]
fn a_chain_that_runs_through_a_scope_not_named_has_no_successor_to_show() {
    let mut w = world();
    let first = bead(&mut w, &said("1", 0, "The budget is ten thousand."));
    bead(&mut w, &said_in("other", "2", 5, "Revised figure: twelve."));
    judge(&mut w, &[("Revised figure", "budget", "repl")]);
    bead(&mut w, &said("3", 10, "Final figure: fifteen."));
    judge(&mut w, &[("Final figure", "Revised figure", "repl")]);

    // Everything may be read: the first is answered by the last of the chain.
    // Without following arrows, so that only the choice of successor shows.
    let mut all = ask("budget");
    all.follow = 0;
    let answer = w.store.recall(&all).unwrap();
    assert_eq!(told(&answer), [pair("Final figure: fifteen.", "successor")]);

    // The middle of the chain is in a scope the caller did not name, and the
    // last is in one it did. Recall does not pass through the middle.
    let mut asked = ask("budget");
    asked.follow = 0;
    asked.scopes = only(&["acme"]);
    assert!(w.store.recall(&asked).unwrap().beads.is_empty());
    asked.include_ended = true;
    let answer = w.store.recall(&asked).unwrap();
    assert_eq!(answer.beads.len(), 1);
    assert_eq!(answer.beads[0].bead_id, first);
    assert!(!answer.beads[0].current);
}

#[test]
fn nothing_is_recalled_of_an_erased_message() {
    let mut w = world();
    let (gone, _) = w.accept(&said("1", 0, "The vault code is 4417."));
    bead(&mut w, &said("2", 5, "The vault opens at nine."));
    judge(&mut w, &[("opens at nine", "vault code", "note")]);
    let answer = w.store.recall(&ask("vault")).unwrap();
    assert_eq!(answer.beads.len(), 2);

    w.db.connect()
        .execute(
            "SELECT core_memory.erase_event($1, 'user_request', 'test')",
            &[&gone.0],
        )
        .unwrap();
    assert_eq!(
        w.store.recall(&ask("4417")).unwrap().found,
        RecallFound::NoMatch
    );
    // What is left is not linked to it any more.
    let answer = w.store.recall(&ask("vault")).unwrap();
    assert_eq!(told(&answer), [pair("The vault opens at nine.", "match")]);
    assert!(answer.arrows.is_empty());
}

#[test]
fn a_tool_call_is_recalled_whole() {
    let mut w = world();
    let (event_id, chunks) = w.accept(&tool_call(
        "call-1",
        "search",
        json!({"q": "quarterly revenue"}),
        "Found three reports on quarterly revenue.",
        None,
    ));
    let answer = w.store.recall(&ask("revenue reports")).unwrap();
    assert_eq!(answer.beads.len(), 1);
    let call = &answer.beads[0];
    assert_eq!(call.kind, BeadKind::Tool);
    assert_eq!(call.bead_id, BeadId(event_id.0));
    let ids: Vec<_> = call.chunks.iter().map(|c| c.chunk_id).collect();
    assert_eq!(ids, chunks);
    assert!(text(call).contains("Found three reports"));
}

fn big(id: &str, text: &str) -> SourceEvent {
    serde_json::from_value(json!({
        "scope": "acme",
        "source_ns": "notes",
        "external_id": id,
        "profile_id": "big.notes",
        "media_type": "text/plain",
        "content": {"text": text},
        "parser_contract_version": "1"
    }))
    .unwrap()
}

#[test]
fn an_answer_carries_text_only_as_far_as_it_has_room() {
    let mut w = world();
    // Chunks longer than the index reads are stored, and found by their
    // first words.
    let filler = "x".repeat(700_000);
    let first = w.accept(&big("1", &format!("zebra {filler}"))).0;
    let second = w.accept(&big("2", &format!("zebra {filler}"))).0;
    w.accept(&big("3", "zebra crossing"));
    let answer = w.store.recall(&ask("zebra")).unwrap();
    assert_eq!(answer.beads.len(), 3);
    // Newest first at one rank: the short one, then the second long one;
    // the first long one would take the answer past its limit.
    assert_eq!(text(&answer.beads[0]), "zebra crossing");
    assert_eq!(answer.beads[1].event.as_ref().unwrap().event_id, second);
    assert!(!answer.beads[1].text_omitted);
    let omitted = &answer.beads[2];
    assert_eq!(omitted.event.as_ref().unwrap().event_id, first);
    assert!(omitted.text_omitted && omitted.chunks.is_empty());
}

#[test]
fn the_planner_keeps_no_sample_of_the_words_of_the_text() {
    let mut w = world();
    for n in 0..50 {
        bead(
            &mut w,
            &said(
                &n.to_string(),
                n,
                &format!("Codename hyacinth, report {n}."),
            ),
        );
    }
    let mut owner = w.db.connect();
    owner
        .batch_execute("ANALYZE core_memory.chunk_text")
        .unwrap();
    let sampled = |owner: &mut postgres::Client, index: &str| -> i64 {
        owner
            .query_one(
                &format!(
                    "SELECT count(*) FROM pg_catalog.pg_statistic
                      WHERE starelid = '{index}'::regclass"
                ),
                &[],
            )
            .unwrap()
            .get(0)
    };
    assert_eq!(sampled(&mut owner, "core_memory.chunk_text_search_idx"), 0);
    // The same index without that setting would keep the words: this test
    // can tell.
    owner
        .batch_execute(
            "CREATE INDEX probe_idx ON core_memory.chunk_text USING gin (
                 pg_catalog.to_tsvector('pg_catalog.english'::pg_catalog.regconfig,
                                        pg_catalog.\"left\"(text, 100000)));
             ANALYZE core_memory.chunk_text;",
        )
        .unwrap();
    assert!(sampled(&mut owner, "core_memory.probe_idx") > 0);
    owner
        .batch_execute("DROP INDEX core_memory.probe_idx")
        .unwrap();
}

#[test]
fn the_engine_role_recalls_and_the_search_uses_the_index() {
    let mut w = world();
    bead(&mut w, &said("1", 0, "Codename hyacinth is ready."));
    let mut admin = w.db.connect();
    admin
        .batch_execute(
            "DROP ROLE IF EXISTS cm_t_recall_engine;
             CREATE ROLE cm_t_recall_engine LOGIN PASSWORD 'synthetic';
             GRANT core_memory_engine TO cm_t_recall_engine;",
        )
        .unwrap();
    let (scheme, rest) = w.db.url.split_once("://").unwrap();
    let (_, host) = rest.split_once('@').unwrap();
    let url = format!("{scheme}://cm_t_recall_engine:synthetic@{host}");
    let mut store = PostgresStore::new(&ConnectConfig::new(url)).unwrap();
    let answer = store.recall(&ask("hyacinth")).unwrap();
    assert_eq!(answer.beads.len(), 1);

    // The search's expression is the index's, so the index can find the
    // words instead of every chunk being read. On a table this small the
    // planner would rather read it all, so it is told not to.
    admin.batch_execute("SET enable_seqscan = off").unwrap();
    let plan: Vec<String> = admin
        .query(
            "EXPLAIN SELECT t.chunk_id FROM core_memory.chunk_text AS t
              WHERE pg_catalog.to_tsvector('pg_catalog.english'::pg_catalog.regconfig,
                                           pg_catalog.\"left\"(t.text, 100000))
                    @@ pg_catalog.websearch_to_tsquery('pg_catalog.english'::pg_catalog.regconfig,
                                                       'hyacinth')",
            &[],
        )
        .unwrap()
        .iter()
        .map(|r| r.get(0))
        .collect();
    assert!(
        plan.iter()
            .any(|line| line.contains("chunk_text_search_idx")),
        "{plan:?}"
    );
    drop(store);
    admin
        .batch_execute("DROP OWNED BY cm_t_recall_engine; DROP ROLE cm_t_recall_engine;")
        .unwrap();
}

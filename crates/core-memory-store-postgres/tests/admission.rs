//! Admission against PostgreSQL: identity, typed outcomes and what is stored.

#![cfg(feature = "postgres-tests")]
#![allow(clippy::unwrap_used)]

mod common;

use std::collections::BTreeSet;
use std::sync::Arc;

use common::{CONFIG, T0, World, chat, chat_in, crm, engine_with, events, note};
use core_memory_contract::{
    AdmissionOutcome, Content, EventId, IdKey, RefusalReason, TimeBasis, Timestamp,
};
use core_memory_core::store::{ScopeState, Store};
use core_memory_core::{ManualClock, ScopeGrants};

/// Text a store must keep exactly: JSON escapes, a line separator, a tab,
/// CRLF, an emoji and CJK.
const AWKWARD: &str = "Quotes \"like this\", a back\\slash, a line\u{2028}separator, a tab\there, CRLF\r\nline endings, \u{1F680} and \u{65E5}\u{672C}\u{8A9E}.";

fn refusal(outcome: AdmissionOutcome) -> (RefusalReason, String) {
    match outcome {
        AdmissionOutcome::Refused { reason, problems } => (
            reason,
            problems
                .iter()
                .map(|p| format!("{}: {}", p.field, p.problem))
                .collect::<Vec<_>>()
                .join("; "),
        ),
        other => panic!("not refused: {other:?}"),
    }
}

#[test]
fn accepts_then_replays_without_writing_again() {
    let mut w = World::new();
    let event = chat("1", "We decided to ship on Friday.");
    let (event_id, chunk_ids) = w.accept(&event);
    assert_eq!(chunk_ids.len(), 1);
    assert_eq!(
        w.admit(&event),
        AdmissionOutcome::Replay {
            event_id,
            chunk_ids,
            checkpoint: None
        }
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_text"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.beads"), 1);
}

#[test]
fn ids_are_keyed_by_the_workspace() {
    let mut w = World::new();
    let event = chat("1", "hello");
    let (event_id, _) = w.accept(&event);
    // The workspace key in `World` is 32 bytes of 0x07.
    let id_key = IdKey::from_bytes([7; 32]);
    assert_eq!(event_id, EventId::derive(&id_key, &event.key()));
    assert_ne!(
        event_id,
        EventId::derive(&IdKey::from_bytes([8; 32]), &event.key())
    );
}

#[test]
fn stored_text_is_exact() {
    let mut w = World::new();
    let (event_id, chunk_ids) = w.accept(&chat("1", AWKWARD));
    let exported = w.export();
    assert_eq!(exported.len(), 1);
    let e = &exported[0];
    assert_eq!(e.event.event_id, event_id);
    assert_eq!(e.event.workspace, "w1");
    assert_eq!(e.event.key.external_id, "1");
    assert_eq!(e.event.details.structure.thread.as_deref(), Some("C1/1"));
    assert_eq!(e.event.details.actor.as_ref().unwrap().id, "U1");
    assert_eq!(e.event.details.source_system, "slack");
    assert_eq!(e.event.captured_at, Timestamp::from_unix_millis(T0));
    assert_eq!(e.event.time_basis, TimeBasis::Source);
    assert_eq!(
        e.event.time_at,
        Timestamp::parse("2026-10-26T14:00:00Z").unwrap()
    );
    assert_eq!(e.chunks.len(), 1);
    let chunk = &e.chunks[0];
    assert_eq!(chunk.chunk_id, chunk_ids[0]);
    assert_eq!(chunk.text, AWKWARD);
    assert_eq!(chunk.byte_end as usize, AWKWARD.len());
    assert_eq!(chunk.char_end as usize, AWKWARD.chars().count());
    assert_eq!(chunk.facets.lengths.bytes as usize, AWKWARD.len());
}

#[test]
fn multi_chunk_events_partition_the_content() {
    let mut w = World::new();
    let text = "First paragraph, short.\n\nSecond paragraph, also short.\n\nThird one closes it.";
    let (_, chunk_ids) = w.accept(&note("n1", text));
    assert!(chunk_ids.len() >= 2, "{chunk_ids:?}");
    let e = w.export().pop().unwrap();
    assert_eq!(e.event.chunk_count as usize, chunk_ids.len());
    let rebuilt: String = e.chunks.iter().map(|c| c.text.as_str()).collect();
    assert_eq!(rebuilt, text, "chunks rebuild the source byte for byte");
    let mut expected_start = 0;
    for (i, c) in e.chunks.iter().enumerate() {
        assert_eq!(c.ordinal as usize, i);
        assert_eq!(c.byte_start, expected_start);
        assert_eq!(&text[c.byte_start as usize..c.byte_end as usize], c.text);
        expected_start = c.byte_end;
    }
    // Every chunk of a message is a bead with the chunk's own ID.
    assert_eq!(
        w.count(
            "SELECT count(*) FROM core_memory.beads AS b
               JOIN core_memory.chunks AS c ON c.chunk_id = b.bead_id AND b.chunk_id = c.chunk_id
              WHERE b.kind = 'chunk' AND b.state = 'live' AND b.created_by = 'engine'"
        ) as usize,
        chunk_ids.len()
    );
}

#[test]
fn json_records_are_stored_in_canonical_form_with_their_facets() {
    let mut w = World::new();
    w.accept(&crm("opp-1", "closed_won", 48_000));
    let e = w.export().pop().unwrap();
    assert_eq!(
        e.chunks[0].text,
        r#"{"Amount":48000,"Id":"006AAAAAAAAAAAA","StageName":"closed_won"}"#
    );
    assert_eq!(e.chunks[0].facets.identifiers[0].kind, "opportunity");
    assert_eq!(e.chunks[0].facets.identifiers[0].value, "006AAAAAAAAAAAA");
    let record = e.event.details.structure.record.as_ref().unwrap();
    assert_eq!(record.action, "closed_won");
    assert_eq!(e.event.source_profile.id, "acme.crm");
    assert_eq!(e.event.time_basis, TimeBasis::Captured);
    assert_eq!(e.event.time_at, e.event.captured_at);
}

#[test]
fn changed_content_under_the_same_key_is_a_conflict() {
    let mut w = World::new();
    let (event_id, _) = w.accept(&chat("1", "We ship Friday."));
    match w.admit(&chat("1", "We ship Monday.")) {
        AdmissionOutcome::IdentityConflict {
            event_id: stored,
            stored_hash,
            offered_hash,
        } => {
            assert_eq!(stored, event_id);
            assert_ne!(stored_hash, offered_hash);
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(w.export()[0].chunks[0].text, "We ship Friday.");
}

#[test]
fn metadata_changes_are_covered_by_the_identity_hash() {
    let mut w = World::new();
    w.accept(&chat("1", "Same text."));
    let mut moved = chat("1", "Same text.");
    moved.structure.thread = Some("C2/9".into());
    assert!(matches!(
        w.admit(&moved),
        AdmissionOutcome::IdentityConflict { .. }
    ));
    // The profile and parser version are not part of the identity.
    let mut reparsed = chat("1", "Same text.");
    reparsed.parser_contract_version = "slack-export/2".into();
    assert!(matches!(
        w.admit(&reparsed),
        AdmissionOutcome::Replay { .. }
    ));
}

#[test]
fn editable_sources_get_a_content_derived_revision() {
    let mut w = World::new();
    let (first, _) = w.accept(&crm("opp-1", "open", 100));
    let edited = crm("opp-1", "closed_won", 100);
    let (second, revision) = match w.admit(&edited) {
        AdmissionOutcome::Accepted {
            event_id,
            assigned_revision: Some(revision),
            ..
        } => (event_id, revision),
        other => panic!("{other:?}"),
    };
    assert_ne!(first, second);
    assert!(revision.starts_with("cm-"), "{revision}");
    // The same edit again is a replay of the revision, not a third event.
    assert!(matches!(
        w.admit(&edited),
        AdmissionOutcome::Replay { event_id, .. } if event_id == second
    ));
    let exported = w.export();
    assert_eq!(exported.len(), 2);
    assert!(!exported[0].event.engine_assigned_revision);
    assert!(exported[1].event.engine_assigned_revision);
    assert_eq!(exported[1].event.key.revision.as_deref(), Some(&*revision));
    // A source revision is never rewritten: with one given, it is a conflict.
    let mut versioned = crm("opp-2", "open", 1);
    versioned.revision = Some("7".into());
    w.accept(&versioned);
    let mut changed = crm("opp-2", "closed_lost", 1);
    changed.revision = Some("7".into());
    assert!(matches!(
        w.admit(&changed),
        AdmissionOutcome::IdentityConflict { .. }
    ));
}

#[test]
fn the_caller_needs_a_grant_and_the_scope_must_be_enrolled() {
    let mut w = World::new();
    let grants = ScopeGrants::Only(BTreeSet::from(["beta".to_string()]));
    let denied = w
        .engine
        .admit(&mut w.store, &grants, &chat("1", "hi"))
        .unwrap();
    assert_eq!(refusal(denied).0, RefusalReason::ScopeDenied);
    let (reason, problem) = refusal(w.admit(&chat_in("beta", "1", "hi")));
    assert_eq!(reason, RefusalReason::ScopeDenied);
    assert!(problem.contains("not enrolled"), "{problem}");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 0);
}

#[test]
fn paused_and_revoked_scopes_admit_nothing() {
    let mut w = World::new();
    w.accept(&chat("1", "before"));
    for (state, word) in [
        (ScopeState::Paused, "paused"),
        (ScopeState::Revoked, "revoked"),
    ] {
        w.store.set_scope_state("acme", state).unwrap();
        let (reason, problem) = refusal(w.admit(&chat("2", "during")));
        assert_eq!(reason, RefusalReason::ScopeNotActive);
        assert!(problem.contains(word), "{problem}");
        // Even a replay is refused: nothing is confirmed for an inactive scope.
        assert_eq!(
            refusal(w.admit(&chat("1", "before"))).0,
            RefusalReason::ScopeNotActive
        );
        if state == ScopeState::Paused {
            w.store.set_scope_state("acme", ScopeState::Active).unwrap();
            w.accept(&chat("3", "resumed"));
        }
    }
    // A revoked scope comes back only by enrolling it again.
    assert!(
        w.store
            .set_scope_state("acme", ScopeState::Active)
            .unwrap_err()
            .to_string()
            .contains("enroll it again")
    );
    w.store.enroll_scope("acme", "w1").unwrap();
    w.accept(&chat("4", "reconnected"));
    assert_eq!(w.export().len(), 3);
}

#[test]
fn a_revocation_in_progress_is_never_overtaken_by_a_write() {
    let mut w = World::new();
    let mut owner = w.db.connect();
    let mut revoking = owner.transaction().unwrap();
    revoking
        .execute("SELECT core_memory.set_scope_state('acme', 'revoked')", &[])
        .unwrap();
    // The revocation holds the scope row. An admission must wait for it.
    let writer = std::thread::spawn(move || {
        let outcome = w.admit(&chat("1", "racing the revocation"));
        (w, outcome)
    });
    std::thread::sleep(std::time::Duration::from_millis(300));
    assert!(!writer.is_finished(), "the admission did not wait");
    revoking.commit().unwrap();
    let (w, outcome) = writer.join().unwrap();
    assert_eq!(refusal(outcome).0, RefusalReason::ScopeNotActive);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 0);
}

#[test]
fn concurrent_admissions_of_one_event_store_it_once() {
    let w = World::new();
    let clock = Arc::new(ManualClock::at_unix_millis(T0));
    let handles: Vec<_> = (0..8)
        .map(|_| {
            let mut store = w.db.store();
            let engine = engine_with(CONFIG, &clock);
            std::thread::spawn(move || {
                engine
                    .admit(&mut store, &ScopeGrants::All, &chat("1", "once"))
                    .unwrap()
            })
        })
        .collect();
    let outcomes: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    let accepted = outcomes
        .iter()
        .filter(|o| matches!(o, AdmissionOutcome::Accepted { .. }))
        .count();
    let replays = outcomes
        .iter()
        .filter(|o| matches!(o, AdmissionOutcome::Replay { .. }))
        .count();
    assert_eq!((accepted, replays), (1, 7), "{outcomes:?}");
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_text"), 1);
}

#[test]
fn profile_and_content_problems_are_typed() {
    let mut w = World::new();
    let mut unknown = chat("1", "hi");
    unknown.profile_id = "acme.nope".into();
    assert_eq!(refusal(w.admit(&unknown)).0, RefusalReason::InvalidProfile);

    let mut wrong_kind = chat("2", "hi");
    wrong_kind.profile_id = "acme.crm".into();
    assert_eq!(refusal(w.admit(&wrong_kind)).0, RefusalReason::Malformed);

    assert_eq!(
        refusal(w.admit(&chat("3", "  \n\t "))).0,
        RefusalReason::Malformed
    );

    let mut blank = chat("4", "hi");
    blank.external_id.clear();
    assert_eq!(refusal(w.admit(&blank)).0, RefusalReason::Malformed);

    let mut duplicate_keys = crm("5", "open", 1);
    duplicate_keys.content =
        serde_json::from_str::<Content>(r#"{"json": {"a": 1, "a": 2}}"#).unwrap();
    assert_eq!(
        refusal(w.admit(&duplicate_keys)).0,
        RefusalReason::Malformed
    );

    let mut inexact = crm("6", "open", 1);
    inexact.content =
        serde_json::from_str::<Content>(r#"{"json": {"n": 9007199254740993}}"#).unwrap();
    assert_eq!(refusal(w.admit(&inexact)).0, RefusalReason::Malformed);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 0);
}

#[test]
fn content_the_store_cannot_hold_exactly_is_refused() {
    let mut w = World::new();
    let (reason, problem) = refusal(w.admit(&chat("1", "a NUL \0 inside")));
    assert_eq!(reason, RefusalReason::Malformed);
    assert!(problem.contains("U+0000"), "{problem}");
    let mut in_metadata = chat("2", "clean text");
    in_metadata
        .attributes
        .insert("channel".into(), "gen\0eral".into());
    let (reason, problem) = refusal(w.admit(&in_metadata));
    assert_eq!(reason, RefusalReason::Malformed);
    assert!(problem.starts_with("event:"), "{problem}");
    // The six characters backslash-u-0-0-0-0 are ordinary text.
    w.accept(&chat("3", r"the escape \u0000 written out"));
    // Canonical JSON writes a NUL inside a string as that escape, so a JSON
    // record that holds one is stored exactly.
    let mut in_json = crm("4", "open", 1);
    in_json.content = serde_json::from_str::<Content>(r#"{"json": {"note": "x\u0000y"}}"#).unwrap();
    w.accept(&in_json);
    let exported = w.export();
    assert_eq!(exported[0].chunks[0].text, r"the escape \u0000 written out");
    assert_eq!(exported[1].chunks[0].text, r#"{"note":"x\u0000y"}"#);
}

#[test]
fn oversize_events_are_refused() {
    let mut w = World::new();
    let big = note("n1", &"word ".repeat(60));
    assert_eq!(refusal(w.admit(&big)).0, RefusalReason::Oversize);
    let mut record = crm("opp-1", "open", 1);
    record.content = serde_json::from_str::<Content>(&format!(
        r#"{{"json": {{"blob": "{}"}}}}"#,
        "x".repeat(5000)
    ))
    .unwrap();
    assert_eq!(refusal(w.admit(&record)).0, RefusalReason::Oversize);
}

#[test]
fn events_keep_their_source_time_or_fall_back_to_admission_time() {
    let mut w = World::new();
    let mut by_day = chat("day", "sometime that day");
    by_day.occurred_at = serde_json::from_value(
        serde_json::json!({"at": "2026-10-20T00:00:00Z", "precision": "day"}),
    )
    .unwrap();
    let mut unparsed = chat("raw", "the source's clock was unreadable");
    unparsed.occurred_at =
        serde_json::from_value(serde_json::json!({"precision": "unknown", "raw": "last Tuesday"}))
            .unwrap();
    w.accept(&by_day);
    w.clock.advance_millis(5_000);
    w.accept(&unparsed);
    let exported = w.export();
    assert_eq!(exported[0].event.time_basis, TimeBasis::SourceBucket);
    assert_eq!(
        exported[0].event.time_at,
        Timestamp::parse("2026-10-20T00:00:00Z").unwrap()
    );
    assert_eq!(exported[1].event.time_basis, TimeBasis::Captured);
    assert_eq!(
        exported[1].event.time_at,
        Timestamp::from_unix_millis(T0 + 5_000)
    );
    // The source's own words are kept even when they do not parse.
    assert_eq!(
        exported[1]
            .event
            .details
            .occurred_at
            .as_ref()
            .unwrap()
            .raw
            .as_deref(),
        Some("last Tuesday")
    );
    assert!(exported[0].event.seq < exported[1].event.seq);
}

#[test]
fn changed_profile_contents_need_a_new_revision() {
    let mut w = World::new();
    w.engine.pin_profiles(&mut w.store).unwrap();
    w.engine.pin_profiles(&mut w.store).unwrap();
    let changed = CONFIG.replace("max = 40 }", "max = 60 }");
    let other = engine_with(&changed, &w.clock);
    let err = other.pin_profiles(&mut w.store).unwrap_err().to_string();
    assert!(err.contains("bump its rev"), "{err}");
    let bumped = changed.replace(
        r#"chunker = { id = "text.structural", rev = 1, max = 60 }"#,
        r#"chunker = { id = "text.structural", rev = 2, max = 60 }"#,
    );
    // The profile itself changed too, so it needs its own new revision.
    let err = engine_with(&bumped, &w.clock)
        .pin_profiles(&mut w.store)
        .unwrap_err()
        .to_string();
    assert!(err.contains("source_profile"), "{err}");
    let both = bumped.replace(
        "id = \"acme.notes\"\nrev = 1",
        "id = \"acme.notes\"\nrev = 2",
    );
    engine_with(&both, &w.clock)
        .pin_profiles(&mut w.store)
        .unwrap();
}

#[test]
fn export_pages_in_admission_order_and_filters_by_scope() {
    let mut w = World::new();
    w.store.enroll_scope("beta", "w1").unwrap();
    for i in 0..5 {
        w.accept(&chat(&format!("a{i}"), "acme text"));
        w.accept(&chat_in("beta", &format!("b{i}"), "beta text"));
    }
    let first = events(w.store.export("w1", 0, 4, None).unwrap());
    assert_eq!(first.len(), 4);
    let rest = events(
        w.store
            .export("w1", first.last().unwrap().event.seq, 100, None)
            .unwrap(),
    );
    assert_eq!(rest.len(), 6);
    let ids: Vec<_> = first
        .iter()
        .chain(&rest)
        .map(|e| e.event.key.external_id.clone())
        .collect();
    assert_eq!(
        ids,
        ["a0", "b0", "a1", "b1", "a2", "b2", "a3", "b3", "a4", "b4"]
    );
    let beta = events(w.store.export("w1", 0, 100, Some("beta")).unwrap());
    assert_eq!(beta.len(), 5);
    assert!(beta.iter().all(|e| e.event.key.scope == "beta"));
    assert!(w.store.export("w2", 0, 100, None).unwrap().is_empty());
}

// ---- races between admissions and erasures of one record ----

/// Starts each of `events` on its own connection while another session
/// holds workspace `w1`, lets them all reach the store, then releases the
/// workspace. `held` is the SQL the other session runs first and commits
/// after. Returns the outcomes in the order of `events`.
fn admit_while_the_workspace_is_held(
    w: &World,
    held: &str,
    events: &[core_memory_contract::SourceEvent],
) -> Vec<AdmissionOutcome> {
    let mut holder = w.db.connect();
    holder.batch_execute(&format!("BEGIN; {held}")).unwrap();
    let (db, engine) = (&w.db, &w.engine);
    std::thread::scope(|s| {
        let waiting: Vec<_> = events
            .iter()
            .map(|event| {
                s.spawn(move || {
                    let mut store = db.store();
                    engine.admit(&mut store, &ScopeGrants::All, event).unwrap()
                })
            })
            .collect();
        std::thread::sleep(std::time::Duration::from_millis(400));
        assert!(
            waiting.iter().all(|w| !w.is_finished()),
            "admissions wait for the workspace"
        );
        holder.batch_execute("COMMIT").unwrap();
        waiting.into_iter().map(|w| w.join().unwrap()).collect()
    })
}

#[test]
fn two_first_versions_of_an_editable_record_are_both_stored() {
    let mut w = World::new();
    // Both see nothing stored under the key, and then one commits first.
    let versions = [crm("opp-1", "open", 100), crm("opp-1", "closed_won", 100)];
    let outcomes =
        admit_while_the_workspace_is_held(&w, "SELECT core_memory.lock_workspace('w1')", &versions);
    let revisions: Vec<bool> = outcomes
        .iter()
        .map(|outcome| match outcome {
            AdmissionOutcome::Accepted {
                assigned_revision, ..
            } => assigned_revision.is_some(),
            other => panic!("{other:?}"),
        })
        .collect();
    assert_eq!(
        revisions.iter().filter(|is_revision| **is_revision).count(),
        1,
        "one is the record's first version and the other a revision of it: {outcomes:?}"
    );
    assert_eq!(w.export().len(), 2);
    // Each is a replay from now on, whichever order they landed in.
    for version in &versions {
        assert!(matches!(w.admit(version), AdmissionOutcome::Replay { .. }));
    }
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 2);
}

#[test]
fn an_edit_is_not_stored_after_its_record_is_erased() {
    let mut w = World::new();
    let (record, _) = w.accept(&crm("opp-1", "open", 100));
    // The erase is not committed when the edit checks its record, so the
    // edit sees the record live and goes on to store a revision of it.
    let outcomes = admit_while_the_workspace_is_held(
        &w,
        &format!(
            "SELECT core_memory.erase_event('{}', 'user_request', 'owner')",
            record.0
        ),
        &[crm("opp-1", "closed_won", 100)],
    );
    assert_eq!(refusal(outcomes[0].clone()).0, RefusalReason::Tombstoned);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 1);
    assert_eq!(w.count("SELECT count(*) FROM core_memory.chunk_text"), 0);
    // Nothing was numbered for the edit that was turned away.
    assert_eq!(
        w.count("SELECT last_seq FROM core_memory.workspace_seq WHERE workspace_id = 'w1'"),
        3
    );
}

// ---- limits and ordering ----

#[test]
fn the_deepest_metadata_an_event_may_carry_reads_back() {
    let mut w = World::new();
    let event = |external_id: &str, levels: usize| {
        let nested = (0..levels).fold(serde_json::json!(0), |inner, _| serde_json::json!([inner]));
        let mut json = serde_json::to_value(chat(external_id, "ordinary text")).unwrap();
        json["x_deep"] = nested;
        serde_json::from_value::<core_memory_contract::SourceEvent>(json)
    };
    // One level more is refused when the event is read, not found out
    // when the workspace is exported.
    let err = event("too-deep", 64).unwrap_err();
    assert!(err.to_string().contains("nested more than 64"), "{err}");
    let (event_id, _) = w.accept(&event("deep", 63).unwrap());

    let exported = w.export();
    assert_eq!(exported.len(), 1);
    assert_eq!(exported[0].event.event_id, event_id);
    assert!(exported[0].event.details.extra.contains_key("x_deep"));
    // The record a consumer receives parses with an ordinary JSON reader.
    let line = serde_json::to_string(&core_memory_contract::Record::event(
        exported[0].event.clone(),
    ))
    .unwrap();
    serde_json::from_str::<serde_json::Value>(&line).unwrap();
    serde_json::from_str::<core_memory_contract::Record>(&line).unwrap();

    // An event built in code was never read, and is held to the same
    // limits when it is admitted: stored, either of these would stop the
    // export of the whole workspace or come back as a replay of another.
    let built = |external_id: &str, value: serde_json::Value| {
        let mut event = chat(external_id, "ordinary text");
        event.extra.insert("x_built".into(), value);
        event
    };
    let nested = |levels: usize| {
        (0..levels).fold(serde_json::json!(0), |inner, _| serde_json::json!([inner]))
    };
    for (external_id, value, says) in [
        ("built-deep", nested(64), "nested more than 64"),
        ("built-very-deep", nested(126), "nested more than 64"),
        (
            "built-inexact",
            serde_json::json!(9_007_199_254_740_993_u64),
            "cannot be represented exactly",
        ),
    ] {
        let (reason, problem) = refusal(w.admit(&built(external_id, value)));
        assert_eq!(reason, RefusalReason::Malformed, "{external_id}");
        assert!(
            problem.starts_with("x_built: ") && problem.contains(says),
            "{external_id}: {problem}"
        );
    }
    w.accept(&built("built", nested(63)));
    assert_eq!(w.export().len(), 2);
}

#[test]
fn metadata_is_bounded_apart_from_content() {
    let mut w = World::with_config(&format!("{CONFIG}\n[limits]\nmax_metadata_bytes = 600\n"));
    w.accept(&chat("small", "ordinary metadata"));
    // 300 references of about 30 bytes each: within every per-field limit.
    let mut heavy = chat("heavy", "a short message");
    heavy.structure.references = (0..200)
        .map(|i| core_memory_contract::ObjectRef {
            source_ns: None,
            external_id: format!("message-{i:06}"),
            revision: None,
        })
        .collect();
    let (reason, problem) = refusal(w.admit(&heavy));
    assert_eq!(reason, RefusalReason::Oversize);
    assert!(
        problem.starts_with("event: the event's metadata is"),
        "{problem}"
    );
    assert_eq!(w.count("SELECT count(*) FROM core_memory.events"), 1);
}

#[test]
fn events_of_one_day_are_ordered_by_admission() {
    let mut w = World::new();
    let on_the_day = |id: &str, at: &str| {
        let mut event = chat(id, "sometime that day");
        event.occurred_at =
            serde_json::from_value(serde_json::json!({"at": at, "precision": "day"})).unwrap();
        event
    };
    // The reader stamped the later-admitted one with an earlier hour. A
    // day is all the source said, so the hour decides nothing.
    w.accept(&on_the_day("first", "2026-10-20T18:00:00Z"));
    w.accept(&on_the_day("second", "2026-10-20T09:00:00Z"));
    // A day named in another offset starts when that offset's day starts.
    w.accept(&on_the_day("tokyo", "2026-10-20T08:00:00+09:00"));
    let order: Vec<(String, String)> =
        w.db.connect()
            .query(
                "SELECT external_id, to_char(time_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI')
               FROM core_memory.events ORDER BY time_at, seq",
                &[],
            )
            .unwrap()
            .iter()
            .map(|row| (row.get(0), row.get(1)))
            .collect();
    assert_eq!(
        order,
        [
            ("tokyo".to_string(), "2026-10-19T15:00".to_string()),
            ("first".to_string(), "2026-10-20T00:00".to_string()),
            ("second".to_string(), "2026-10-20T00:00".to_string()),
        ]
    );
    assert!(
        w.export()
            .iter()
            .all(|e| e.event.time_basis == TimeBasis::SourceBucket)
    );
    // What the source wrote is kept as it wrote it.
    assert_eq!(
        w.export()[0].event.details.occurred_at.as_ref().unwrap().at,
        Some(Timestamp::parse("2026-10-20T18:00:00Z").unwrap())
    );
}

#[test]
fn times_at_the_ends_of_the_calendar_are_stored_and_exported() {
    let mut w = World::new();
    for (id, at, precision) in [
        ("earliest", "0000-01-01T00:00:00Z", "second"),
        ("latest", "9999-12-31T23:59:59Z", "second"),
        ("first-year", "0000-06-01T00:00:00+02:00", "year"),
    ] {
        let mut event = chat(id, "an unlikely date");
        event.occurred_at =
            serde_json::from_value(serde_json::json!({"at": at, "precision": precision})).unwrap();
        w.accept(&event);
    }
    let times: Vec<String> = w
        .export()
        .iter()
        .map(|e| e.event.time_at.to_rfc3339())
        .collect();
    assert_eq!(
        times,
        [
            "0000-01-01T00:00:00Z",
            "9999-12-31T23:59:59Z",
            "0000-01-01T00:00:00Z"
        ]
    );
    // A time a day past either end is refused where it is read.
    for at in ["9999-12-31T23:59:59-01:00", "0000-01-01T00:00:00+01:00"] {
        let json = serde_json::json!({"at": at, "precision": "second"});
        assert!(
            serde_json::from_value::<core_memory_contract::OccurredAt>(json).is_err(),
            "{at}"
        );
    }
}

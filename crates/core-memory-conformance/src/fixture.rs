//! The configuration, events and stand-in model every check uses.

use std::collections::BTreeSet;

use core_memory_contract::SourceEvent;
use core_memory_core::model::{FnTransport, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_core::{EngineConfig, ModelTransport, ScopeGrants};
use serde_json::{Value, json};

/// The fixture clock starts here: 2026-10-27T03:33:20Z.
pub const T0: i64 = 1_793_072_000_000;

/// `deadline_ms` in [`CONFIG`].
pub const DEADLINE_MS: i64 = 10_000;

/// The vocabulary is a subset of memoriesQL's governed bead types, under
/// their catalog id, so memoriesQL, the reference system of record, can
/// accept every fixture record.
pub const CONFIG: &str = r#"
[vocabulary]
id = "memoriesql.bead_types"
types = [
  { key = "decision", rev = 1, definition = "A choice someone made." },
  { key = "action", rev = 1, definition = "Work someone did or will do." },
  { key = "outcome", rev = 1, definition = "A result that happened." },
]

[classifier]
binding = "keywords"
batch_size = 4
deadline_ms = 10000
max_attempts = 3
retry_backoff_ms = 1000

[[binding]]
id = "keywords"
provider = "scripted"
endpoint = "local"
model = "keywords-2026-01-01"
max_output_tokens = 1024
scopes = ["acme", "beta"]
budget = { max_requests_per_day = 1000, max_tokens_per_day = 100000000 }

[[profile]]
id = "conformance.notes"
rev = 1
source_type = "document"
content = "text"
max_event_bytes = 16384
chunker = { id = "text.structural", rev = 1, max = 300 }

[[profile]]
id = "conformance.crm"
rev = 1
source_type = "relational"
source_system = "salesforce"
content = "json"
editable = true
types = ["outcome", "action"]
chunker = { id = "json.whole", rev = 1, max = 65536 }

[[profile.rules]]
id = "opportunity-closed"
assign = "outcome"
when = { "record.object_type" = "Opportunity", "record.action" = ["closed_won", "closed_lost"] }
"#;

pub fn config() -> EngineConfig {
    toml::from_str(CONFIG).expect("the fixture config parses")
}

/// The scopes the fixture's caller may write to.
pub fn grants() -> ScopeGrants {
    ScopeGrants::Only(BTreeSet::from(["acme".to_string(), "beta".to_string()]))
}

const NOTES: &str = "Cutover plan for the billing ledger. We decided to move read traffic first, because a failed read is cheap to retry and a failed write is not.

Priya will own the export job and report its runtime every morning until it finishes in under an hour. The current run takes about three hours.

The write cutover waits on two things: the export under an hour, and a rehearsed rollback. Nobody has rehearsed the rollback yet.

Open questions: whether to freeze invoicing during the switch, and who signs off on the reconciliation report.
";

/// Text a sink must carry exactly: JSON escapes, a line separator, a tab,
/// CRLF, an emoji and CJK.
const AWKWARD: &str = "Quotes \"like this\", a back\\slash, a line\u{2028}separator, a tab\there, CRLF\r\nline endings, \u{1F680} and \u{65E5}\u{672C}\u{8A9E}.";

fn event(value: Value) -> SourceEvent {
    serde_json::from_value(value).expect("fixture events are well formed")
}

fn chat(scope: &str, id: &str, text: &str) -> SourceEvent {
    event(json!({
        "scope": scope,
        "source_ns": "chat/conformance",
        "external_id": id,
        "profile_id": "core.chat_transcript",
        "occurred_at": {"at": "2026-10-26T15:00:00Z", "precision": "second"},
        "actor": {"id": "U1", "kind": "human"},
        "media_type": "text/plain",
        "content": {"text": text},
        "structure": {"thread": "general"},
        "parser_contract_version": "conformance/1"
    }))
}

/// The events of the standard run, in admission order. Between them they
/// make a multi-chunk document, a rule-typed JSON record, model-typed
/// chunks of all three outcomes, and records in two scopes.
pub fn events() -> Vec<SourceEvent> {
    vec![
        chat(
            "acme",
            "chat-1",
            "We decided to ship the importer on Friday.",
        ),
        chat(
            "acme",
            "chat-2",
            "Sam will update the runbook before the cutover.",
        ),
        chat("acme", "chat-3", AWKWARD),
        event(json!({
            "scope": "acme",
            "source_ns": "docs/conformance",
            "external_id": "cutover-plan.md",
            "revision": "v1",
            "profile_id": "conformance.notes",
            "media_type": "text/markdown",
            "content": {"text": NOTES},
            "structure": {"document": {"title": "Cutover plan"}},
            "parser_contract_version": "conformance/1"
        })),
        event(json!({
            "scope": "acme",
            "source_ns": "salesforce/conformance",
            "external_id": "006Ak000002XyZaAAB",
            "profile_id": "conformance.crm",
            "media_type": "application/json",
            "content": {"json": {"Id": "006Ak000002XyZa", "StageName": "Closed Won", "Amount": 48000}},
            "structure": {"record": {
                "system": "salesforce",
                "object_type": "Opportunity",
                "object_key": "006Ak000002XyZa",
                "action": "closed_won"
            }},
            "parser_contract_version": "conformance/1"
        })),
        chat(
            "beta",
            "chat-1",
            "We decided to hire two engineers in Lisbon.",
        ),
    ]
}

/// One chat chunk of about 3,500 bytes, far larger as a record than any
/// record of the standard run.
pub fn large_event() -> SourceEvent {
    chat(
        "acme",
        "chat-large",
        &"Long note with nothing to decide. ".repeat(100),
    )
}

/// Events that, after the standard run, must produce no record: replays
/// of every standard event, an identity conflict, and events refused for
/// their scope, profile and size.
pub fn events_that_add_nothing() -> Vec<SourceEvent> {
    let changed = chat("acme", "chat-1", "We decided to ship on Monday instead.");
    let mut unknown_profile = chat("acme", "chat-9", "Hello.");
    unknown_profile.profile_id = "conformance.nope".into();
    let mut oversize = events().swap_remove(3);
    oversize.external_id = "huge.md".into();
    oversize.content = core_memory_contract::Content::Text("x".repeat(20_000));
    let mut events = events();
    events.extend([
        changed,
        chat("gamma", "chat-1", "We decided to leave."),
        unknown_profile,
        oversize,
    ]);
    events
}

/// The text of an event's content as the source sent it: the text itself,
/// or the JSON document.
pub fn source_text(event: &SourceEvent) -> Value {
    match &event.content {
        core_memory_contract::Content::Text(t) => Value::String(t.clone()),
        core_memory_contract::Content::Json(raw) => {
            serde_json::from_str(raw.get()).expect("fixture JSON parses")
        }
    }
}

fn keyword_reply(request: &ModelRequest) -> Result<ModelResponse, TransportError> {
    let user: Value = serde_json::from_str(&request.user).expect("requests are JSON");
    let items: Vec<Value> = user["chunks"]
        .as_array()
        .expect("requests list their chunks")
        .iter()
        .map(|c| {
            let text = c["text"].as_str().unwrap_or_default();
            if text.contains("decided") {
                json!({"chunk": c["chunk"], "type": "decision"})
            } else if text.contains(" will ") {
                json!({"chunk": c["chunk"], "type": "action"})
            } else {
                json!({"chunk": c["chunk"], "abstain": "no_fit"})
            }
        })
        .collect();
    Ok(ModelResponse {
        text: json!({ "items": items }).to_string(),
        usage: Usage {
            input_tokens: 100,
            output_tokens: 20,
        },
    })
}

/// The stand-in model: "decided" is a decision, " will " an action, and
/// anything else gets `unassigned{no_fit}`.
pub fn keywords() -> impl ModelTransport {
    FnTransport(keyword_reply)
}

/// A provider that is down.
pub fn outage() -> impl ModelTransport {
    FnTransport(|_: &ModelRequest| {
        Err(TransportError::Transient {
            message: "the fixture provider is down".into(),
            retry_after_ms: None,
        })
    })
}

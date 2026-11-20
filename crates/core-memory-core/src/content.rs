//! Accepted content bytes and the event-level identity hash (ING-02).

use std::collections::BTreeMap;
use std::fmt;

use core_memory_contract::hash::canonical_json_digest;
use core_memory_contract::json::{self, Integers};
use core_memory_contract::{
    Actor, Content, ContentKind, OccurredAt, Sha256Digest, SourceEvent, Structure, ToolCallDetails,
};
use serde::Serialize;

const EVENT_HASH_DOMAIN: &str = "core-memory/event-hash/v1";

/// The exact bytes the engine accepts for an event's content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AcceptedContent {
    pub kind: ContentKind,
    /// UTF-8 text. For JSON content, the RFC 8785 canonical form. For a
    /// tool call, its arguments in that form, a line feed, and its output.
    pub text: String,
    pub hash: Sha256Digest,
    /// For a tool call: the tool, and where its arguments end in `text`.
    pub tool_call: Option<ToolCallDetails>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentError(pub String);

impl fmt::Display for ContentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A JSON value in RFC 8785 canonical form. It is refused if it has
/// duplicate object keys, integers that would lose precision, or more
/// nesting than an event may hold. `inside` is how many objects of the
/// event enclose the value: the limit on nesting counts from the event,
/// whether the event was read as JSON or built in code.
fn canonical(raw: &str, what: &str, inside: usize) -> Result<String, ContentError> {
    let problem = |e: &dyn fmt::Display| ContentError(format!("{what}: {e}"));
    json::check_inside(raw, Integers::Exact, inside).map_err(|e| problem(&e))?;
    let value: serde_json::Value = serde_json::from_str(raw).map_err(|e| problem(&e))?;
    let bytes = serde_json_canonicalizer::to_vec(&value).map_err(|e| problem(&e))?;
    String::from_utf8(bytes).map_err(|e| problem(&e))
}

/// Produces the accepted content. Text is kept byte for byte. JSON is
/// refused if it has duplicate object keys or integers that would lose
/// precision, then canonicalized. A tool call becomes its canonical
/// arguments, a line feed, and its output byte for byte.
pub fn accept_content(content: &Content) -> Result<AcceptedContent, ContentError> {
    let (text, tool_call) = match content {
        Content::Text(text) => (text.clone(), None),
        // Inside the event and inside its `content`.
        Content::Json(raw) => (canonical(raw.get(), "json content", 2)?, None),
        Content::ToolCall(call) => {
            // One object further in: the call itself.
            let input = canonical(call.input.get(), "tool call input", 3)?;
            let details = ToolCallDetails {
                tool: call.tool.clone(),
                input_bytes: input.len() as u64,
                is_error: call.is_error,
            };
            (format!("{input}\n{}", call.output), Some(details))
        }
    };
    Ok(AcceptedContent {
        kind: content.kind(),
        hash: Sha256Digest::of(text.as_bytes()),
        text,
        tool_call,
    })
}

/// What the identity check covers: the content and the metadata the source
/// declared. Profile, parser version and chunker settings are excluded, so
/// changing them never turns a replay into a conflict.
#[derive(Serialize)]
struct EventHashView<'a> {
    content_kind: ContentKind,
    content_hash: &'a Sha256Digest,
    /// Left out for other kinds of content, so their hashes are unchanged.
    #[serde(skip_serializing_if = "Option::is_none")]
    tool_call: &'a Option<ToolCallDetails>,
    media_type: &'a str,
    occurred_at: &'a Option<OccurredAt>,
    actor: &'a Option<Actor>,
    structure: &'a Structure,
    attributes: &'a BTreeMap<String, String>,
    extra: &'a BTreeMap<String, serde_json::Value>,
}

pub fn event_hash(
    event: &SourceEvent,
    content: &AcceptedContent,
) -> Result<Sha256Digest, serde_json::Error> {
    canonical_json_digest(
        EVENT_HASH_DOMAIN,
        &EventHashView {
            content_kind: content.kind,
            content_hash: &content.hash,
            tool_call: &content.tool_call,
            media_type: &event.media_type,
            occurred_at: &event.occurred_at,
            actor: &event.actor,
            structure: &event.structure,
            attributes: &event.attributes,
            extra: &event.extra,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::value::RawValue;

    fn json(s: &str) -> Content {
        Content::Json(RawValue::from_string(s.to_string()).unwrap())
    }

    fn tool_call(input: &str, output: &str) -> Content {
        Content::ToolCall(core_memory_contract::ToolCall {
            tool: "Bash".into(),
            input: RawValue::from_string(input.to_string()).unwrap(),
            output: output.into(),
            is_error: None,
        })
    }

    #[test]
    fn a_tool_call_is_its_canonical_arguments_then_its_output() {
        let accepted = accept_content(&tool_call(
            r#"{"timeout": 60, "command": "ls"}"#,
            "a.txt\nb.txt\n",
        ))
        .unwrap();
        assert_eq!(accepted.kind, ContentKind::ToolCall);
        assert_eq!(
            accepted.text,
            "{\"command\":\"ls\",\"timeout\":60}\na.txt\nb.txt\n"
        );
        // The details say where the arguments end, so the two parts can be
        // told apart again.
        let details = accepted.tool_call.clone().unwrap();
        let at = details.input_bytes as usize;
        assert_eq!(&accepted.text[..at], r#"{"command":"ls","timeout":60}"#);
        assert_eq!(&accepted.text[at + 1..], "a.txt\nb.txt\n");
        assert_eq!(details.tool, "Bash");

        // Arguments written another way are the same content. Output is
        // never normalized.
        let reordered = accept_content(&tool_call(
            r#"{"command":"ls","timeout":60}"#,
            "a.txt\nb.txt\n",
        ))
        .unwrap();
        assert_eq!(reordered.hash, accepted.hash);
        let other_output = accept_content(&tool_call(
            r#"{"command":"ls","timeout":60}"#,
            "a.txt\r\nb.txt\n",
        ))
        .unwrap();
        assert_ne!(other_output.hash, accepted.hash);
        // An empty output still leaves text to store.
        assert_eq!(
            accept_content(&tool_call("null", "")).unwrap().text,
            "null\n"
        );
        // Arguments that could be read two ways are refused.
        let err = accept_content(&tool_call(r#"{"a": 1, "a": 2}"#, "")).unwrap_err();
        assert!(err.0.starts_with("tool call input: "), "{err}");
        assert!(accept_content(&tool_call("9007199254740993", "")).is_err());
        // Other kinds of content carry no tool details.
        assert_eq!(accept_content(&json("{}")).unwrap().tool_call, None);
    }

    #[test]
    fn text_is_kept_exactly() {
        let c = accept_content(&Content::Text("  Hello\r\n".into())).unwrap();
        assert_eq!(c.text, "  Hello\r\n");
        assert_eq!(c.hash, Sha256Digest::of(b"  Hello\r\n"));
    }

    #[test]
    fn json_is_canonicalized() {
        let a = accept_content(&json(r#"{ "b": [1, 2.50], "a": "x" }"#)).unwrap();
        let b = accept_content(&json(r#"{"a":"x","b":[1,2.5]}"#)).unwrap();
        assert_eq!(a.text, r#"{"a":"x","b":[1,2.5]}"#);
        assert_eq!(a.hash, b.hash);
    }

    #[test]
    fn duplicate_keys_are_refused_at_any_depth() {
        let e = accept_content(&json(r#"{"a":{"k":1,"k":2}}"#)).unwrap_err();
        assert!(e.0.contains("duplicate key `k`"), "{e}");
    }

    #[test]
    fn nesting_is_counted_from_the_event() {
        use core_memory_contract::json::MAX_DEPTH;
        let nested = |levels: usize| format!("{}0{}", "[".repeat(levels), "]".repeat(levels));
        // What an event read as JSON may hold as content, and no more.
        assert!(accept_content(&json(&nested(MAX_DEPTH - 2))).is_ok());
        let e = accept_content(&json(&nested(MAX_DEPTH - 1))).unwrap_err();
        assert!(e.0.contains("nested more than 64"), "{e}");
    }

    #[test]
    fn inexact_integers_are_refused() {
        assert!(accept_content(&json(r#"{"id": 9007199254740993}"#)).is_err());
        assert!(accept_content(&json(r#"{"id": 9007199254740992}"#)).is_ok());
        assert!(accept_content(&json(r#"{"id": -9007199254740993}"#)).is_err());
        // Larger than u64: parsed as a float, still refused.
        assert!(accept_content(&json(r#"{"id": 18446744073709551617}"#)).is_err());
        assert!(accept_content(&json(r#"{"ratio": 0.25, "big": 1e300}"#)).is_err());
        assert!(accept_content(&json(r#"{"ratio": 0.25, "small": 1.5e-7}"#)).is_ok());
    }
}

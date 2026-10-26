//! Accepted content bytes and the event-level identity hash (ING-02).

use std::collections::{BTreeMap, HashSet};
use std::fmt;

use core_memory_contract::hash::canonical_json_digest;
use core_memory_contract::{
    Actor, Content, ContentKind, OccurredAt, Sha256Digest, SourceEvent, Structure,
};
use serde::Serialize;
use serde::de::{self, Deserializer, MapAccess, SeqAccess, Visitor};

const EVENT_HASH_DOMAIN: &str = "core-memory/event-hash/v1";

/// Largest integer magnitude that JSON canonicalization (RFC 8785, which
/// treats numbers as IEEE doubles) represents exactly: 2^53.
const MAX_EXACT_INTEGER: u64 = 1 << 53;

/// The exact bytes the engine accepts for an event's content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AcceptedContent {
    pub kind: ContentKind,
    /// UTF-8 text. For JSON content, the RFC 8785 canonical form.
    pub text: String,
    pub hash: Sha256Digest,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentError(pub String);

impl fmt::Display for ContentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Produces the accepted content. Text is kept byte for byte. JSON is
/// refused if it has duplicate object keys or integers that would lose
/// precision, then canonicalized.
pub fn accept_content(content: &Content) -> Result<AcceptedContent, ContentError> {
    let text = match content {
        Content::Text(text) => text.clone(),
        Content::Json(raw) => {
            let mut de = serde_json::Deserializer::from_str(raw.get());
            de.deserialize_any(StrictJson)
                .map_err(|e| ContentError(format!("json content: {e}")))?;
            let value: serde_json::Value = serde_json::from_str(raw.get())
                .map_err(|e| ContentError(format!("json content: {e}")))?;
            let bytes = serde_json_canonicalizer::to_vec(&value)
                .map_err(|e| ContentError(format!("json content: {e}")))?;
            String::from_utf8(bytes).map_err(|e| ContentError(format!("json content: {e}")))?
        }
    };
    Ok(AcceptedContent {
        kind: content.kind(),
        hash: Sha256Digest::of(text.as_bytes()),
        text,
    })
}

/// What the identity check covers: the content and the metadata the source
/// declared. Profile, parser version and chunker settings are excluded, so
/// changing them never turns a replay into a conflict.
#[derive(Serialize)]
struct EventHashView<'a> {
    content_kind: ContentKind,
    content_hash: &'a Sha256Digest,
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
            media_type: &event.media_type,
            occurred_at: &event.occurred_at,
            actor: &event.actor,
            structure: &event.structure,
            attributes: &event.attributes,
            extra: &event.extra,
        },
    )
}

/// Walks a JSON document, refusing duplicate keys and inexact integers.
struct StrictJson;

impl<'de> Visitor<'de> for StrictJson {
    type Value = ();

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("any JSON value")
    }

    fn visit_bool<E>(self, _: bool) -> Result<(), E> {
        Ok(())
    }

    fn visit_i64<E: de::Error>(self, v: i64) -> Result<(), E> {
        if v.unsigned_abs() > MAX_EXACT_INTEGER {
            return Err(E::custom(format!(
                "integer {v} cannot be represented exactly; send it as a string"
            )));
        }
        Ok(())
    }

    fn visit_u64<E: de::Error>(self, v: u64) -> Result<(), E> {
        if v > MAX_EXACT_INTEGER {
            return Err(E::custom(format!(
                "integer {v} cannot be represented exactly; send it as a string"
            )));
        }
        Ok(())
    }

    fn visit_f64<E>(self, _: f64) -> Result<(), E> {
        Ok(())
    }

    fn visit_str<E>(self, _: &str) -> Result<(), E> {
        Ok(())
    }

    fn visit_unit<E>(self) -> Result<(), E> {
        Ok(())
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<(), A::Error> {
        while seq.next_element_seed(StrictSeed)?.is_some() {}
        Ok(())
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
        let mut seen = HashSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !seen.insert(key.clone()) {
                return Err(de::Error::custom(format!("duplicate key `{key}`")));
            }
            map.next_value_seed(StrictSeed)?;
        }
        Ok(())
    }
}

struct StrictSeed;

impl<'de> de::DeserializeSeed<'de> for StrictSeed {
    type Value = ();

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<(), D::Error> {
        deserializer.deserialize_any(StrictJson)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::value::RawValue;

    fn json(s: &str) -> Content {
        Content::Json(RawValue::from_string(s.to_string()).unwrap())
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
    fn inexact_integers_are_refused() {
        assert!(accept_content(&json(r#"{"id": 9007199254740993}"#)).is_err());
        assert!(accept_content(&json(r#"{"id": 9007199254740992}"#)).is_ok());
        assert!(accept_content(&json(r#"{"id": -9007199254740993}"#)).is_err());
    }
}

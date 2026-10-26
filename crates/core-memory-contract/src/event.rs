//! The input contract: SourceEvent and its identity.
//!
//! Field meanings follow memoriesQL's `SourceEventInput` and
//! `SourceUnitInput` so the memoriesQL sink can map them without inventing
//! values.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;

use crate::hash::FieldHasher;
use crate::timestamp::Timestamp;

/// Maximum byte length of identifier-like fields (scope, IDs, keys).
pub const MAX_IDENTIFIER_BYTES: usize = 512;
/// Maximum byte length of an attribute value (memoriesQL `CanonicalAttribute`).
pub const MAX_ATTRIBUTE_VALUE_BYTES: usize = 512;
/// Maximum number of attributes on one event.
pub const MAX_ATTRIBUTES: usize = 64;
/// Maximum number of references or correlation keys on one event.
pub const MAX_STRUCTURE_ITEMS: usize = 256;

/// Identity of one occurrence: `(scope, source_ns, external_id, revision)`.
///
/// It is never positional and never derived from ingest order (ING-01). The
/// same text at a different position in a source is a different event.
#[derive(
    Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct EventKey {
    pub scope: String,
    pub source_ns: String,
    pub external_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision: Option<String>,
}

impl EventKey {
    /// Feeds the key's fields into `hasher` in contract order.
    pub fn hash_into(&self, hasher: &mut FieldHasher) {
        hasher
            .str(&self.scope)
            .str(&self.source_ns)
            .str(&self.external_id)
            .opt_str(self.revision.as_deref());
    }

    /// The same key with a different revision.
    pub fn with_revision(&self, revision: impl Into<String>) -> Self {
        Self {
            revision: Some(revision.into()),
            ..self.clone()
        }
    }
}

/// One thing received from a source: a message, a turn, a CRM change, a
/// parsed document or a log line.
///
/// Deserialization only works from JSON (`serde_json`). It refuses duplicate
/// top-level keys and keeps unknown fields in `extra`.
#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct SourceEvent {
    pub scope: String,
    /// Namespace that makes `external_id` unique, for example `slack/T0123`.
    pub source_ns: String,
    pub external_id: String,
    /// Source-supplied revision. Absent for sources without revisions.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision: Option<String>,
    /// The source profile that admits, chunks, facets and classifies this event.
    pub profile_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occurred_at: Option<OccurredAt>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub actor: Option<Actor>,
    /// Media type of the content, for example `text/plain` or `application/json`.
    pub media_type: String,
    pub content: Content,
    #[serde(default, skip_serializing_if = "Structure::is_empty")]
    pub structure: Structure,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub attributes: BTreeMap<String, String>,
    /// Version of the upstream parser contract that produced this event.
    pub parser_contract_version: String,
    /// Fields this contract does not know. They are kept and delivered
    /// unchanged, and they are covered by the event hash (ING-06).
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Top-level field names of [`SourceEvent`], apart from `extra`.
pub const SOURCE_EVENT_FIELDS: &[&str] = &[
    "scope",
    "source_ns",
    "external_id",
    "revision",
    "profile_id",
    "occurred_at",
    "actor",
    "media_type",
    "content",
    "structure",
    "attributes",
    "parser_contract_version",
];

/// The known fields, parsed strictly. `Box<RawValue>` cannot pass through
/// `#[serde(flatten)]`, so [`SourceEvent`] splits unknown fields off first.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct KnownFields {
    scope: String,
    source_ns: String,
    external_id: String,
    #[serde(default)]
    revision: Option<String>,
    profile_id: String,
    #[serde(default)]
    occurred_at: Option<OccurredAt>,
    #[serde(default)]
    actor: Option<Actor>,
    media_type: String,
    content: Content,
    #[serde(default)]
    structure: Structure,
    #[serde(default)]
    attributes: BTreeMap<String, String>,
    parser_contract_version: String,
}

impl<'de> Deserialize<'de> for SourceEvent {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        use serde::de::Error;

        let entries = deserializer.deserialize_map(UniqueRawEntries)?;
        let mut known = String::from("{");
        let mut extra = BTreeMap::new();
        for (key, value) in entries {
            if SOURCE_EVENT_FIELDS.contains(&key.as_str()) {
                if known.len() > 1 {
                    known.push(',');
                }
                known.push_str(&serde_json::to_string(&key).map_err(D::Error::custom)?);
                known.push(':');
                known.push_str(value.get());
            } else {
                let value = serde_json::from_str(value.get()).map_err(D::Error::custom)?;
                extra.insert(key, value);
            }
        }
        known.push('}');
        let f: KnownFields = serde_json::from_str(&known).map_err(D::Error::custom)?;
        Ok(SourceEvent {
            scope: f.scope,
            source_ns: f.source_ns,
            external_id: f.external_id,
            revision: f.revision,
            profile_id: f.profile_id,
            occurred_at: f.occurred_at,
            actor: f.actor,
            media_type: f.media_type,
            content: f.content,
            structure: f.structure,
            attributes: f.attributes,
            parser_contract_version: f.parser_contract_version,
            extra,
        })
    }
}

/// Reads a JSON object as raw entries, refusing duplicate keys.
struct UniqueRawEntries;

impl<'de> serde::de::Visitor<'de> for UniqueRawEntries {
    type Value = Vec<(String, Box<RawValue>)>;

    fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("a SourceEvent JSON object")
    }

    fn visit_map<A: serde::de::MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
        let mut entries: Vec<(String, Box<RawValue>)> = Vec::new();
        while let Some(key) = map.next_key::<String>()? {
            if entries.iter().any(|(k, _)| *k == key) {
                return Err(serde::de::Error::custom(format!("duplicate key `{key}`")));
            }
            let value: Box<RawValue> = map.next_value()?;
            entries.push((key, value));
        }
        Ok(entries)
    }
}

impl SourceEvent {
    pub fn key(&self) -> EventKey {
        EventKey {
            scope: self.scope.clone(),
            source_ns: self.source_ns.clone(),
            external_id: self.external_id.clone(),
            revision: self.revision.clone(),
        }
    }

    /// Checks field shapes and limits. Returns every problem found.
    pub fn validate(&self) -> Result<(), Vec<FieldError>> {
        let mut errors = Vec::new();
        check_identifier(&mut errors, "scope", &self.scope);
        check_identifier(&mut errors, "source_ns", &self.source_ns);
        check_identifier(&mut errors, "external_id", &self.external_id);
        if let Some(rev) = &self.revision {
            check_identifier(&mut errors, "revision", rev);
        }
        check_identifier(&mut errors, "profile_id", &self.profile_id);
        check_identifier(&mut errors, "media_type", &self.media_type);
        check_identifier(
            &mut errors,
            "parser_contract_version",
            &self.parser_contract_version,
        );
        if let Some(actor) = &self.actor {
            check_identifier(&mut errors, "actor.id", &actor.id);
            check_identifier(&mut errors, "actor.kind", &actor.kind);
            if let Some(role) = &actor.role {
                check_identifier(&mut errors, "actor.role", role);
            }
        }
        if let Some(occurred) = &self.occurred_at {
            occurred.validate(&mut errors);
        }
        self.structure.validate(&mut errors);
        if self.attributes.len() > MAX_ATTRIBUTES {
            errors.push(FieldError::new(
                "attributes",
                format!("at most {MAX_ATTRIBUTES} attributes are allowed"),
            ));
        }
        for (key, value) in &self.attributes {
            if !is_attribute_key(key) {
                errors.push(FieldError::new(
                    format!("attributes.{key}"),
                    "keys must match ^[a-z][a-z0-9_]{0,63}$",
                ));
            }
            if value.len() > MAX_ATTRIBUTE_VALUE_BYTES {
                errors.push(FieldError::new(
                    format!("attributes.{key}"),
                    format!("values are limited to {MAX_ATTRIBUTE_VALUE_BYTES} bytes"),
                ));
            }
        }
        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors)
        }
    }
}

/// The event's content: exact text, or a JSON value.
///
/// Text is kept byte for byte. JSON content is hashed and chunked in its
/// RFC 8785 canonical form, and duplicate object keys are refused.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Content {
    Text(String),
    Json(#[schemars(with = "serde_json::Value")] Box<RawValue>),
}

impl Content {
    pub fn kind(&self) -> ContentKind {
        match self {
            Content::Text(_) => ContentKind::Text,
            Content::Json(_) => ContentKind::Json,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ContentKind {
    Text,
    Json,
}

/// When the event happened in the source, with its precision.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct OccurredAt {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub at: Option<Timestamp>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub end: Option<Timestamp>,
    pub precision: TimePrecision,
    /// The source's own representation, kept when it does not parse.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub raw: Option<String>,
    /// IANA time zone the source reported, if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timezone: Option<String>,
}

impl OccurredAt {
    fn validate(&self, errors: &mut Vec<FieldError>) {
        if self.end.is_some() && self.at.is_none() {
            errors.push(FieldError::new("occurred_at.end", "end requires at"));
        }
        if let (Some(at), Some(end)) = (self.at, self.end)
            && end < at
        {
            errors.push(FieldError::new("occurred_at.end", "end cannot precede at"));
        }
        if self.precision != TimePrecision::Unknown && self.at.is_none() && self.raw.is_none() {
            errors.push(FieldError::new(
                "occurred_at",
                "a known precision requires at or raw",
            ));
        }
        if let Some(raw) = &self.raw {
            check_identifier(errors, "occurred_at.raw", raw);
        }
        if let Some(tz) = &self.timezone {
            check_identifier(errors, "occurred_at.timezone", tz);
        }
    }
}

/// Precision of a source time. Values match memoriesQL `SourceTimePrecision`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TimePrecision {
    Instant,
    Second,
    Minute,
    Hour,
    Day,
    Month,
    Year,
    Interval,
    Unknown,
}

/// Who produced the event.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Actor {
    pub id: String,
    /// For example `human`, `agent` or `system`.
    pub kind: String,
    /// Conversational role, for example `user` or `assistant`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub role: Option<String>,
}

/// Structural fields the source declares. They describe the shape of the
/// source, not its meaning, and never become relationships (FAC-03).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Structure {
    /// Thread or conversation key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thread: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent: Option<ObjectRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reply_to: Option<ObjectRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision_of: Option<ObjectRef>,
    /// Objects the source explicitly references.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub references: Vec<ObjectRef>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub correlation_keys: Vec<CorrelationKey>,
    /// The record a change event is about (CRM, ticketing, ops systems).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub record: Option<RecordObject>,
    /// Position of a parsed document element.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub document: Option<DocumentPosition>,
}

impl Structure {
    pub fn is_empty(&self) -> bool {
        *self == Structure::default()
    }

    fn validate(&self, errors: &mut Vec<FieldError>) {
        for (name, value) in [
            ("structure.thread", &self.thread),
            ("structure.session", &self.session),
        ] {
            if let Some(v) = value {
                check_identifier(errors, name, v);
            }
        }
        for (name, r) in [
            ("structure.parent", &self.parent),
            ("structure.reply_to", &self.reply_to),
            ("structure.revision_of", &self.revision_of),
        ] {
            if let Some(r) = r {
                r.validate(errors, name);
            }
        }
        if self.references.len() > MAX_STRUCTURE_ITEMS {
            errors.push(FieldError::new(
                "structure.references",
                format!("at most {MAX_STRUCTURE_ITEMS} references are allowed"),
            ));
        }
        for r in &self.references {
            r.validate(errors, "structure.references");
        }
        if self.correlation_keys.len() > MAX_STRUCTURE_ITEMS {
            errors.push(FieldError::new(
                "structure.correlation_keys",
                format!("at most {MAX_STRUCTURE_ITEMS} correlation keys are allowed"),
            ));
        }
        for k in &self.correlation_keys {
            for v in [&k.source_system, &k.object_type, &k.key_kind, &k.value] {
                check_identifier(errors, "structure.correlation_keys", v);
            }
        }
        if let Some(record) = &self.record {
            for (name, v) in [
                ("structure.record.system", &record.system),
                ("structure.record.object_type", &record.object_type),
                ("structure.record.object_key", &record.object_key),
                ("structure.record.action", &record.action),
            ] {
                check_identifier(errors, name, v);
            }
            if record.changed_fields.len() > MAX_ATTRIBUTES {
                errors.push(FieldError::new(
                    "structure.record.changed_fields",
                    format!("at most {MAX_ATTRIBUTES} changed fields are allowed"),
                ));
            }
        }
        if let Some(doc) = &self.document {
            if doc.page == Some(0) {
                errors.push(FieldError::new(
                    "structure.document.page",
                    "pages start at 1",
                ));
            }
            if doc.section_path.len() > MAX_STRUCTURE_ITEMS {
                errors.push(FieldError::new(
                    "structure.document.section_path",
                    "section path is too deep",
                ));
            }
        }
    }
}

/// A reference to another source object in the same scope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ObjectRef {
    /// Defaults to the referencing event's `source_ns`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_ns: Option<String>,
    pub external_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision: Option<String>,
}

impl ObjectRef {
    fn validate(&self, errors: &mut Vec<FieldError>, field: &str) {
        check_identifier(errors, field, &self.external_id);
        if let Some(ns) = &self.source_ns {
            check_identifier(errors, field, ns);
        }
        if let Some(rev) = &self.revision {
            check_identifier(errors, field, rev);
        }
    }
}

/// A namespaced key that correlates events across systems, for example
/// `(salesforce, opportunity, id, 006xx)`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, JsonSchema)]
pub struct CorrelationKey {
    pub source_system: String,
    pub object_type: String,
    pub key_kind: String,
    pub value: String,
}

/// The record a change event is about. Mirrors memoriesQL `RecordEventDetail`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecordObject {
    pub system: String,
    pub object_type: String,
    pub object_key: String,
    /// For example `created`, `updated` or `stage_changed`.
    pub action: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effective_at: Option<Timestamp>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub changed_fields: BTreeMap<String, String>,
}

/// Where a parsed document element sits. Mirrors memoriesQL
/// `DocumentSegmentDetail` and `DocumentRevision`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DocumentPosition {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub section_path: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page: Option<u32>,
}

/// One validation problem in a SourceEvent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct FieldError {
    pub field: String,
    pub problem: String,
}

impl FieldError {
    pub fn new(field: impl Into<String>, problem: impl Into<String>) -> Self {
        Self {
            field: field.into(),
            problem: problem.into(),
        }
    }
}

fn check_identifier(errors: &mut Vec<FieldError>, field: &str, value: &str) {
    if value.is_empty() {
        errors.push(FieldError::new(field, "must not be empty"));
    } else if value.len() > MAX_IDENTIFIER_BYTES {
        errors.push(FieldError::new(
            field,
            format!("must be at most {MAX_IDENTIFIER_BYTES} bytes"),
        ));
    } else if value.chars().any(char::is_control) {
        errors.push(FieldError::new(
            field,
            "must not contain control characters",
        ));
    }
}

/// memoriesQL attribute keys: `^[a-z][a-z0-9_]{0,63}$`.
fn is_attribute_key(key: &str) -> bool {
    let bytes = key.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && bytes[0].is_ascii_lowercase()
        && bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event_json() -> &'static str {
        r#"{
            "scope": "acme",
            "source_ns": "slack/T01",
            "external_id": "C1/1730000000.0001",
            "profile_id": "core.chat_transcript",
            "occurred_at": {"at": "2026-10-26T14:00:00Z", "precision": "second"},
            "actor": {"id": "U1", "kind": "human", "role": "user"},
            "media_type": "text/plain",
            "content": {"text": "We decided to ship on Friday."},
            "structure": {"thread": "C1/1730000000.0000"},
            "attributes": {"channel": "general"},
            "parser_contract_version": "slack-export/1",
            "x_custom": {"kept": true}
        }"#
    }

    #[test]
    fn parses_and_keeps_unknown_fields() {
        let event: SourceEvent = serde_json::from_str(event_json()).unwrap();
        assert!(event.validate().is_ok());
        assert_eq!(event.extra["x_custom"], serde_json::json!({"kept": true}));
        let round_trip = serde_json::to_value(&event).unwrap();
        assert_eq!(round_trip["x_custom"], serde_json::json!({"kept": true}));
    }

    #[test]
    fn duplicate_top_level_keys_are_refused() {
        let doubled = event_json().replacen(
            r#""scope": "acme","#,
            r#""scope": "acme", "scope": "evil","#,
            1,
        );
        let err = serde_json::from_str::<SourceEvent>(&doubled).unwrap_err();
        assert!(err.to_string().contains("duplicate key `scope`"), "{err}");
    }

    #[test]
    fn known_field_list_matches_the_schema() {
        let schema = schemars::schema_for!(SourceEvent);
        let props = schema.as_value()["properties"].as_object().unwrap();
        let mut from_schema: Vec<_> = props.keys().map(String::as_str).collect();
        let mut known = SOURCE_EVENT_FIELDS.to_vec();
        from_schema.sort_unstable();
        known.sort_unstable();
        assert_eq!(from_schema, known);
    }

    #[test]
    fn json_content_keeps_raw_text() {
        let event: SourceEvent = serde_json::from_str(
            r#"{"scope":"a","source_ns":"crm","external_id":"1","profile_id":"p",
                "media_type":"application/json","parser_contract_version":"1",
                "content":{"json":{"b": 2, "a": 1}}}"#,
        )
        .unwrap();
        match &event.content {
            Content::Json(raw) => assert_eq!(raw.get(), r#"{"b": 2, "a": 1}"#),
            Content::Text(_) => panic!("expected json content"),
        }
    }

    #[test]
    fn validation_reports_every_problem() {
        let mut event: SourceEvent = serde_json::from_str(event_json()).unwrap();
        event.scope.clear();
        event.attributes.insert("Bad-Key".into(), "v".into());
        let errors = event.validate().unwrap_err();
        let fields: Vec<_> = errors.iter().map(|e| e.field.as_str()).collect();
        assert!(fields.contains(&"scope"));
        assert!(fields.contains(&"attributes.Bad-Key"));
    }

    #[test]
    fn end_before_start_is_refused() {
        let occurred = OccurredAt {
            at: Some(Timestamp::parse("2026-10-26T14:00:00Z").unwrap()),
            end: Some(Timestamp::parse("2026-10-26T13:00:00Z").unwrap()),
            precision: TimePrecision::Interval,
            raw: None,
            timezone: None,
        };
        let mut errors = Vec::new();
        occurred.validate(&mut errors);
        assert_eq!(errors.len(), 1);
    }
}

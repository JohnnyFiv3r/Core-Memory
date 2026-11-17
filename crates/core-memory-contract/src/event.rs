//! The input contract: SourceEvent and its identity.
//!
//! Field meanings follow memoriesQL's `SourceEventInput` and
//! `SourceUnitInput`, so a memoriesQL host can map them without inventing
//! values.

use std::collections::{BTreeMap, HashSet};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;

use crate::hash::{FieldHasher, Sha256Digest};
use crate::json::{self, Integers};
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
/// Deserialization only works from JSON (`serde_json`). It keeps unknown
/// top-level fields in `extra`, and refuses input that could be read two
/// ways: a duplicate key at any depth, an unknown field inside a known
/// one, or an integer in an unknown field that canonical JSON would round.
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
    /// The reader's position in its source after this event. It is advanced
    /// in the same transaction as the admission. It is not part of the
    /// event: it is never hashed and never stored with it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub checkpoint: Option<Checkpoint>,
    /// Fields this contract does not know. They are kept unchanged, and
    /// they are covered by the event hash (ING-06).
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// A reader's durable position in its source.
///
/// The store keeps one sequence and hash per `(scope, key)`. Offering a
/// checkpoint moves the stored sequence from `expected_sequence` to
/// `next_sequence`, or changes nothing and reports a conflict. Offering the
/// same checkpoint again after it was applied succeeds, so a reader that
/// crashed before reading its receipt can retry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Checkpoint {
    /// Names the stream within the scope, for example one session file.
    pub key: String,
    /// The sequence the reader believes is stored. 0 before the first.
    pub expected_sequence: u64,
    /// The sequence after this event. Greater than `expected_sequence`.
    pub next_sequence: u64,
    /// A hash of the reader's own position, opaque to the engine. The reader
    /// compares it on restart to confirm it resumes the same stream.
    pub hash: Sha256Digest,
}

/// Largest checkpoint sequence: PostgreSQL stores it as a signed 64-bit integer.
pub const MAX_CHECKPOINT_SEQUENCE: u64 = i64::MAX as u64;
/// Maximum byte length of a checkpoint key.
pub const MAX_CHECKPOINT_KEY_BYTES: usize = 128;

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
    "checkpoint",
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
    #[serde(default)]
    checkpoint: Option<Checkpoint>,
}

impl<'de> Deserialize<'de> for SourceEvent {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        use serde::de::Error;

        let entries = deserializer.deserialize_map(UniqueRawEntries)?;
        let mut known = String::from("{");
        let mut extra = BTreeMap::new();
        for (key, value) in entries {
            let problem = |e: serde_json::Error| D::Error::custom(format!("{key}: {e}"));
            // Every value sits inside the event's own object, and the
            // limit on nesting counts from the event.
            if SOURCE_EVENT_FIELDS.contains(&key.as_str()) {
                // Known fields are parsed into types, so their integers are
                // read exactly. JSON content is checked when it is accepted.
                json::check_inside(value.get(), Integers::Any, 1).map_err(problem)?;
                if known.len() > 1 {
                    known.push(',');
                }
                known.push_str(&serde_json::to_string(&key).map_err(D::Error::custom)?);
                known.push(':');
                known.push_str(value.get());
            } else {
                // Unknown fields are hashed and stored in canonical form.
                json::check_inside(value.get(), Integers::Exact, 1).map_err(problem)?;
                let value = serde_json::from_str(value.get()).map_err(problem)?;
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
            checkpoint: f.checkpoint,
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
        // A set, so that an object with very many keys costs time in
        // proportion to its size.
        let mut seen: HashSet<String> = HashSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if seen.contains(&key) {
                return Err(serde::de::Error::custom(format!("duplicate key `{key}`")));
            }
            seen.insert(key.clone());
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
    ///
    /// The list is bounded: at most [`MAX_PROBLEMS`] entries, the last of
    /// which counts the rest when there are more.
    ///
    /// An event built in code was never read as JSON, so what reading
    /// checks in the unknown fields is checked here too: how deeply they
    /// nest, and that canonical JSON would not round their integers.
    pub fn validate(&self) -> Result<(), Vec<FieldError>> {
        let mut errors = Problems::default();
        for (key, value) in &self.extra {
            if SOURCE_EVENT_FIELDS.contains(&key.as_str()) {
                // Written out, the event would hold this key twice.
                errors.push(FieldError::new(
                    key.as_str(),
                    "is one of the event's own fields, and cannot be an unknown one as well",
                ));
            }
            // As when it is read: one level inside the event.
            if let Err(e) = json::check_value_inside(value, Integers::Exact, 1) {
                errors.push(FieldError::new(key.as_str(), e.to_string()));
            }
        }
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
        if let Some(checkpoint) = &self.checkpoint {
            checkpoint.validate(&mut errors);
        }
        if let Content::ToolCall(call) = &self.content {
            check_identifier(&mut errors, "content.tool_call.tool", &call.tool);
        }
        errors.finish()
    }
}

impl Checkpoint {
    fn validate(&self, errors: &mut Problems) {
        check_identifier(errors, "checkpoint.key", &self.key);
        if self.key.len() > MAX_CHECKPOINT_KEY_BYTES {
            errors.push(FieldError::new(
                "checkpoint.key",
                format!("must be at most {MAX_CHECKPOINT_KEY_BYTES} bytes"),
            ));
        }
        if self.next_sequence <= self.expected_sequence {
            errors.push(FieldError::new(
                "checkpoint.next_sequence",
                "must be greater than expected_sequence",
            ));
        }
        if self.next_sequence > MAX_CHECKPOINT_SEQUENCE {
            errors.push(FieldError::new(
                "checkpoint.next_sequence",
                format!("must be at most {MAX_CHECKPOINT_SEQUENCE}"),
            ));
        }
    }
}

/// The event's content: exact text, a JSON value, or one call of a tool.
///
/// Text is kept byte for byte. JSON content is hashed and chunked in its
/// RFC 8785 canonical form, and duplicate object keys are refused.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Content {
    Text(String),
    Json(#[schemars(with = "serde_json::Value")] Box<RawValue>),
    ToolCall(ToolCall),
}

impl Content {
    pub fn kind(&self) -> ContentKind {
        match self {
            Content::Text(_) => ContentKind::Text,
            Content::Json(_) => ContentKind::Json,
            Content::ToolCall(_) => ContentKind::ToolCall,
        }
    }
}

/// One call of a tool by an agent, with what the tool returned.
///
/// The engine stores it as text: the arguments in RFC 8785 canonical JSON,
/// a line feed, then the output byte for byte. An event of this kind is
/// one bead, however many chunks its text is cut into.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ToolCall {
    /// The tool's name, as the agent called it.
    pub tool: String,
    /// The arguments the agent gave: any JSON value.
    #[schemars(with = "serde_json::Value")]
    pub input: Box<RawValue>,
    /// What the tool returned, as the agent received it.
    pub output: String,
    /// Whether the tool reported that the call failed, if it said.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_error: Option<bool>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ContentKind {
    Text,
    Json,
    ToolCall,
}

/// When the event happened in the source, with its precision.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
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
    fn validate(&self, errors: &mut Problems) {
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
#[serde(deny_unknown_fields)]
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
#[serde(deny_unknown_fields)]
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

    fn validate(&self, errors: &mut Problems) {
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
#[serde(deny_unknown_fields)]
pub struct ObjectRef {
    /// Defaults to the referencing event's `source_ns`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_ns: Option<String>,
    pub external_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision: Option<String>,
}

impl ObjectRef {
    fn validate(&self, errors: &mut Problems, field: &str) {
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
#[serde(deny_unknown_fields)]
pub struct CorrelationKey {
    pub source_system: String,
    pub object_type: String,
    pub key_kind: String,
    pub value: String,
}

/// The record a change event is about. Mirrors memoriesQL `RecordEventDetail`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
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
#[serde(deny_unknown_fields)]
pub struct DocumentPosition {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub section_path: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page: Option<u32>,
}

/// Most problems one refusal lists.
pub const MAX_PROBLEMS: usize = 32;
/// Longest field name a problem repeats, in bytes.
pub const MAX_PROBLEM_FIELD_BYTES: usize = 128;
/// Longest description of one problem, in bytes.
pub const MAX_PROBLEM_TEXT_BYTES: usize = 512;

/// One validation problem in a SourceEvent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct FieldError {
    pub field: String,
    pub problem: String,
}

impl FieldError {
    /// Both parts are cut to a bounded length. A field name or a parser's
    /// message can repeat what was sent, and a refusal must not grow with
    /// what it refuses.
    pub fn new(field: impl Into<String>, problem: impl Into<String>) -> Self {
        Self {
            field: clipped(field.into(), MAX_PROBLEM_FIELD_BYTES),
            problem: clipped(problem.into(), MAX_PROBLEM_TEXT_BYTES),
        }
    }
}

/// `text`, or its first `max` bytes or so with a mark that it was cut.
fn clipped(mut text: String, max: usize) -> String {
    const CUT: &str = "...";
    if text.len() <= max {
        return text;
    }
    let mut end = max - CUT.len();
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    text.truncate(end);
    text.push_str(CUT);
    text
}

/// The problems found in one event: the first of them in full and a count
/// of the rest, so that a refusal stays small however much is wrong.
#[derive(Default)]
struct Problems {
    /// The first [`MAX_PROBLEMS`] found.
    listed: Vec<FieldError>,
    /// How many were found after those.
    unlisted: usize,
}

impl Problems {
    fn push(&mut self, problem: FieldError) {
        if self.listed.len() < MAX_PROBLEMS {
            self.listed.push(problem);
        } else {
            self.unlisted += 1;
        }
    }

    /// Every problem when they fit. Otherwise all but the last place hold
    /// problems, and the last holds the count of the rest.
    fn finish(mut self) -> Result<(), Vec<FieldError>> {
        if self.listed.is_empty() {
            return Ok(());
        }
        if self.unlisted > 0 {
            // The count takes the place of one problem, and counts it.
            self.listed.truncate(MAX_PROBLEMS - 1);
            self.listed.push(FieldError::new(
                "event",
                format!("and {} more problems", self.unlisted + 1),
            ));
        }
        Err(self.listed)
    }
}

fn check_identifier(errors: &mut Problems, field: &str, value: &str) {
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
    fn nesting_is_counted_from_the_event() {
        let nested = |levels: usize| format!("{}0{}", "[".repeat(levels), "]".repeat(levels));
        let parse = |json: String| {
            serde_json::from_str::<SourceEvent>(&json)
                .map(|_| ())
                .map_err(|e| e.to_string())
        };
        let scope = r#""scope": "acme","#;
        // An unknown field sits one level inside the event.
        let unknown = |levels| {
            parse(event_json().replacen(
                scope,
                &format!(r#"{scope} "x_deep": {},"#, nested(levels)),
                1,
            ))
        };
        assert!(unknown(json::MAX_DEPTH - 1).is_ok());
        let err = unknown(json::MAX_DEPTH).unwrap_err();
        assert!(
            err.contains("x_deep") && err.contains("nested more than 64"),
            "{err}"
        );
        // JSON content sits two levels inside it.
        let content = |levels| {
            parse(event_json().replacen(
                r#"{"text": "We decided to ship on Friday."}"#,
                &format!(r#"{{"json": {}}}"#, nested(levels)),
                1,
            ))
        };
        assert!(content(json::MAX_DEPTH - 2).is_ok());
        let err = content(json::MAX_DEPTH - 1).unwrap_err();
        assert!(
            err.contains("content") && err.contains("nested more than 64"),
            "{err}"
        );
    }

    #[test]
    fn input_that_could_be_read_two_ways_is_refused() {
        let with = |extra: &str| {
            let json = event_json().replacen(r#""scope": "acme","#, extra, 1);
            serde_json::from_str::<SourceEvent>(&json)
                .map(|_| ())
                .map_err(|e| e.to_string())
        };
        let scope = r#""scope": "acme","#;
        assert!(with(scope).is_ok());
        // A duplicate key inside a map, a known object or an unknown field.
        for doubled in [
            r#""attributes": {"team": "a", "team": "b"},"#,
            r#""x_other": {"deep": [{"k": 1, "k": 2}]},"#,
        ] {
            let json = event_json()
                .replacen(r#""attributes": {"channel": "general"},"#, "", 1)
                .replacen(scope, &format!("{scope} {doubled}"), 1);
            let err = serde_json::from_str::<SourceEvent>(&json).unwrap_err();
            assert!(
                err.to_string().contains("duplicate key"),
                "{doubled}: {err}"
            );
        }
        // A mistyped or unknown field inside a known one is not dropped.
        for nested in [
            r#""actor": {"id": "U1", "kind": "human", "display_name": "Ada"},"#,
            r#""structure": {"thred": "C1"},"#,
            r#""structure": {"reply_to": {"external_id": "1", "channel": "C1"}},"#,
            r#""occurred_at": {"precision": "unknown", "zone": "UTC"},"#,
        ] {
            let field = nested.split('"').nth(1).unwrap();
            let json = event_json()
                .lines()
                .filter(|line| !line.trim_start().starts_with(&format!("\"{field}\"")))
                .collect::<Vec<_>>()
                .join("\n")
                .replacen(scope, &format!("{scope} {nested}"), 1);
            let err = serde_json::from_str::<SourceEvent>(&json).unwrap_err();
            assert!(err.to_string().contains("unknown field"), "{nested}: {err}");
        }
        // Two unknown fields that differ only beyond 2^53 would hash alike.
        let err = with(&format!(r#"{scope} "x_reply_to": 9007199254740993,"#)).unwrap_err();
        assert!(
            err.contains("x_reply_to") && err.contains("exactly"),
            "{err}"
        );
        assert!(with(&format!(r#"{scope} "x_reply_to": 9007199254740992,"#)).is_ok());
        // A checkpoint's sequence is a typed integer and is read exactly.
        assert!(
            with(&format!(
                r#"{scope} "checkpoint": {{"key": "k", "expected_sequence": 9007199254740993,
                "next_sequence": 9007199254740994,
                "hash": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}},"#
            ))
            .is_ok()
        );
    }

    #[test]
    fn an_object_with_very_many_keys_parses_in_linear_time() {
        let mut json = String::from(
            r#"{"scope":"a","source_ns":"n","external_id":"1","profile_id":"p","media_type":"text/plain","parser_contract_version":"1","content":{"text":"x"}"#,
        );
        for i in 0..200_000 {
            json.push_str(&format!(r#","x{i}":0"#));
        }
        json.push('}');
        let started = std::time::Instant::now();
        let event: SourceEvent = serde_json::from_str(&json).unwrap();
        assert_eq!(event.extra.len(), 200_000);
        // A quadratic scan takes minutes here.
        assert!(started.elapsed() < std::time::Duration::from_secs(20));
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
            other => panic!("expected json content, not {other:?}"),
        }
    }

    #[test]
    fn a_tool_call_is_a_tool_its_arguments_and_what_it_returned() {
        let call = |content: &str| {
            serde_json::from_str::<SourceEvent>(&format!(
                r#"{{"scope":"a","source_ns":"agent/s1","external_id":"toolu_01",
                    "profile_id":"core.tool_call","media_type":"application/json",
                    "parser_contract_version":"1","structure":{{"parent":{{"external_id":"m7"}}}},
                    "content":{{"tool_call":{content}}}}}"#
            ))
        };
        let event = call(
            r#"{"tool":"Bash","input":{"command":"cargo test","timeout":60},
                "output":"test result: ok. 12 passed\n","is_error":false}"#,
        )
        .unwrap();
        assert!(event.validate().is_ok());
        assert_eq!(event.content.kind(), ContentKind::ToolCall);
        let Content::ToolCall(tool_call) = &event.content else {
            panic!("expected a tool call");
        };
        assert_eq!(tool_call.tool, "Bash");
        // The arguments are kept as they were written until they are accepted.
        assert_eq!(
            tool_call.input.get(),
            r#"{"command":"cargo test","timeout":60}"#
        );
        assert_eq!(tool_call.output, "test result: ok. 12 passed\n");
        assert_eq!(tool_call.is_error, Some(false));
        // It reads back as it was written.
        let again: SourceEvent =
            serde_json::from_str(&serde_json::to_string(&event).unwrap()).unwrap();
        assert_eq!(again.content.kind(), ContentKind::ToolCall);

        // The output is required, an unknown field is refused, and so is a
        // duplicate key in the arguments.
        assert!(call(r#"{"tool":"Bash","input":{}}"#).is_err());
        assert!(call(r#"{"tool":"Bash","input":{},"output":"","stdout":"x"}"#).is_err());
        assert!(call(r#"{"tool":"Bash","input":{"a":1,"a":2},"output":""}"#).is_err());
        // A tool has a name.
        let unnamed = call(r#"{"tool":"","input":null,"output":"x"}"#).unwrap();
        let problems = unnamed.validate().unwrap_err();
        assert_eq!(problems[0].field, "content.tool_call.tool");
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
    fn a_checkpoint_rides_with_the_event_but_is_not_an_unknown_field() {
        let with = event_json().replacen(
            r#""scope": "acme","#,
            r#""scope": "acme", "checkpoint": {"key": "session-1", "expected_sequence": 4,
                "next_sequence": 5, "hash": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},"#,
            1,
        );
        let event: SourceEvent = serde_json::from_str(&with).unwrap();
        assert!(event.validate().is_ok());
        let checkpoint = event.checkpoint.as_ref().unwrap();
        assert_eq!(
            (checkpoint.expected_sequence, checkpoint.next_sequence),
            (4, 5)
        );
        assert!(!event.extra.contains_key("checkpoint"));
    }

    #[test]
    fn a_checkpoint_must_advance() {
        let mut event: SourceEvent = serde_json::from_str(event_json()).unwrap();
        event.checkpoint = Some(Checkpoint {
            key: "session-1".into(),
            expected_sequence: 5,
            next_sequence: 5,
            hash: Sha256Digest::of(b"position"),
        });
        let errors = event.validate().unwrap_err();
        assert_eq!(errors[0].field, "checkpoint.next_sequence");
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
        let mut errors = Problems::default();
        occurred.validate(&mut errors);
        assert_eq!(errors.listed.len(), 1);
    }

    #[test]
    fn a_refusal_stays_small_however_much_is_wrong() {
        let mut event: SourceEvent = serde_json::from_str(event_json()).unwrap();
        event.attributes = (0..5_000)
            .map(|i| (format!("Bad-Key-{i}-{}", "x".repeat(300)), "v".to_string()))
            .collect();
        let problems = event.validate().unwrap_err();
        assert_eq!(problems.len(), MAX_PROBLEMS);
        // One problem for the number of attributes and one for each key.
        let last = &problems[MAX_PROBLEMS - 1];
        assert_eq!(
            (last.field.as_str(), last.problem.as_str()),
            ("event", "and 4970 more problems")
        );
        assert!(problems.iter().all(|p| {
            p.field.len() <= MAX_PROBLEM_FIELD_BYTES && p.problem.len() <= MAX_PROBLEM_TEXT_BYTES
        }));
        let size = serde_json::to_string(&problems).unwrap().len();
        assert!(size < 16 * 1024, "{size}");

        // One more than fit: the count takes the last place, and covers
        // the problem that would have held it.
        event.attributes = (0..=MAX_PROBLEMS)
            .map(|i| (format!("Bad-Key-{i}"), "v".to_string()))
            .collect();
        let problems = event.validate().unwrap_err();
        assert_eq!(problems.len(), MAX_PROBLEMS);
        assert_eq!(problems[MAX_PROBLEMS - 1].problem, "and 2 more problems");
        // Exactly as many as fit are all listed, and so are fewer.
        for fewer in [0, 1] {
            event.attributes.pop_first();
            let problems = event.validate().unwrap_err();
            assert_eq!(problems.len(), MAX_PROBLEMS - fewer);
            assert!(problems.iter().all(|p| p.field.starts_with("attributes.")));
        }
    }

    #[test]
    fn an_event_built_in_code_is_checked_as_one_that_was_read() {
        let event: SourceEvent = serde_json::from_str(event_json()).unwrap();
        let with = |key: &str, value: serde_json::Value| {
            let mut event = event.clone();
            event.extra.insert(key.into(), value);
            event.validate()
        };
        let nested = |levels: usize| {
            let mut value = serde_json::json!(0);
            for _ in 0..levels {
                value = serde_json::json!([value]);
            }
            value
        };
        // The same depths as `nesting_is_counted_from_the_event`.
        assert!(with("x_deep", nested(json::MAX_DEPTH - 1)).is_ok());
        let problems = with("x_deep", nested(json::MAX_DEPTH)).unwrap_err();
        assert_eq!(problems.len(), 1);
        assert_eq!(problems[0].field, "x_deep");
        assert!(problems[0].problem.contains("nested more than 64"));
        // And the same integers.
        assert!(with("x_reply_to", serde_json::json!(9_007_199_254_740_992_u64)).is_ok());
        let problems =
            with("x_reply_to", serde_json::json!([9_007_199_254_740_993_u64])).unwrap_err();
        assert!(problems[0].problem.contains("exactly"), "{problems:?}");
        // A key that reading would have taken for the event's own field.
        let problems = with("scope", serde_json::json!("elsewhere")).unwrap_err();
        assert_eq!(problems[0].field, "scope");
        assert!(problems[0].problem.contains("own fields"), "{problems:?}");
    }

    #[test]
    fn a_problem_is_cut_between_characters() {
        let cut = FieldError::new("\u{e9}".repeat(200), "\u{65e5}\u{672c}\u{8a9e}".repeat(400));
        assert!(cut.field.len() <= MAX_PROBLEM_FIELD_BYTES && cut.field.ends_with("..."));
        assert!(cut.problem.len() <= MAX_PROBLEM_TEXT_BYTES && cut.problem.ends_with("..."));
        let whole = FieldError::new("scope", "must not be empty");
        assert_eq!(
            (whole.field.as_str(), whole.problem.as_str()),
            ("scope", "must not be empty")
        );
    }
}

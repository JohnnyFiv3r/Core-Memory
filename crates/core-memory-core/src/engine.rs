//! Admission: SourceEvent in, typed outcome out, the event stored.

use std::collections::BTreeSet;
use std::ops::Range;
use std::sync::Arc;

use core_memory_chunker::{ChunkError, LengthUnit, chunk_text};
use core_memory_contract::{
    AdmissionOutcome, ChunkId, ContentKind, EventDetails, EventId, EventKey, IdKey, OccurredAt,
    RefusalReason, Sha256Digest, SourceEvent, TimeBasis, TimePrecision, Timestamp,
};
use serde::Deserialize;

use crate::clock::Clock;
use crate::content::{accept_content, event_hash};
use crate::error::{ConfigError, EngineError, StoreError};
use crate::facets::chunk_facets;
use crate::judge::JudgeConfig;
use crate::judging::CompiledJudge;
use crate::model::BindingConfig;
use crate::profile::{CompiledChunker, ProfileRegistry, SourceProfile};
use crate::read::ReaderConfig;
use crate::reader::CompiledReader;
use crate::store::{
    AdmissionCommit, AdmissionWrite, BeadsOf, CheckpointAdvance, CheckpointState, ChunkWrite,
    PinKind, PinResult, Purpose, ScopeState, Store, StoredEvent,
};
use crate::tool::ToolReaderConfig;
use crate::tool_reader::CompiledToolReader;
use crate::vocab::{LoadedVocabulary, Vocabulary};

/// Engine configuration as written by operators.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineConfig {
    #[serde(default)]
    pub vocabulary: Option<Vocabulary>,
    #[serde(default, rename = "profile")]
    pub profiles: Vec<SourceProfile>,
    #[serde(default)]
    pub limits: Limits,
    /// Has a model read each chunk. Without it the engine makes no model
    /// calls, and everything else works the same.
    #[serde(default)]
    pub reader: Option<ReaderConfig>,
    /// Has a model read each tool call. Without it tool calls are stored
    /// and not read.
    #[serde(default)]
    pub tool_reader: Option<ToolReaderConfig>,
    /// Has a judge model say how new beads relate to the beads near them.
    /// Without it nothing is judged and no relationship is written.
    #[serde(default)]
    pub judge: Option<JudgeConfig>,
    /// The models the engine may call.
    #[serde(default, rename = "binding")]
    pub bindings: Vec<BindingConfig>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Limits {
    pub max_chunks_per_event: u32,
    /// Most bytes of metadata one event may carry: everything it declares
    /// apart from its content, as JSON. A profile's `max_event_bytes`
    /// bounds the content.
    pub max_metadata_bytes: u32,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_chunks_per_event: 4_096,
            max_metadata_bytes: 262_144,
        }
    }
}

/// Which scopes a caller may write (SEC-01), or recall from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScopeGrants {
    All,
    Only(BTreeSet<String>),
}

impl ScopeGrants {
    pub fn allows(&self, scope: &str) -> bool {
        match self {
            ScopeGrants::All => true,
            ScopeGrants::Only(scopes) => scopes.contains(scope),
        }
    }
}

/// A startup failure: bad configuration, or a store that disagrees with it.
#[derive(Debug, thiserror::Error)]
pub enum StartupError {
    #[error("configuration: {0}")]
    Config(#[from] ConfigError),
    #[error(transparent)]
    Store(#[from] StoreError),
}

#[derive(Debug)]
pub struct Engine {
    pub(crate) registry: ProfileRegistry,
    pub(crate) vocab: Option<LoadedVocabulary>,
    limits: Limits,
    pub(crate) reader: Option<CompiledReader>,
    pub(crate) tool_reader: Option<CompiledToolReader>,
    pub(crate) judge: Option<CompiledJudge>,
    pub(crate) clock: Arc<dyn Clock>,
}

impl Engine {
    /// Validates the configuration.
    pub fn new(config: EngineConfig, clock: Arc<dyn Clock>) -> Result<Self, ConfigError> {
        let vocab = config.vocabulary.map(LoadedVocabulary::load).transpose()?;
        let registry = ProfileRegistry::new(config.profiles, vocab.as_ref())?;
        if config.limits.max_chunks_per_event == 0 || config.limits.max_metadata_bytes == 0 {
            return Err(ConfigError::new(
                "limits.max_chunks_per_event and limits.max_metadata_bytes must be 1 or more",
            ));
        }
        let mut ids = BTreeSet::new();
        for binding in &config.bindings {
            binding.validate()?;
            if !ids.insert(binding.id.as_str()) {
                return Err(ConfigError::new(format!(
                    "binding `{}` is configured twice",
                    binding.id
                )));
            }
        }
        let reader = config
            .reader
            .map(|settings| CompiledReader::compile(settings, &config.bindings))
            .transpose()?;
        if reader.is_some() && vocab.is_none() {
            return Err(ConfigError::new(
                "a [reader] needs a [vocabulary]: the types it may give a chunk",
            ));
        }
        let tool_reader = config
            .tool_reader
            .map(|settings| CompiledToolReader::compile(settings, &config.bindings))
            .transpose()?;
        let judge = config
            .judge
            .map(|settings| CompiledJudge::compile(settings, &config.bindings))
            .transpose()?;
        Ok(Self {
            registry,
            vocab,
            limits: config.limits,
            reader,
            tool_reader,
            judge,
            clock,
        })
    }

    pub fn registry(&self) -> &ProfileRegistry {
        &self.registry
    }

    pub fn vocabulary(&self) -> Option<&LoadedVocabulary> {
        self.vocab.as_ref()
    }

    pub fn limits(&self) -> Limits {
        self.limits
    }

    /// Pins every profile and chunker revision in the store, refusing to
    /// start if a stored revision has different contents. Changing a profile
    /// or its chunker settings therefore requires a new revision, which keeps
    /// chunk IDs reproducible.
    pub fn pin_profiles(&self, store: &mut dyn Store) -> Result<(), StartupError> {
        for p in self.registry.iter() {
            let pins = [
                (PinKind::SourceProfile, p.def.id.clone(), p.def.rev, p.hash),
                (
                    PinKind::Chunker,
                    format!("{}/{}", p.def.id, p.chunker_ref.id),
                    p.chunker_ref.rev,
                    p.chunker_ref.config_hash,
                ),
            ];
            for (kind, id, rev, hash) in pins {
                if let PinResult::Mismatch { stored } = store.pin(kind, &id, rev, &hash)? {
                    return Err(ConfigError::new(format!(
                        "{} `{id}` rev {rev} changed since it was first used (stored {stored}, now {hash}); bump its rev",
                        kind.as_str()
                    ))
                    .into());
                }
            }
        }
        Ok(())
    }

    /// Admits one event. Returns `Err` only for store or serialization
    /// failures; every judgment about the event is a typed outcome.
    ///
    /// `grants` are the caller's. The scope's own state is read from the
    /// store, and checked again inside the transaction that writes.
    pub fn admit(
        &self,
        store: &mut dyn Store,
        grants: &ScopeGrants,
        event: &SourceEvent,
    ) -> Result<AdmissionOutcome, EngineError> {
        use AdmissionOutcome as A;
        use RefusalReason as R;

        if !grants.allows(&event.scope) {
            return Ok(A::refused(R::ScopeDenied));
        }
        if let Err(problems) = event.validate() {
            return Ok(A::Refused {
                reason: R::Malformed,
                problems,
            });
        }
        let Some(profile) = self.registry.get(&event.profile_id) else {
            return Ok(A::refused_with(
                R::InvalidProfile,
                "profile_id",
                "no such profile",
            ));
        };
        if event.content.kind() != profile.def.content {
            return Ok(A::refused_with(
                R::Malformed,
                "content",
                format!(
                    "profile `{}` takes {:?} content",
                    profile.def.id, profile.def.content
                ),
            ));
        }
        let max_bytes = profile.def.max_event_bytes;
        let raw_len = match &event.content {
            core_memory_contract::Content::Text(t) => t.len(),
            core_memory_contract::Content::Json(raw) => raw.get().len(),
            core_memory_contract::Content::ToolCall(call) => {
                call.input.get().len() + 1 + call.output.len()
            }
        };
        if raw_len as u64 > max_bytes {
            return Ok(oversize(
                "content",
                format!("content exceeds {max_bytes} bytes"),
            ));
        }
        let content = match accept_content(&event.content) {
            Ok(c) => c,
            Err(e) => return Ok(A::refused_with(R::Malformed, "content", e.0)),
        };
        if content.text.len() as u64 > max_bytes {
            return Ok(oversize(
                "content",
                format!("canonical content exceeds {max_bytes} bytes"),
            ));
        }
        // The store keeps content exactly or not at all, and PostgreSQL text
        // cannot hold U+0000.
        if content.text.contains('\0') {
            return Ok(A::refused_with(
                R::Malformed,
                "content",
                "content contains U+0000, which cannot be stored",
            ));
        }
        let details = EventDetails {
            content_kind: content.kind,
            source_type: profile.def.source_type,
            source_system: profile.source_system(event).to_string(),
            media_type: event.media_type.clone(),
            occurred_at: event.occurred_at.clone(),
            actor: event.actor.clone(),
            structure: event.structure.clone(),
            attributes: event.attributes.clone(),
            tool_call: content.tool_call.clone(),
            extra: event.extra.clone(),
            parser_contract_version: event.parser_contract_version.clone(),
        };
        let details_json = serde_json::to_value(&details)?;
        if has_nul(&details_json) {
            return Ok(A::refused_with(
                R::Malformed,
                "event",
                "a field contains U+0000, which cannot be stored",
            ));
        }
        let metadata_bytes = details_json.to_string().len();
        if metadata_bytes > self.limits.max_metadata_bytes as usize {
            return Ok(oversize(
                "event",
                format!(
                    "the event's metadata is {metadata_bytes} bytes; the limit is {}",
                    self.limits.max_metadata_bytes
                ),
            ));
        }
        let hash = event_hash(event, &content)?;

        let Some(scope) = store.scope(&event.scope)? else {
            return Ok(not_active(None));
        };
        if scope.state != ScopeState::Active {
            return Ok(not_active(Some(scope.state)));
        }

        // Identity check on the event-level hash, before chunking (ING-03).
        let offered = Offered {
            id_key: &scope.id_key,
            editable: profile.def.editable && event.revision.is_none(),
            hash,
        };
        let first = Target {
            event_id: EventId::derive(&scope.id_key, &event.key()),
            key: event.key(),
            assigned_revision: None,
            revises: None,
        };
        let mut target = match store.event(&first.event_id)? {
            None => first,
            Some(stored) => match offered.against(store, &first, stored)? {
                Ok(revision) => revision,
                Err(outcome) => return self.settle(store, event, outcome),
            },
        };

        let spans = match profile.chunker {
            CompiledChunker::Text(cfg) => match chunk_text(&content.text, &cfg) {
                Ok(spans) => spans,
                Err(ChunkError::Empty) => {
                    return Ok(A::refused_with(
                        R::Malformed,
                        "content",
                        "content is empty or only whitespace",
                    ));
                }
                Err(ChunkError::InvalidConfig(e)) => {
                    return Ok(A::refused_with(R::InvalidProfile, "profile_id", e));
                }
            },
            CompiledChunker::JsonWhole { max, unit } => {
                let len = match unit {
                    LengthUnit::Bytes => content.text.len(),
                    LengthUnit::CodePoints => content.text.chars().count(),
                };
                if len > max as usize {
                    return Ok(oversize(
                        "content",
                        format!("record exceeds the chunker's {max} {unit:?} limit"),
                    ));
                }
                std::iter::once(0..content.text.len()).collect()
            }
        };
        if spans.len() > self.limits.max_chunks_per_event as usize {
            return Ok(oversize(
                "content",
                format!(
                    "event yields {} chunks; the limit is {}",
                    spans.len(),
                    self.limits.max_chunks_per_event
                ),
            ));
        }

        let captured_at = self.clock.now().truncated_to_micros();
        let (time_at, time_basis) = ordering_time(event.occurred_at.as_ref(), captured_at);
        // Facets depend only on the text. Chunk IDs depend on the key too,
        // so they are derived again if the event becomes a revision.
        let mut pieces = Vec::with_capacity(spans.len());
        let mut char_pos = 0u64;
        for span in &spans {
            let text = &content.text[Range::clone(span)];
            let facets = chunk_facets(text, content.kind, &profile.identifiers);
            let char_start = char_pos;
            char_pos += facets.lengths.code_points;
            pieces.push((span, char_start, char_pos, facets));
        }
        let profile_ref = profile.profile_ref();

        // Two passes at most: one under the key as offered, and one under
        // a derived revision if another first version of an editable
        // record was stored meanwhile.
        // A message is a bead for each chunk, and a model reads each
        // chunk. A tool call is one bead, and a model reads it whole.
        let (beads, queue) = match content.kind {
            ContentKind::ToolCall => (
                BeadsOf::Event,
                self.tool_reader.as_ref().map(|_| Purpose::ToolRead),
            ),
            ContentKind::Text | ContentKind::Json => (
                BeadsOf::Chunks,
                self.reader.as_ref().map(|_| Purpose::ChunkRead),
            ),
        };
        // A message's beads are owed to the judge from now, after a pause
        // that lets what arrives with them be judged with them. A tool
        // call's bead is owed once a model has said what the call did,
        // which is what the judge is shown of it.
        let judge = self
            .judge
            .as_ref()
            .filter(|_| beads == BeadsOf::Chunks)
            .map(|judge| captured_at.plus_millis(judge.settings.debounce_ms));
        loop {
            let chunks: Vec<ChunkWrite> = pieces
                .iter()
                .enumerate()
                .map(|(ordinal, (span, char_start, char_end, facets))| {
                    let text = &content.text[Range::clone(span)];
                    ChunkWrite {
                        chunk_id: ChunkId::derive(
                            &scope.id_key,
                            &target.key,
                            &profile.chunker_ref.id,
                            profile.chunker_ref.rev,
                            span.start as u64,
                            span.end as u64,
                        ),
                        ordinal: u32::try_from(ordinal).unwrap_or(u32::MAX),
                        byte_start: span.start as u64,
                        byte_end: span.end as u64,
                        char_start: *char_start,
                        char_end: *char_end,
                        content_hash: Sha256Digest::of(text.as_bytes()),
                        text: text.to_string(),
                        facets: facets.clone(),
                    }
                })
                .collect();
            let committed = store.commit_admission(&AdmissionWrite {
                event_id: target.event_id,
                workspace: &scope.workspace,
                key: &target.key,
                engine_assigned_revision: target.assigned_revision.is_some(),
                event_hash: hash,
                content_hash: content.hash,
                details: &details,
                profile: &profile_ref,
                chunker: &profile.chunker_ref,
                chunks: &chunks,
                time_at,
                time_basis,
                captured_at,
                checkpoint: event.checkpoint.as_ref(),
                revises: target.revises,
                beads,
                queue,
                judge,
            })?;
            return match committed {
                AdmissionCommit::Committed { checkpoint } => Ok(A::Accepted {
                    event_id: target.event_id,
                    chunk_ids: chunks.iter().map(|c| c.chunk_id).collect(),
                    assigned_revision: target.assigned_revision,
                    checkpoint,
                }),
                // Another admission of this key committed first. If it was
                // the first version of an editable record and this is a
                // different one, this becomes a revision of it.
                AdmissionCommit::AlreadyPresent(stored) if target.revises.is_none() => {
                    match offered.against(store, &target, stored)? {
                        Ok(revision) => {
                            target = revision;
                            continue;
                        }
                        Err(outcome) => self.settle(store, event, outcome),
                    }
                }
                AdmissionCommit::AlreadyPresent(stored) => {
                    let (Existing::Settled(o) | Existing::Changed(o)) =
                        existing_outcome(target.event_id, stored, hash);
                    self.settle(store, event, o)
                }
                AdmissionCommit::RevisedEventErased => Ok(A::refused(R::Tombstoned)),
                AdmissionCommit::ScopeNotActive(state) => Ok(not_active(state)),
                AdmissionCommit::CheckpointConflict(stored) => {
                    Ok(checkpoint_conflict(event, stored))
                }
            };
        }
    }

    /// Finishes an outcome for an event that was already stored. A replay
    /// still advances the reader's checkpoint; nothing else does.
    fn settle(
        &self,
        store: &mut dyn Store,
        event: &SourceEvent,
        outcome: AdmissionOutcome,
    ) -> Result<AdmissionOutcome, EngineError> {
        let (
            AdmissionOutcome::Replay {
                event_id,
                chunk_ids,
                ..
            },
            Some(offered),
        ) = (&outcome, &event.checkpoint)
        else {
            return Ok(outcome);
        };
        let at = self.clock.now().truncated_to_micros();
        Ok(match store.advance_checkpoint(&event.scope, offered, at)? {
            CheckpointAdvance::Advanced(receipt) => AdmissionOutcome::Replay {
                event_id: *event_id,
                chunk_ids: chunk_ids.clone(),
                checkpoint: Some(receipt),
            },
            CheckpointAdvance::Conflict(stored) => checkpoint_conflict(event, stored),
            CheckpointAdvance::ScopeNotActive(state) => not_active(state),
        })
    }
}

/// Where an event is to be stored.
struct Target {
    key: EventKey,
    event_id: EventId,
    /// The revision the engine derived, when the event is an edit of an
    /// editable record.
    assigned_revision: Option<String>,
    /// The first version of the record, when the event is an edit of it.
    /// It must still be live when the edit is stored.
    revises: Option<EventId>,
}

/// What an admission offers, as far as identity goes.
struct Offered<'a> {
    id_key: &'a IdKey,
    /// The profile edits objects in place and the event names no revision.
    editable: bool,
    hash: Sha256Digest,
}

impl Offered<'_> {
    /// Decides what to do when `at.key` already holds `stored`: the
    /// outcome to answer with, or the revision to store the event under.
    fn against(
        &self,
        store: &mut dyn Store,
        at: &Target,
        stored: StoredEvent,
    ) -> Result<Result<Target, AdmissionOutcome>, EngineError> {
        let conflict = match existing_outcome(at.event_id, stored, self.hash) {
            Existing::Settled(outcome) => return Ok(Err(outcome)),
            Existing::Changed(conflict) => conflict,
        };
        if !self.editable {
            return Ok(Err(conflict));
        }
        // An editable source changed the object in place: admit it as a
        // revision derived from its content (ING-04).
        let revision = derived_revision(&self.hash);
        let key = at.key.with_revision(revision.clone());
        let event_id = EventId::derive(self.id_key, &key);
        if let Some(stored) = store.event(&event_id)? {
            let (Existing::Settled(o) | Existing::Changed(o)) =
                existing_outcome(event_id, stored, self.hash);
            return Ok(Err(o));
        }
        Ok(Ok(Target {
            key,
            event_id,
            assigned_revision: Some(revision),
            revises: Some(at.event_id),
        }))
    }
}

enum Existing {
    /// Replay or tombstone: the answer regardless of profile.
    Settled(AdmissionOutcome),
    /// Same key, different content: a conflict unless the source is editable.
    Changed(AdmissionOutcome),
}

fn existing_outcome(event_id: EventId, stored: StoredEvent, offered: Sha256Digest) -> Existing {
    match stored {
        StoredEvent::Erased => {
            Existing::Settled(AdmissionOutcome::refused(RefusalReason::Tombstoned))
        }
        StoredEvent::Live {
            event_hash,
            chunk_ids,
        } if event_hash == offered => Existing::Settled(AdmissionOutcome::Replay {
            event_id,
            chunk_ids,
            checkpoint: None,
        }),
        StoredEvent::Live { event_hash, .. } => {
            Existing::Changed(AdmissionOutcome::IdentityConflict {
                event_id,
                stored_hash: event_hash,
                offered_hash: offered,
            })
        }
    }
}

fn not_active(state: Option<ScopeState>) -> AdmissionOutcome {
    match state {
        None => AdmissionOutcome::refused_with(
            RefusalReason::ScopeDenied,
            "scope",
            "the scope is not enrolled",
        ),
        Some(state) => AdmissionOutcome::refused_with(
            RefusalReason::ScopeNotActive,
            "scope",
            format!("the scope is {}", state.as_str()),
        ),
    }
}

fn checkpoint_conflict(event: &SourceEvent, stored: CheckpointState) -> AdmissionOutcome {
    AdmissionOutcome::CheckpointConflict {
        key: event
            .checkpoint
            .as_ref()
            .map(|c| c.key.clone())
            .unwrap_or_default(),
        stored_sequence: stored.sequence,
        stored_hash: stored.hash,
    }
}

/// The time an event is ordered by, best effort: the source's instant, else
/// the start of the period the source named, else when it was admitted.
fn ordering_time(occurred: Option<&OccurredAt>, captured_at: Timestamp) -> (Timestamp, TimeBasis) {
    match occurred.and_then(|o| o.at.map(|at| (at, o.precision))) {
        Some((at, TimePrecision::Instant | TimePrecision::Second | TimePrecision::Interval)) => {
            (at.truncated_to_micros(), TimeBasis::Source)
        }
        // Events of one period share its start, so within the period they
        // are ordered by when they were admitted.
        Some((at, precision)) => (
            period_start(at, precision).truncated_to_micros(),
            TimeBasis::SourceBucket,
        ),
        None => (captured_at, TimeBasis::Captured),
    }
}

/// The start of the minute, hour, day, month or year that `at` falls in,
/// read in the offset the source gave it in. A time of unknown precision
/// is kept as given.
fn period_start(at: Timestamp, precision: TimePrecision) -> Timestamp {
    use time::{Month, Time};
    let t = at.0;
    let start = match precision {
        TimePrecision::Minute => Time::from_hms(t.hour(), t.minute(), 0)
            .ok()
            .map(|time| t.replace_time(time)),
        TimePrecision::Hour => Time::from_hms(t.hour(), 0, 0)
            .ok()
            .map(|time| t.replace_time(time)),
        TimePrecision::Day => Some(t.replace_time(Time::MIDNIGHT)),
        TimePrecision::Month => t.replace_time(Time::MIDNIGHT).replace_day(1).ok(),
        TimePrecision::Year => t
            .replace_time(Time::MIDNIGHT)
            .replace_day(1)
            .ok()
            .and_then(|first| first.replace_month(Month::January).ok()),
        TimePrecision::Instant
        | TimePrecision::Second
        | TimePrecision::Interval
        | TimePrecision::Unknown => None,
    };
    start.map_or(at, Timestamp)
}

/// Whether any string or object key in `value` contains U+0000.
fn has_nul(value: &serde_json::Value) -> bool {
    match value {
        serde_json::Value::String(s) => s.contains('\0'),
        serde_json::Value::Array(items) => items.iter().any(has_nul),
        serde_json::Value::Object(map) => map.iter().any(|(k, v)| k.contains('\0') || has_nul(v)),
        _ => false,
    }
}

/// Revision the engine assigns to an edited event: derived from content,
/// never from arrival order.
fn derived_revision(hash: &Sha256Digest) -> String {
    format!("cm-{}", &hash.to_hex()[..32])
}

fn oversize(field: &str, problem: String) -> AdmissionOutcome {
    AdmissionOutcome::refused_with(RefusalReason::Oversize, field, problem)
}

/// The key an edited event would be stored under. Exposed for tests and
/// tooling that need to look an engine-assigned revision up.
pub fn engine_revision_key(key: &EventKey, hash: &Sha256Digest) -> EventKey {
    key.with_revision(derived_revision(hash))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(s: &str) -> Timestamp {
        Timestamp::parse(s).unwrap()
    }

    fn occurred(precision: TimePrecision, when: Option<&str>) -> OccurredAt {
        OccurredAt {
            at: when.map(at),
            end: None,
            precision,
            raw: Some("as the source wrote it".into()),
            timezone: None,
        }
    }

    #[test]
    fn events_are_ordered_by_the_best_time_the_source_gave() {
        let captured = at("2026-11-17T12:00:00Z");
        let exact = occurred(TimePrecision::Second, Some("2026-11-01T09:30:00Z"));
        assert_eq!(
            ordering_time(Some(&exact), captured),
            (at("2026-11-01T09:30:00Z"), TimeBasis::Source)
        );
        let day = occurred(TimePrecision::Day, Some("2026-11-01T00:00:00Z"));
        assert_eq!(
            ordering_time(Some(&day), captured),
            (at("2026-11-01T00:00:00Z"), TimeBasis::SourceBucket)
        );
        // A coarse time is ordered by the start of its period, in the
        // offset the source gave, whatever time of day came with it.
        for (precision, given, start) in [
            (
                TimePrecision::Minute,
                "2026-11-01T09:30:45.5Z",
                "2026-11-01T09:30:00Z",
            ),
            (
                TimePrecision::Hour,
                "2026-11-01T09:30:45Z",
                "2026-11-01T09:00:00Z",
            ),
            (
                TimePrecision::Day,
                "2026-11-01T09:30:45Z",
                "2026-11-01T00:00:00Z",
            ),
            (
                TimePrecision::Day,
                "2026-11-01T01:00:00+05:00",
                "2026-10-31T19:00:00Z",
            ),
            (
                TimePrecision::Month,
                "2026-11-17T09:30:45Z",
                "2026-11-01T00:00:00Z",
            ),
            (
                TimePrecision::Year,
                "2026-11-17T09:30:45-08:00",
                "2026-01-01T08:00:00Z",
            ),
            (
                TimePrecision::Unknown,
                "2026-11-17T09:30:45Z",
                "2026-11-17T09:30:45Z",
            ),
        ] {
            assert_eq!(
                ordering_time(Some(&occurred(precision, Some(given))), captured),
                (at(start), TimeBasis::SourceBucket),
                "{precision:?} {given}"
            );
        }
        // The first year's start, east of UTC, is before the earliest
        // instant that can be written. It is clamped, not a panic.
        let (earliest, _) = ordering_time(
            Some(&occurred(
                TimePrecision::Year,
                Some("0000-06-01T00:00:00+02:00"),
            )),
            captured,
        );
        assert_eq!(earliest, at("0000-01-01T00:00:00Z"));
        let unparsed = occurred(TimePrecision::Day, None);
        assert_eq!(
            ordering_time(Some(&unparsed), captured),
            (captured, TimeBasis::Captured)
        );
        assert_eq!(
            ordering_time(None, captured),
            (captured, TimeBasis::Captured)
        );
    }

    #[test]
    fn nul_is_found_in_strings_and_keys_at_any_depth() {
        let clean = serde_json::json!({"a": ["x", {"b": "\\u0000 as six characters"}]});
        assert!(!has_nul(&clean));
        assert!(has_nul(&serde_json::json!({"a": ["x", {"b": "y\0"}]})));
        assert!(has_nul(&serde_json::json!({"k\0": 1})));
    }
}

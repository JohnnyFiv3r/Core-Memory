//! Admission: SourceEvent in, typed outcome out, records into the outbox.

use std::collections::BTreeSet;
use std::ops::Range;
use std::sync::Arc;

use core_memory_chunker::{ChunkError, LengthUnit, chunk_text};
use core_memory_contract::record::chunk_record_key;
use core_memory_contract::{
    AdmissionOutcome, BusyCause, Catalogs, ChunkBody, ChunkId, ChunkRecord, EventId, EventInfo,
    EventKey, Memory, RECORD_CONTRACT_V1, Record, RecordBody, RefusalReason, RelationshipStage,
    Sha256Digest, SourceEvent, Stage,
};
use serde::{Deserialize, Serialize};

use crate::classify::classify;
use crate::clock::Clock;
use crate::content::{accept_content, event_hash};
use crate::error::{ConfigError, EngineError, StoreError};
use crate::facets::chunk_facets;
use crate::profile::{CompiledChunker, ProfileRegistry, SourceProfile};
use crate::store::{
    AdmissionWrite, CommitResult, LedgerEntry, OperationalStore, OutboxRecord, PinKind, PinResult,
};
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
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Limits {
    /// New events are refused with `Busy{sink_lagging}` while this many
    /// records wait for the slowest required sink.
    pub max_outbox_backlog: u64,
    pub busy_retry_after_ms: u64,
    pub max_chunks_per_event: u32,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_outbox_backlog: 100_000,
            busy_retry_after_ms: 1_000,
            max_chunks_per_event: 4_096,
        }
    }
}

/// Admission readiness: new events are refused as busy while the slowest
/// required sink is `max_backlog` or more records behind.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Readiness {
    pub ready: bool,
    pub backlog: u64,
    pub max_backlog: u64,
}

/// Which scopes a caller may write (SEC-01).
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
    registry: ProfileRegistry,
    vocab: Option<LoadedVocabulary>,
    limits: Limits,
    required_sinks: Vec<String>,
    clock: Arc<dyn Clock>,
}

impl Engine {
    /// Validates the configuration. `required_sinks` are the sinks whose
    /// backlog applies backpressure and holds back outbox pruning.
    pub fn new(
        config: EngineConfig,
        required_sinks: Vec<String>,
        clock: Arc<dyn Clock>,
    ) -> Result<Self, ConfigError> {
        let vocab = config.vocabulary.map(LoadedVocabulary::load).transpose()?;
        let registry = ProfileRegistry::new(config.profiles, vocab.as_ref())?;
        if config.limits.max_chunks_per_event == 0 {
            return Err(ConfigError::new(
                "limits.max_chunks_per_event must be 1 or more",
            ));
        }
        Ok(Self {
            registry,
            vocab,
            limits: config.limits,
            required_sinks,
            clock,
        })
    }

    pub fn registry(&self) -> &ProfileRegistry {
        &self.registry
    }

    pub fn required_sinks(&self) -> &[String] {
        &self.required_sinks
    }

    pub fn limits(&self) -> Limits {
        self.limits
    }

    /// Whether a new event would be admitted now rather than refused as
    /// busy, so hosts can pause intake (OPS-05).
    pub fn readiness(&self, store: &dyn OperationalStore) -> Result<Readiness, StoreError> {
        let backlog = store.outbox_backlog(&self.required_sinks)?;
        Ok(Readiness {
            ready: backlog < self.limits.max_outbox_backlog,
            backlog,
            max_backlog: self.limits.max_outbox_backlog,
        })
    }

    /// Pins every profile and chunker revision in the store, refusing to
    /// start if a stored revision has different contents. Changing a profile
    /// or its chunker settings therefore requires a new revision, which keeps
    /// chunk IDs and decisions reproducible.
    pub fn pin_profiles(&self, store: &mut dyn OperationalStore) -> Result<(), StartupError> {
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
    pub fn admit(
        &self,
        store: &mut dyn OperationalStore,
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
        };
        if raw_len as u64 > max_bytes {
            return Ok(oversize(format!("content exceeds {max_bytes} bytes")));
        }
        let content = match accept_content(&event.content) {
            Ok(c) => c,
            Err(e) => return Ok(A::refused_with(R::Malformed, "content", e.0)),
        };
        if content.text.len() as u64 > max_bytes {
            return Ok(oversize(format!(
                "canonical content exceeds {max_bytes} bytes"
            )));
        }
        let hash = event_hash(event, &content)?;

        // Identity check on the event-level hash, before chunking (ING-03).
        let mut key = event.key();
        let mut assigned_revision = None;
        match store.ledger_get(&key)? {
            None => {}
            Some(entry) => match existing_outcome(entry, hash) {
                Existing::Settled(outcome) => return Ok(outcome),
                Existing::Changed(outcome) => {
                    if !(profile.def.editable && event.revision.is_none()) {
                        return Ok(outcome);
                    }
                    // An editable source changed the object in place: admit
                    // it as a revision derived from its content (ING-04).
                    let revision = derived_revision(&hash);
                    key = key.with_revision(revision.clone());
                    if let Some(entry) = store.ledger_get(&key)? {
                        let (Existing::Settled(o) | Existing::Changed(o)) =
                            existing_outcome(entry, hash);
                        return Ok(o);
                    }
                    assigned_revision = Some(revision);
                }
            },
        }

        let backlog = store.outbox_backlog(&self.required_sinks)?;
        if backlog >= self.limits.max_outbox_backlog {
            return Ok(A::Busy {
                retry_after_ms: self.limits.busy_retry_after_ms,
                cause: BusyCause::SinkLagging,
            });
        }

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
                    return Ok(oversize(format!(
                        "record exceeds the chunker's {max} {unit:?} limit"
                    )));
                }
                std::iter::once(0..content.text.len()).collect()
            }
        };
        if spans.len() > self.limits.max_chunks_per_event as usize {
            return Ok(oversize(format!(
                "event yields {} chunks; the limit is {}",
                spans.len(),
                self.limits.max_chunks_per_event
            )));
        }

        let now = self.clock.now();
        let event_id = EventId::derive(&key);
        let chunk_ids: Vec<ChunkId> = spans
            .iter()
            .map(|r| {
                ChunkId::derive(
                    &key,
                    &profile.chunker_ref.id,
                    profile.chunker_ref.rev,
                    r.start as u64,
                    r.end as u64,
                )
            })
            .collect();
        let info = EventInfo {
            event_id,
            key: key.clone(),
            engine_assigned_revision: assigned_revision.is_some(),
            event_hash: hash,
            content_hash: content.hash,
            content_kind: content.kind,
            source_type: profile.def.source_type,
            source_system: profile.source_system(event).to_string(),
            media_type: event.media_type.clone(),
            occurred_at: event.occurred_at.clone(),
            actor: event.actor.clone(),
            structure: event.structure.clone(),
            attributes: event.attributes.clone(),
            extra: event.extra.clone(),
            parser_contract_version: event.parser_contract_version.clone(),
            captured_at: now,
            chunk_count: u32::try_from(spans.len()).unwrap_or(u32::MAX),
        };
        let catalogs = Catalogs {
            type_catalog: self.vocab.as_ref().map(|v| v.catalog().clone()),
            predicate: None,
        };

        let mut records = Vec::with_capacity(spans.len());
        let mut char_pos = 0u64;
        for (ordinal, (span, chunk_id)) in spans.iter().zip(&chunk_ids).enumerate() {
            let text = &content.text[Range::clone(span)];
            let facets = chunk_facets(text, content.kind, &profile.identifiers);
            let char_start = char_pos;
            char_pos += facets.lengths.code_points;
            let classification = classify(profile, event, chunk_id, now);
            let record = Record {
                contract: RECORD_CONTRACT_V1.to_string(),
                record_key: chunk_record_key(chunk_id),
                scope: key.scope.clone(),
                body: RecordBody::Chunk(Box::new(ChunkRecord {
                    event: info.clone(),
                    memory: Memory {
                        chunk: ChunkBody {
                            chunk_id: *chunk_id,
                            ordinal: u32::try_from(ordinal).unwrap_or(u32::MAX),
                            byte_start: span.start as u64,
                            byte_end: span.end as u64,
                            char_start,
                            char_end: char_pos,
                            content_hash: Sha256Digest::of(text.as_bytes()),
                            text: text.to_string(),
                        },
                        type_outcome: classification.outcome,
                    },
                    facets,
                    source_profile: profile.profile_ref(),
                    chunker: profile.chunker_ref.clone(),
                    catalogs: catalogs.clone(),
                    decision: classification.decision,
                    relationships: RelationshipStage {
                        stage: Stage::Off,
                        binding_ref: None,
                    },
                })),
            };
            records.push(OutboxRecord {
                record_key: record.record_key.clone(),
                record_json: serde_json::to_string(&record)?,
            });
        }

        let committed = store.commit_admission(AdmissionWrite {
            key: &key,
            event_id,
            event_hash: hash,
            chunk_ids: &chunk_ids,
            records,
            admitted_at: now,
        })?;
        Ok(match committed {
            CommitResult::Committed => A::Accepted {
                event_id,
                chunk_ids,
                assigned_revision,
            },
            CommitResult::AlreadyPresent(entry) => {
                let (Existing::Settled(o) | Existing::Changed(o)) = existing_outcome(entry, hash);
                o
            }
        })
    }
}

enum Existing {
    /// Replay or tombstone: the answer regardless of profile.
    Settled(AdmissionOutcome),
    /// Same key, different content: a conflict unless the source is editable.
    Changed(AdmissionOutcome),
}

fn existing_outcome(entry: LedgerEntry, offered: Sha256Digest) -> Existing {
    match entry {
        LedgerEntry::Tombstoned => {
            Existing::Settled(AdmissionOutcome::refused(RefusalReason::Tombstoned))
        }
        LedgerEntry::Live {
            event_id,
            event_hash,
            chunk_ids,
        } if event_hash == offered => Existing::Settled(AdmissionOutcome::Replay {
            event_id,
            chunk_ids,
        }),
        LedgerEntry::Live {
            event_id,
            event_hash,
            ..
        } => Existing::Changed(AdmissionOutcome::IdentityConflict {
            event_id,
            stored_hash: event_hash,
            offered_hash: offered,
        }),
    }
}

/// Revision the engine assigns to an edited event: derived from content,
/// never from arrival order.
fn derived_revision(hash: &Sha256Digest) -> String {
    format!("cm-{}", &hash.to_hex()[..32])
}

fn oversize(problem: String) -> AdmissionOutcome {
    AdmissionOutcome::refused_with(RefusalReason::Oversize, "content", problem)
}

/// The key an edited event would be stored under. Exposed for tests and
/// tooling that need to look an engine-assigned revision up.
pub fn engine_revision_key(key: &EventKey, hash: &Sha256Digest) -> EventKey {
    key.with_revision(derived_revision(hash))
}

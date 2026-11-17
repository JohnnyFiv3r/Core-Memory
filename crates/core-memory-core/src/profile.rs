//! Source profiles (ING-07): declarative configuration describing how to
//! admit, identify, chunk and facet one kind of source.
//!
//! A profile never assigns a type. It may narrow which types a model can
//! choose for its source, and nothing more.

use std::collections::BTreeMap;

use core_memory_chunker::{
    ALGORITHM as TEXT_ALGORITHM, ALGORITHM_REV, LengthUnit, TextChunkerConfig,
};
use core_memory_contract::hash::canonical_json_digest;
use core_memory_contract::{
    ChunkerRef, ContentKind, ProfileRef, Sha256Digest, SourceEvent, SourceType,
};
use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::error::ConfigError;
use crate::ident::is_identifier;
use crate::vocab::LoadedVocabulary;

const PROFILE_DOMAIN: &str = "core-memory/source-profile/v1";
const CHUNKER_DOMAIN: &str = "core-memory/chunker/v1";

/// Chunker ID for JSON content: the canonical record is one chunk.
pub const JSON_WHOLE: &str = "json.whole";
/// Revision of the `json.whole` algorithm.
pub const JSON_WHOLE_REV: u32 = 1;

const DEFAULT_MAX_EVENT_BYTES: u64 = 1 << 20;
/// Upper bound on `max_event_bytes` for any profile.
pub const MAX_EVENT_BYTES_LIMIT: u64 = 64 << 20;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceProfile {
    pub id: String,
    /// Bump whenever anything in the profile changes. The engine refuses to
    /// start when a stored profile revision has different contents.
    pub rev: u32,
    pub source_type: SourceType,
    /// The source's system name, stored with each event. Defaults to the
    /// first `/`-separated segment of the event's `source_ns`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_system: Option<String>,
    pub content: ContentKind,
    /// Whether the source edits objects in place. When true, a changed event
    /// without a source revision is admitted as a new engine-assigned
    /// revision instead of an identity conflict (ING-04).
    #[serde(default)]
    pub editable: bool,
    #[serde(default = "default_max_event_bytes")]
    pub max_event_bytes: u64,
    pub chunker: ChunkerSettings,
    /// The subset of the vocabulary this source may be classified into.
    /// Absent means the whole vocabulary.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub types: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub identifiers: Vec<IdentifierPattern>,
}

fn default_max_event_bytes() -> u64 {
    DEFAULT_MAX_EVENT_BYTES
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChunkerSettings {
    /// `text.structural` for text content, `json.whole` for JSON content.
    pub id: String,
    /// Bump whenever the settings change. Chunk IDs include it (CHK-05).
    pub rev: u32,
    pub max: u32,
    #[serde(default = "default_unit")]
    pub unit: LengthUnit,
}

fn default_unit() -> LengthUnit {
    LengthUnit::Bytes
}

/// A pattern for identifiers in source text, such as ticket keys.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentifierPattern {
    pub kind: String,
    pub pattern: String,
}

/// A validated profile, ready to use.
#[derive(Debug, Clone)]
pub struct CompiledProfile {
    pub def: SourceProfile,
    pub hash: Sha256Digest,
    pub chunker: CompiledChunker,
    pub chunker_ref: ChunkerRef,
    pub identifiers: Vec<(String, Regex)>,
}

#[derive(Debug, Clone, Copy)]
pub enum CompiledChunker {
    Text(TextChunkerConfig),
    JsonWhole { max: u32, unit: LengthUnit },
}

impl CompiledProfile {
    pub fn profile_ref(&self) -> ProfileRef {
        ProfileRef {
            id: self.def.id.clone(),
            rev: self.def.rev,
        }
    }

    /// The `source_system` for an event under this profile.
    pub fn source_system<'a>(&'a self, event: &'a SourceEvent) -> &'a str {
        match &self.def.source_system {
            Some(system) => system,
            None => event
                .source_ns
                .split('/')
                .next()
                .unwrap_or(&event.source_ns),
        }
    }

    pub fn compile(
        def: SourceProfile,
        vocab: Option<&LoadedVocabulary>,
    ) -> Result<Self, ConfigError> {
        // An id is repeated in an error only once it is known to be one.
        if !is_identifier(&def.id) {
            return Err(ConfigError::new(
                "a profile's id is not a valid identifier: lowercase letters and digits, \
                 in parts joined by `.`, `_` or `-`",
            ));
        }
        let err = |msg: String| ConfigError::new(format!("profile `{}`: {msg}", def.id));
        if def.rev == 0 {
            return Err(err("rev must be 1 or more".into()));
        }
        if def.max_event_bytes == 0 || def.max_event_bytes > MAX_EVENT_BYTES_LIMIT {
            return Err(err(format!(
                "max_event_bytes must be between 1 and {MAX_EVENT_BYTES_LIMIT}"
            )));
        }
        if let Some(system) = &def.source_system
            && (system.is_empty() || system.len() > 128)
        {
            return Err(err("source_system must be 1 to 128 bytes".into()));
        }
        let settings = &def.chunker;
        if settings.rev == 0 {
            return Err(err("chunker rev must be 1 or more".into()));
        }
        let (chunker, algorithm_rev) = match (def.content, settings.id.as_str()) {
            (ContentKind::Text, TEXT_ALGORITHM) => {
                let cfg = TextChunkerConfig {
                    max: settings.max,
                    unit: settings.unit,
                };
                cfg.validate().map_err(|e| err(e.to_string()))?;
                (CompiledChunker::Text(cfg), ALGORITHM_REV)
            }
            (ContentKind::Json, JSON_WHOLE) => {
                if settings.max == 0 {
                    return Err(err("chunker max must be 1 or more".into()));
                }
                (
                    CompiledChunker::JsonWhole {
                        max: settings.max,
                        unit: settings.unit,
                    },
                    JSON_WHOLE_REV,
                )
            }
            (content, id) => {
                // The id is repeated only if it is one of the two. Anything
                // else may be what was meant for another setting.
                let which = if [TEXT_ALGORITHM, JSON_WHOLE].contains(&id) {
                    format!("chunker `{id}` cannot chunk {content:?} content")
                } else {
                    "the chunker's id is not one this build has".to_string()
                };
                return Err(err(format!(
                    "{which} (use `{TEXT_ALGORITHM}` for text, `{JSON_WHOLE}` for json)"
                )));
            }
        };
        let chunker_hash = canonical_json_digest(
            CHUNKER_DOMAIN,
            &serde_json::json!({
                "id": settings.id,
                "rev": settings.rev,
                "max": settings.max,
                "unit": settings.unit,
                "algorithm_rev": algorithm_rev,
            }),
        )
        .map_err(|e| err(format!("cannot hash chunker settings: {e}")))?;

        let mut identifiers = Vec::new();
        for p in &def.identifiers {
            if !is_identifier(&p.kind) {
                return Err(err("an identifier's kind is not a valid identifier".into()));
            }
            // The regex crate's own message quotes the pattern, which is
            // not repeated: only which pattern it is, and how it failed.
            let re = Regex::new(&p.pattern).map_err(|e| {
                let how = match e {
                    regex::Error::CompiledTooBig(_) => "is too large once compiled",
                    _ => "is not a valid regular expression",
                };
                err(format!("the pattern of identifier `{}` {how}", p.kind))
            })?;
            identifiers.push((p.kind.clone(), re));
        }

        if let Some(types) = &def.types {
            let Some(vocab) = vocab else {
                return Err(err("declares types but no vocabulary is configured".into()));
            };
            for key in types {
                if vocab.get(key).is_none() {
                    // A key is repeated only if it could be one.
                    return Err(err(if is_identifier(key) {
                        format!("type `{key}` is not in the vocabulary")
                    } else {
                        "an entry of `types` is not a type's key, so it is not in the vocabulary"
                            .to_string()
                    }));
                }
            }
        }

        let hash = canonical_json_digest(PROFILE_DOMAIN, &def)
            .map_err(|e| err(format!("cannot hash profile: {e}")))?;
        let chunker_ref = ChunkerRef {
            id: settings.id.clone(),
            rev: settings.rev,
            config_hash: chunker_hash,
        };
        Ok(Self {
            def,
            hash,
            chunker,
            chunker_ref,
            identifiers,
        })
    }
}

/// Generic profiles shipped with the engine. Changing one requires bumping
/// its `rev`; a stored pin refuses the old revision with new contents.
pub fn builtin_profiles() -> Vec<SourceProfile> {
    let text = |id: &str, source_type, max| SourceProfile {
        id: id.into(),
        rev: 1,
        source_type,
        source_system: None,
        content: ContentKind::Text,
        editable: false,
        max_event_bytes: DEFAULT_MAX_EVENT_BYTES,
        chunker: ChunkerSettings {
            id: TEXT_ALGORITHM.into(),
            rev: 1,
            max,
            unit: LengthUnit::Bytes,
        },
        types: None,
        identifiers: Vec::new(),
    };
    vec![
        text("core.plain_text", SourceType::Document, 2048),
        text("core.chat_transcript", SourceType::Transcript, 4096),
        text("core.document", SourceType::Document, 2048),
        SourceProfile {
            id: "core.json_record".into(),
            rev: 1,
            source_type: SourceType::Relational,
            source_system: None,
            content: ContentKind::Json,
            editable: true,
            max_event_bytes: DEFAULT_MAX_EVENT_BYTES,
            chunker: ChunkerSettings {
                id: JSON_WHOLE.into(),
                rev: 1,
                max: 64 * 1024,
                unit: LengthUnit::Bytes,
            },
            types: None,
            identifiers: Vec::new(),
        },
    ]
}

/// All profiles the engine can admit events under.
#[derive(Debug, Clone, Default)]
pub struct ProfileRegistry {
    profiles: BTreeMap<String, CompiledProfile>,
}

impl ProfileRegistry {
    /// Built-in profiles plus `extra`. An extra profile may not reuse a
    /// built-in ID.
    pub fn new(
        extra: Vec<SourceProfile>,
        vocab: Option<&LoadedVocabulary>,
    ) -> Result<Self, ConfigError> {
        let mut profiles = BTreeMap::new();
        for def in builtin_profiles().into_iter().chain(extra) {
            let id = def.id.clone();
            let compiled = CompiledProfile::compile(def, vocab)?;
            if profiles.insert(id.clone(), compiled).is_some() {
                return Err(ConfigError::new(format!("profile `{id}` is defined twice")));
            }
        }
        Ok(Self { profiles })
    }

    pub fn get(&self, id: &str) -> Option<&CompiledProfile> {
        self.profiles.get(id)
    }

    pub fn iter(&self) -> impl Iterator<Item = &CompiledProfile> {
        self.profiles.values()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::vocab::{TypeDef, Vocabulary};

    fn vocab() -> LoadedVocabulary {
        LoadedVocabulary::load(Vocabulary {
            id: "acme.types".into(),
            types: vec![
                TypeDef {
                    key: "outcome".into(),
                    rev: 1,
                    definition: None,
                },
                TypeDef {
                    key: "decision".into(),
                    rev: 1,
                    definition: None,
                },
            ],
        })
        .unwrap()
    }

    fn crm_profile() -> SourceProfile {
        toml::from_str(
            r#"
            id = "acme.crm"
            rev = 1
            source_type = "relational"
            source_system = "salesforce"
            content = "json"
            types = ["outcome"]
            chunker = { id = "json.whole", rev = 1, max = 65536 }
            "#,
        )
        .unwrap()
    }

    #[test]
    fn builtins_compile() {
        let registry = ProfileRegistry::new(Vec::new(), None).unwrap();
        assert!(registry.get("core.chat_transcript").is_some());
        assert!(registry.get("core.json_record").is_some());
    }

    #[test]
    fn a_profile_cannot_assign_types() {
        let with_rules = r#"
            id = "acme.crm"
            rev = 1
            source_type = "relational"
            content = "json"
            chunker = { id = "json.whole", rev = 1, max = 65536 }

            [[rules]]
            id = "stage-change"
            assign = "outcome"
            when = { "record.action" = "closed_won" }
        "#;
        let err = toml::from_str::<SourceProfile>(with_rules).unwrap_err();
        assert!(err.to_string().contains("unknown field `rules`"), "{err}");
    }

    #[test]
    fn types_outside_the_vocabulary_are_refused() {
        let mut p = crm_profile();
        p.types = Some(vec!["made_up".into()]);
        let e = CompiledProfile::compile(p, Some(&vocab())).unwrap_err();
        assert!(e.to_string().contains("is not in the vocabulary"), "{e}");
    }

    #[test]
    fn chunker_must_fit_the_content_kind() {
        let mut p = crm_profile();
        p.chunker.id = TEXT_ALGORITHM.into();
        let e = CompiledProfile::compile(p, Some(&vocab())).unwrap_err();
        assert!(
            e.to_string()
                .contains("chunker `text.structural` cannot chunk Json content"),
            "{e}"
        );
    }

    #[test]
    fn a_value_that_failed_its_check_is_not_repeated() {
        // A connection string written where another setting belongs. It
        // holds nothing the last line of defence looks for.
        let stray = "host=db user=engine password = hunter2 dbname=memory";
        let v = vocab();
        let mut chunker = crm_profile();
        chunker.chunker.id = stray.into();
        let mut types = crm_profile();
        types.types = Some(vec![stray.into()]);
        let mut pattern = crm_profile();
        pattern.identifiers = vec![IdentifierPattern {
            kind: "ticket".into(),
            pattern: format!("({stray}"),
        }];
        let mut too_big = crm_profile();
        too_big.identifiers = vec![IdentifierPattern {
            kind: "ticket".into(),
            pattern: format!("(?:{stray}){{1000}}{{1000}}{{1000}}"),
        }];
        for (what, profile, says) in [
            (
                "chunker",
                chunker,
                "the chunker's id is not one this build has",
            ),
            ("types", types, "an entry of `types` is not a type's key"),
            (
                "pattern",
                pattern,
                "the pattern of identifier `ticket` is not a valid regular expression",
            ),
            (
                "a large pattern",
                too_big,
                "the pattern of identifier `ticket` is too large once compiled",
            ),
        ] {
            let e = CompiledProfile::compile(profile, Some(&v))
                .unwrap_err()
                .to_string();
            assert!(e.contains(says), "{what}: {e}");
            for part in ["hunter2", "engine", "dbname", "password"] {
                assert!(!e.contains(part), "{what} repeats `{part}`: {e}");
            }
        }
    }

    #[test]
    fn hash_changes_with_any_setting() {
        let v = vocab();
        let a = CompiledProfile::compile(crm_profile(), Some(&v)).unwrap();
        let mut changed = crm_profile();
        changed.chunker.max = 1000;
        let b = CompiledProfile::compile(changed, Some(&v)).unwrap();
        assert_ne!(a.hash, b.hash);
        assert_ne!(a.chunker_ref.config_hash, b.chunker_ref.config_hash);
    }

    #[test]
    fn duplicate_profile_ids_are_refused() {
        let mut p = crm_profile();
        p.id = "core.document".into();
        p.types = None;
        assert!(ProfileRegistry::new(vec![p], None).is_err());
    }
}

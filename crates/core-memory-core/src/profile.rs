//! Source profiles (ING-07): declarative configuration describing how to
//! admit, identify, chunk, facet and classify one kind of source.

use std::collections::BTreeMap;

use core_memory_chunker::{
    ALGORITHM as TEXT_ALGORITHM, ALGORITHM_REV, LengthUnit, TextChunkerConfig,
};
use core_memory_contract::hash::canonical_json_digest;
use core_memory_contract::{
    ChunkerRef, ContentKind, ProfileRef, RecordObject, Sha256Digest, SourceEvent, SourceType,
    TypeRef,
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
    /// System name for sinks. Defaults to the first `/`-separated segment of
    /// each event's `source_ns`.
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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rules: Vec<Rule>,
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

/// A classification rule for sources whose schema declares what kind of
/// event each record is (CLS-02 tier 1). All conditions must hold.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rule {
    pub id: String,
    /// Type key to assign.
    pub assign: String,
    /// Field to accepted values. Fields: `record.system`,
    /// `record.object_type`, `record.action`, `attributes.<key>`.
    pub when: BTreeMap<String, Matcher>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Matcher {
    One(String),
    Any(Vec<String>),
}

impl Matcher {
    fn values(&self) -> Vec<String> {
        match self {
            Matcher::One(v) => vec![v.clone()],
            Matcher::Any(vs) => vs.clone(),
        }
    }
}

/// A validated profile, ready to use.
#[derive(Debug, Clone)]
pub struct CompiledProfile {
    pub def: SourceProfile,
    pub hash: Sha256Digest,
    pub chunker: CompiledChunker,
    pub chunker_ref: ChunkerRef,
    pub identifiers: Vec<(String, Regex)>,
    pub rules: Vec<CompiledRule>,
}

#[derive(Debug, Clone, Copy)]
pub enum CompiledChunker {
    Text(TextChunkerConfig),
    JsonWhole { max: u32, unit: LengthUnit },
}

#[derive(Debug, Clone)]
pub struct CompiledRule {
    pub id: String,
    pub type_ref: TypeRef,
    conditions: Vec<(RuleField, Vec<String>)>,
}

#[derive(Debug, Clone)]
enum RuleField {
    RecordSystem,
    RecordObjectType,
    RecordAction,
    Attribute(String),
}

impl RuleField {
    fn parse(name: &str) -> Option<Self> {
        match name {
            "record.system" => Some(RuleField::RecordSystem),
            "record.object_type" => Some(RuleField::RecordObjectType),
            "record.action" => Some(RuleField::RecordAction),
            _ => name
                .strip_prefix("attributes.")
                .filter(|k| !k.is_empty())
                .map(|k| RuleField::Attribute(k.to_string())),
        }
    }

    fn value<'a>(&self, event: &'a SourceEvent) -> Option<&'a str> {
        let record = || event.structure.record.as_ref();
        match self {
            RuleField::RecordSystem => record().map(|r: &RecordObject| r.system.as_str()),
            RuleField::RecordObjectType => record().map(|r| r.object_type.as_str()),
            RuleField::RecordAction => record().map(|r| r.action.as_str()),
            RuleField::Attribute(k) => event.attributes.get(k).map(String::as_str),
        }
    }
}

impl CompiledRule {
    pub fn matches(&self, event: &SourceEvent) -> bool {
        self.conditions.iter().all(|(field, values)| {
            field
                .value(event)
                .is_some_and(|v| values.iter().any(|x| x == v))
        })
    }
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
        let err = |msg: String| ConfigError::new(format!("profile `{}`: {msg}", def.id));
        if !is_identifier(&def.id) {
            return Err(err("id is not a valid identifier".into()));
        }
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
                return Err(err(format!(
                    "chunker `{id}` cannot chunk {content:?} content (use `{TEXT_ALGORITHM}` for text, `{JSON_WHOLE}` for json)"
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
                return Err(err(format!(
                    "identifier kind `{}` is not a valid identifier",
                    p.kind
                )));
            }
            let re = Regex::new(&p.pattern)
                .map_err(|e| err(format!("identifier `{}` pattern: {e}", p.kind)))?;
            identifiers.push((p.kind.clone(), re));
        }

        if let Some(types) = &def.types {
            let Some(vocab) = vocab else {
                return Err(err("declares types but no vocabulary is configured".into()));
            };
            for key in types {
                if vocab.get(key).is_none() {
                    return Err(err(format!("type `{key}` is not in the vocabulary")));
                }
            }
        }

        let mut rules = Vec::new();
        for rule in &def.rules {
            if !is_identifier(&rule.id) {
                return Err(err(format!(
                    "rule id `{}` is not a valid identifier",
                    rule.id
                )));
            }
            if rules.iter().any(|r: &CompiledRule| r.id == rule.id) {
                return Err(err(format!("rule `{}` is defined twice", rule.id)));
            }
            let Some(vocab) = vocab else {
                return Err(err("has rules but no vocabulary is configured".into()));
            };
            let type_ref = vocab
                .get(&rule.assign)
                .ok_or_else(|| {
                    err(format!(
                        "rule `{}` assigns unknown type `{}`",
                        rule.id, rule.assign
                    ))
                })?
                .clone();
            if let Some(types) = &def.types
                && !types.contains(&rule.assign)
            {
                return Err(err(format!(
                    "rule `{}` assigns `{}`, which is outside the profile's types",
                    rule.id, rule.assign
                )));
            }
            if rule.when.is_empty() {
                return Err(err(format!("rule `{}` has no conditions", rule.id)));
            }
            let mut conditions = Vec::new();
            for (field, matcher) in &rule.when {
                let parsed = RuleField::parse(field).ok_or_else(|| {
                    err(format!(
                        "rule `{}` uses unsupported field `{field}` (container labels such as source type are never evidence of type)",
                        rule.id
                    ))
                })?;
                let values = matcher.values();
                if values.is_empty() {
                    return Err(err(format!(
                        "rule `{}` field `{field}` lists no values",
                        rule.id
                    )));
                }
                conditions.push((parsed, values));
            }
            rules.push(CompiledRule {
                id: rule.id.clone(),
                type_ref,
                conditions,
            });
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
            rules,
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
        rules: Vec::new(),
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
            rules: Vec::new(),
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

            [[rules]]
            id = "stage-change"
            assign = "outcome"
            when = { "record.object_type" = "Opportunity", "record.action" = ["closed_won", "closed_lost"] }
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
    fn rules_compile_against_the_vocabulary() {
        let v = vocab();
        let compiled = CompiledProfile::compile(crm_profile(), Some(&v)).unwrap();
        assert_eq!(compiled.rules[0].type_ref.key, "outcome");
    }

    #[test]
    fn rules_outside_the_type_subset_are_refused() {
        let mut p = crm_profile();
        p.rules[0].assign = "decision".into();
        let e = CompiledProfile::compile(p, Some(&vocab())).unwrap_err();
        assert!(e.to_string().contains("outside the profile's types"), "{e}");
    }

    #[test]
    fn container_labels_are_not_rule_fields() {
        let mut p = crm_profile();
        p.rules[0]
            .when
            .insert("source_type".into(), Matcher::One("transcript".into()));
        assert!(CompiledProfile::compile(p, Some(&vocab())).is_err());
    }

    #[test]
    fn chunker_must_fit_the_content_kind() {
        let mut p = crm_profile();
        p.chunker.id = TEXT_ALGORITHM.into();
        assert!(CompiledProfile::compile(p, Some(&vocab())).is_err());
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
        p.rules.clear();
        p.types = None;
        assert!(ProfileRegistry::new(vec![p], None).is_err());
    }
}

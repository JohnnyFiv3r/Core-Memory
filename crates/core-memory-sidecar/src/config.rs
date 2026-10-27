//! The `core-memory.toml` file.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use core_memory_contract::Sha256Digest;
use core_memory_core::{
    BindingConfig, ClassifierConfig, EngineConfig, Limits, ScopeGrants, SourceProfile, Vocabulary,
    is_identifier,
};
use serde::Deserialize;

const DEFAULT_MAX_RECORD_BYTES: u64 = 8 << 20;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SidecarConfig {
    pub store: StoreConfig,
    pub grants: GrantsConfig,
    #[serde(default, rename = "sink")]
    pub sinks: Vec<SinkConfig>,
    #[serde(default)]
    pub vocabulary: Option<Vocabulary>,
    #[serde(default, rename = "profile")]
    pub profiles: Vec<SourceProfile>,
    #[serde(default)]
    pub limits: Limits,
    /// Settings for `core-memory serve`.
    #[serde(default)]
    pub http: Option<HttpConfig>,
    /// Enables model classification through one of the bindings.
    #[serde(default)]
    pub classifier: Option<ClassifierConfig>,
    #[serde(default, rename = "binding")]
    pub bindings: Vec<BindingConfig>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HttpConfig {
    #[serde(default = "default_listen")]
    pub listen: String,
    /// Largest request body accepted, in bytes.
    #[serde(default = "default_max_body_bytes")]
    pub max_body_bytes: usize,
    /// Most events in one batch request.
    #[serde(default = "default_max_batch_events")]
    pub max_batch_events: usize,
    /// How often the delivery thread checks the outbox when nothing wakes it.
    #[serde(default = "default_delivery_interval_ms")]
    pub delivery_interval_ms: u64,
    /// Callers. Each token is stored only as its SHA-256; generate one with
    /// `core-memory token`.
    #[serde(default, rename = "token")]
    pub tokens: Vec<TokenConfig>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TokenConfig {
    pub id: String,
    pub sha256: Sha256Digest,
    /// Scopes this caller may write. `["*"]` grants every scope.
    pub scopes: Vec<String>,
}

fn default_listen() -> String {
    "127.0.0.1:7600".into()
}

fn default_max_body_bytes() -> usize {
    16 << 20
}

fn default_max_batch_events() -> usize {
    256
}

fn default_delivery_interval_ms() -> u64 {
    500
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StoreConfig {
    /// SQLite file, relative to the config file.
    pub path: PathBuf,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrantsConfig {
    /// Scopes this process may write. `["*"]` grants every scope.
    pub scopes: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SinkConfig {
    pub id: String,
    pub kind: SinkKind,
    /// `jsonl`: the output file, relative to the config file.
    #[serde(default)]
    pub path: Option<PathBuf>,
    /// `webhook`: the receiver's URL.
    #[serde(default)]
    pub url: Option<String>,
    /// `webhook`: the environment variable holding the signing secret.
    #[serde(default)]
    pub secret_env: Option<String>,
    /// `webhook`: the limit for one request, from connecting to the reply.
    #[serde(default = "default_sink_timeout_ms")]
    pub timeout_ms: u64,
    /// `webhook`: the receiver keeps the first record per key and answers
    /// conflicts (REC-07).
    #[serde(default)]
    pub system_of_record: bool,
    /// `webhook`: the most bytes the receiver takes in one request body.
    #[serde(default)]
    pub max_request_bytes: Option<u64>,
    /// Required sinks apply backpressure and hold records until delivered.
    #[serde(default = "default_true")]
    pub required: bool,
    #[serde(default = "default_max_record_bytes")]
    pub max_record_bytes: u64,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SinkKind {
    Jsonl,
    Webhook,
}

fn default_sink_timeout_ms() -> u64 {
    10_000
}

fn default_true() -> bool {
    true
}

fn default_max_record_bytes() -> u64 {
    DEFAULT_MAX_RECORD_BYTES
}

/// Setting names, each with whether the sink sets it.
type Settings<'a> = &'a [(&'a str, bool)];

impl SinkConfig {
    /// Each kind takes its own settings and no other kind's.
    fn check_kind(&self) -> Result<(), String> {
        let (needs, refuses): (Settings<'_>, Settings<'_>) = match self.kind {
            SinkKind::Jsonl => (
                &[("path", self.path.is_some())],
                &[
                    ("url", self.url.is_some()),
                    ("secret_env", self.secret_env.is_some()),
                    ("system_of_record", self.system_of_record),
                    ("max_request_bytes", self.max_request_bytes.is_some()),
                ],
            ),
            SinkKind::Webhook => (
                &[
                    ("url", self.url.is_some()),
                    ("secret_env", self.secret_env.is_some()),
                ],
                &[("path", self.path.is_some())],
            ),
        };
        let kind = format!("{:?}", self.kind).to_lowercase();
        if let Some((name, _)) = needs.iter().find(|(_, set)| !set) {
            return Err(format!("sink `{}` ({kind}) needs `{name}`", self.id));
        }
        if let Some((name, _)) = refuses.iter().find(|(_, set)| *set) {
            return Err(format!(
                "sink `{}` ({kind}) does not take `{name}`",
                self.id
            ));
        }
        Ok(())
    }
}

/// A loaded config with paths resolved against the config file's folder.
#[derive(Debug)]
pub struct Loaded {
    pub config: SidecarConfig,
    pub base: PathBuf,
}

impl Loaded {
    pub fn read(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let config: SidecarConfig =
            toml::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        let base = path
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
        let loaded = Self { config, base };
        loaded.validate()?;
        Ok(loaded)
    }

    fn validate(&self) -> Result<(), String> {
        let c = &self.config;
        if c.sinks.is_empty() {
            return Err(
                "configure at least one [[sink]]; records would otherwise be discarded".into(),
            );
        }
        let mut ids = BTreeSet::new();
        for sink in &c.sinks {
            if !is_identifier(&sink.id) {
                return Err(format!("sink id `{}` is not a valid identifier", sink.id));
            }
            if !ids.insert(&sink.id) {
                return Err(format!("sink `{}` is configured twice", sink.id));
            }
            sink.check_kind()?;
        }
        if !c.sinks.iter().any(|s| s.required) {
            return Err("at least one sink must be required".into());
        }
        check_scopes("grants.scopes", &c.grants.scopes)?;
        if let Some(http) = &c.http {
            let mut ids = BTreeSet::new();
            let mut hashes = BTreeSet::new();
            for t in &http.tokens {
                if !is_identifier(&t.id) {
                    return Err(format!(
                        "http token id `{}` is not a valid identifier",
                        t.id
                    ));
                }
                if !ids.insert(&t.id) {
                    return Err(format!("http token `{}` is configured twice", t.id));
                }
                if !hashes.insert(t.sha256) {
                    return Err(format!("http token `{}` reuses another token's hash", t.id));
                }
                check_scopes(&format!("http token `{}` scopes", t.id), &t.scopes)?;
            }
            if http.max_batch_events == 0 || http.max_body_bytes == 0 {
                return Err(
                    "http.max_batch_events and http.max_body_bytes must be 1 or more".into(),
                );
            }
        }
        Ok(())
    }

    pub fn resolve(&self, path: &Path) -> PathBuf {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.base.join(path)
        }
    }

    /// Grants for the local command-line caller.
    pub fn grants(&self) -> ScopeGrants {
        grants_from(&self.config.grants.scopes)
    }

    pub fn required_sinks(&self) -> Vec<String> {
        self.config
            .sinks
            .iter()
            .filter(|s| s.required)
            .map(|s| s.id.clone())
            .collect()
    }

    pub fn engine_config(&self) -> EngineConfig {
        EngineConfig {
            vocabulary: self.config.vocabulary.clone(),
            profiles: self.config.profiles.clone(),
            limits: self.config.limits,
            classifier: self.config.classifier.clone(),
            bindings: self.config.bindings.clone(),
        }
    }
}

fn check_scopes(what: &str, scopes: &[String]) -> Result<(), String> {
    if scopes.is_empty() {
        return Err(format!("{what} is empty; nothing could be admitted"));
    }
    if scopes.iter().any(|s| s == "*") && scopes.len() > 1 {
        return Err(format!("{what}: `*` must be the only entry"));
    }
    Ok(())
}

/// `["*"]` grants every scope; anything else grants exactly the listed ones.
pub fn grants_from(scopes: &[String]) -> ScopeGrants {
    if scopes.len() == 1 && scopes[0] == "*" {
        ScopeGrants::All
    } else {
        ScopeGrants::Only(scopes.iter().cloned().collect())
    }
}

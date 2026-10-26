//! The `core-memory.toml` file.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use core_memory_core::{
    EngineConfig, Limits, ScopeGrants, SourceProfile, Vocabulary, is_identifier,
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
    /// Output file, relative to the config file.
    pub path: PathBuf,
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
}

fn default_true() -> bool {
    true
}

fn default_max_record_bytes() -> u64 {
    DEFAULT_MAX_RECORD_BYTES
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
        }
        if !c.sinks.iter().any(|s| s.required) {
            return Err("at least one sink must be required".into());
        }
        if c.grants.scopes.is_empty() {
            return Err("grants.scopes is empty; nothing could be admitted".into());
        }
        if c.grants.scopes.iter().any(|s| s == "*") && c.grants.scopes.len() > 1 {
            return Err("grants.scopes: `*` must be the only entry".into());
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

    pub fn grants(&self) -> ScopeGrants {
        let scopes = &self.config.grants.scopes;
        if scopes.len() == 1 && scopes[0] == "*" {
            ScopeGrants::All
        } else {
            ScopeGrants::Only(scopes.iter().cloned().collect())
        }
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
        }
    }
}

//! The type vocabulary (CLS-01).
//!
//! There is one vocabulary, supplied by the configured provider. When
//! memoriesQL is attached it is memoriesQL's governed registry, exported and
//! loaded here; the engine never registers or shadows its keys.

use std::collections::BTreeMap;

use core_memory_contract::hash::canonical_json_digest;
use core_memory_contract::{CatalogRef, TypeRef};
use serde::{Deserialize, Serialize};

use crate::error::ConfigError;
use crate::ident::is_identifier;

const CATALOG_DOMAIN: &str = "core-memory/type-catalog/v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Vocabulary {
    pub id: String,
    pub types: Vec<TypeDef>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TypeDef {
    pub key: String,
    pub rev: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub definition: Option<String>,
}

/// A validated vocabulary with its catalog hash.
#[derive(Debug, Clone)]
pub struct LoadedVocabulary {
    catalog: CatalogRef,
    by_key: BTreeMap<String, TypeRef>,
    definitions: BTreeMap<String, String>,
}

impl LoadedVocabulary {
    pub fn load(vocabulary: Vocabulary) -> Result<Self, ConfigError> {
        if !is_identifier(&vocabulary.id) {
            return Err(ConfigError::new(format!(
                "vocabulary id `{}` is not a valid identifier",
                vocabulary.id
            )));
        }
        let mut by_key = BTreeMap::new();
        let mut definitions = BTreeMap::new();
        for t in &vocabulary.types {
            if !is_identifier(&t.key) {
                return Err(ConfigError::new(format!(
                    "type key `{}` is not a valid identifier",
                    t.key
                )));
            }
            if t.rev == 0 {
                return Err(ConfigError::new(format!(
                    "type `{}` needs a rev of 1 or more",
                    t.key
                )));
            }
            let type_ref = TypeRef {
                key: t.key.clone(),
                rev: t.rev,
            };
            if let Some(d) = &t.definition {
                definitions.insert(t.key.clone(), d.clone());
            }
            if by_key.insert(t.key.clone(), type_ref).is_some() {
                return Err(ConfigError::new(format!(
                    "type `{}` is defined twice",
                    t.key
                )));
            }
        }
        let hash = canonical_json_digest(CATALOG_DOMAIN, &vocabulary)
            .map_err(|e| ConfigError::new(format!("cannot hash vocabulary: {e}")))?;
        Ok(Self {
            catalog: CatalogRef {
                id: vocabulary.id,
                hash,
            },
            by_key,
            definitions,
        })
    }

    pub fn catalog(&self) -> &CatalogRef {
        &self.catalog
    }

    pub fn get(&self, key: &str) -> Option<&TypeRef> {
        self.by_key.get(key)
    }

    pub fn definition(&self, key: &str) -> Option<&str> {
        self.definitions.get(key).map(String::as_str)
    }

    /// All type keys, in order.
    pub fn keys(&self) -> impl Iterator<Item = &str> {
        self.by_key.keys().map(String::as_str)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vocab(types: &[(&str, u32)]) -> Vocabulary {
        Vocabulary {
            id: "acme.types".into(),
            types: types
                .iter()
                .map(|(k, r)| TypeDef {
                    key: (*k).into(),
                    rev: *r,
                    definition: None,
                })
                .collect(),
        }
    }

    #[test]
    fn loads_and_hashes() {
        let a = LoadedVocabulary::load(vocab(&[("decision", 1), ("action", 2)])).unwrap();
        assert_eq!(a.get("action").unwrap().rev, 2);
        let b = LoadedVocabulary::load(vocab(&[("decision", 1), ("action", 3)])).unwrap();
        assert_ne!(a.catalog().hash, b.catalog().hash);
    }

    #[test]
    fn refuses_duplicates_and_bad_keys() {
        assert!(LoadedVocabulary::load(vocab(&[("decision", 1), ("decision", 2)])).is_err());
        assert!(LoadedVocabulary::load(vocab(&[("Decision", 1)])).is_err());
        assert!(LoadedVocabulary::load(vocab(&[("decision", 0)])).is_err());
    }
}

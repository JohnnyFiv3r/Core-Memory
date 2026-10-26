//! JSON Schemas for the published contracts (REC-01).
//!
//! Each schema is published with a content hash so sinks and producers can
//! pin the exact contract they were built against.

use serde_json::Value;

use crate::event::SourceEvent;
use crate::hash::{Sha256Digest, canonical_json_digest};
use crate::outcome::AdmissionOutcome;
use crate::record::Record;

const SCHEMA_DIGEST_DOMAIN: &str = "core-memory/schema/v1";

/// The contracts this crate publishes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Contract {
    SourceEvent,
    AdmissionOutcome,
    Record,
}

impl Contract {
    pub const ALL: [Contract; 3] = [
        Contract::SourceEvent,
        Contract::AdmissionOutcome,
        Contract::Record,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Contract::SourceEvent => "source_event",
            Contract::AdmissionOutcome => "admission_outcome",
            Contract::Record => "record",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|c| c.name() == name)
    }

    pub fn schema(self) -> Value {
        let schema = match self {
            Contract::SourceEvent => schemars::schema_for!(SourceEvent),
            Contract::AdmissionOutcome => schemars::schema_for!(AdmissionOutcome),
            Contract::Record => schemars::schema_for!(Record),
        };
        schema.to_value()
    }

    /// Hash of the schema in canonical JSON form.
    pub fn digest(self) -> Sha256Digest {
        canonical_json_digest(SCHEMA_DIGEST_DOMAIN, &self.schema()).unwrap_or(Sha256Digest([0; 32]))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schemas_generate_and_hash_stably() {
        for contract in Contract::ALL {
            let schema = contract.schema();
            assert!(schema.is_object(), "{}", contract.name());
            assert_eq!(contract.digest(), contract.digest());
            assert_eq!(Contract::from_name(contract.name()), Some(contract));
        }
    }

    #[test]
    fn record_schema_names_the_record_kinds() {
        let text = Contract::Record.schema().to_string();
        assert!(text.contains("\"chunk\""));
        assert!(text.contains("\"type_decision\""));
    }
}

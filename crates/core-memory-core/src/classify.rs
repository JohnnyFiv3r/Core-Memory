//! Classification (CLS-02): profile rules, then a stock model, then
//! `pending`.
//!
//! Model classification is not wired in yet, so a chunk that no rule types
//! is `pending{no_classifier}`. It stays retryable: adding a classifier
//! binding completes it later with a type decision record.

use core_memory_contract::{
    ChunkId, Decision, DecisionId, DecisionMethod, PendingReason, SourceEvent, Timestamp,
    TypeOutcome,
};

use crate::profile::CompiledProfile;

/// The type outcome of a chunk and, for completed decisions, its provenance.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Classification {
    pub outcome: TypeOutcome,
    pub decision: Option<Decision>,
}

/// Classifies one chunk. Rules match on the event's declared structure, so
/// every chunk of an event gets the same rule outcome.
pub fn classify(
    profile: &CompiledProfile,
    event: &SourceEvent,
    chunk_id: &ChunkId,
    now: Timestamp,
) -> Classification {
    for rule in &profile.rules {
        if rule.matches(event) {
            let rev = profile.def.rev.to_string();
            let type_rev = rule.type_ref.rev.to_string();
            let decision_id = DecisionId::derive(
                chunk_id,
                &[
                    "rule",
                    &profile.def.id,
                    &rev,
                    &rule.id,
                    &rule.type_ref.key,
                    &type_rev,
                ],
            );
            return Classification {
                outcome: TypeOutcome::Assigned {
                    type_ref: rule.type_ref.clone(),
                },
                decision: Some(Decision {
                    decision_id,
                    method: DecisionMethod::Rule {
                        profile: profile.profile_ref(),
                        rule_id: rule.id.clone(),
                    },
                    decided_at: now,
                }),
            };
        }
    }
    Classification {
        outcome: TypeOutcome::Pending {
            reason: PendingReason::NoClassifier,
        },
        decision: None,
    }
}

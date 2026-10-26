//! The classification pass: model decisions for queued chunks.
//!
//! Each pass releases held chunk records whose deadline passed (as
//! `pending`, CLS-05), then claims due batches. Every model call is charged
//! to the budget before it is sent and recorded in the attempt journal
//! (MOD-04). Final decisions leave the queue exactly once: as the held chunk
//! record if it is still held, otherwise as a type decision record (CLS-04,
//! REC-03). No store transaction is open while a model call runs (RUN-02).

use std::collections::BTreeSet;

use core_memory_contract::record::type_decision_record_key;
use core_memory_contract::{
    Catalogs, Decision, DecisionId, DecisionMethod, FieldHasher, PendingReason, RECORD_CONTRACT_V1,
    Record, RecordBody, Timestamp, TypeDecisionRecord, TypeOutcome, UnassignedReason,
};

use crate::classifier::{CompiledClassifier, ItemDecision, TypeOption, render, repair, validate};
use crate::engine::Engine;
use crate::error::{EngineError, StoreError};
use crate::model::{ModelRequest, ModelTransport, TransportError, Usage};
use crate::store::{
    AttemptFinish, AttemptReservation, AttemptState, OperationalStore, OutboxRecord, QueueAction,
    QueueUpdate, QueuedChunk, ReserveOutcome,
};

/// Most held records released per pass.
const RELEASE_LIMIT: usize = 1024;

/// What one pass did.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ClassifyReport {
    /// Model calls sent, including repair calls.
    pub calls: u32,
    /// Replies rejected by the validator and retried once.
    pub repaired: u32,
    /// Chunks that got a final decision.
    pub decided: u32,
    /// Chunks put back for a later attempt.
    pub retried: u32,
    /// Held records delivered as pending because their deadline passed.
    pub released: u32,
    /// Chunks that ran out of attempts and stay pending.
    pub abandoned: u32,
    /// A batch was refused because its scope's budget for the day is spent.
    pub budget_exhausted: bool,
}

enum Call {
    Reply {
        text: String,
        attempt_id: String,
        usage: Usage,
    },
    Exhausted,
    Failed {
        reason: PendingReason,
        retry_after_ms: Option<u64>,
    },
}

impl Engine {
    /// Runs one classification pass: releases expired holds, then
    /// classifies up to `max_batches` due batches through `transport`.
    /// Does nothing when no classifier is configured.
    pub fn classify_due(
        &self,
        store: &mut dyn OperationalStore,
        transport: &dyn ModelTransport,
        max_batches: usize,
    ) -> Result<ClassifyReport, EngineError> {
        let mut report = ClassifyReport::default();
        let Some(c) = &self.classifier else {
            return Ok(report);
        };
        report.released = self.release_holds(store, self.clock.now())?;
        for _ in 0..max_batches {
            let now = self.clock.now();
            let batch = store.claim_classification(
                now,
                now.plus_millis(c.settings.lease_ms),
                c.settings.batch_size,
            )?;
            if batch.is_empty() {
                break;
            }
            self.classify_batch(store, transport, c, batch, &mut report)?;
        }
        Ok(report)
    }

    /// Delivers every held record whose deadline is at or before `at` as
    /// pending, with the last failure reason or `timeout`. The chunks stay
    /// queued, so a later decision arrives as a type decision record.
    pub fn release_holds(
        &self,
        store: &mut dyn OperationalStore,
        at: Timestamp,
    ) -> Result<u32, EngineError> {
        let mut released = 0;
        loop {
            let expired = store.expired_holds(at, RELEASE_LIMIT)?;
            if expired.is_empty() {
                return Ok(released);
            }
            let mut updates = Vec::with_capacity(expired.len());
            for q in expired {
                let reason = q.last_reason.unwrap_or(PendingReason::Timeout);
                let Some(held) = &q.held_record else { continue };
                updates.push(QueueUpdate {
                    chunk_id: q.chunk_id,
                    action: QueueAction::Retry {
                        reason,
                        next_attempt_at: None,
                        count_attempt: false,
                        release: Some(with_type(held, TypeOutcome::Pending { reason }, None)?),
                    },
                });
                released += 1;
            }
            store.apply_classification(updates)?;
        }
    }

    fn classify_batch(
        &self,
        store: &mut dyn OperationalStore,
        transport: &dyn ModelTransport,
        c: &CompiledClassifier,
        batch: Vec<QueuedChunk>,
        report: &mut ClassifyReport,
    ) -> Result<(), EngineError> {
        let now = self.clock.now();
        let (Some(profile), Some(vocab)) =
            (self.registry.get(&batch[0].profile_id), self.vocab.as_ref())
        else {
            // The profile or vocabulary is gone from the configuration.
            let updates = abandon(&batch, PendingReason::UnsupportedInput)?;
            report.abandoned += len32(&updates);
            return store.apply_classification(updates).map_err(Into::into);
        };
        let keys = self.allowed_types(profile);
        let options: Vec<TypeOption<'_>> = keys
            .iter()
            .map(|k| TypeOption {
                key: k,
                definition: vocab.definition(k),
            })
            .collect();
        let texts: Vec<&str> = batch.iter().map(|q| q.text.as_str()).collect();
        let allowed: BTreeSet<&str> = keys.iter().copied().collect();
        let request = render(&options, &texts, c.binding.max_output_tokens);

        let mut decisions = None;
        let mut pending = request.clone();
        for phase in ["initial", "repair"] {
            report.calls += 1;
            match self.call(store, transport, c, &batch, &pending, phase, now)? {
                Call::Exhausted => {
                    report.calls -= 1;
                    report.budget_exhausted = true;
                    let updates = retry(
                        c,
                        &batch,
                        PendingReason::Capacity,
                        now.next_utc_midnight(),
                        false,
                    )?;
                    report.retried += len32(&updates);
                    return store.apply_classification(updates).map_err(Into::into);
                }
                Call::Failed {
                    reason,
                    retry_after_ms,
                } => {
                    let delay = backoff(c, &batch).max(retry_after_ms.unwrap_or(0));
                    let updates = retry(c, &batch, reason, now.plus_millis(delay), true)?;
                    self.count(&updates, report);
                    return store.apply_classification(updates).map_err(Into::into);
                }
                Call::Reply {
                    text,
                    attempt_id,
                    usage,
                } => {
                    let checked = validate(&text, batch.len(), &allowed);
                    let state = if checked.is_ok() {
                        AttemptState::Succeeded
                    } else {
                        AttemptState::Rejected
                    };
                    store.finish_attempt(&AttemptFinish {
                        attempt_id,
                        state,
                        usage: Some(usage),
                        detail: checked.as_ref().err().map(|e| e.join("; ")),
                        finished_at: self.clock.now(),
                    })?;
                    match checked {
                        Ok(d) => {
                            decisions = Some(d);
                            break;
                        }
                        Err(errors) if phase == "initial" => {
                            report.repaired += 1;
                            pending = repair(&request, &text, &errors);
                        }
                        Err(_) => {}
                    }
                }
            }
        }
        let Some(decisions) = decisions else {
            let updates = retry(
                c,
                &batch,
                PendingReason::ProviderError,
                now.plus_millis(backoff(c, &batch)),
                true,
            )?;
            self.count(&updates, report);
            return store.apply_classification(updates).map_err(Into::into);
        };

        let decided_at = self.clock.now();
        let catalogs = Catalogs {
            type_catalog: Some(vocab.catalog().clone()),
            predicate: None,
        };
        let mut updates = Vec::with_capacity(batch.len());
        for (q, d) in batch.iter().zip(decisions) {
            let (outcome, key) = match d {
                ItemDecision::Type(t) => {
                    let type_ref = vocab.get(&t).cloned().ok_or_else(|| {
                        StoreError::Corrupt(format!(
                            "validated type `{t}` is not in the vocabulary"
                        ))
                    })?;
                    let key = format!("type:{}:{}", type_ref.key, type_ref.rev);
                    (TypeOutcome::Assigned { type_ref }, key)
                }
                ItemDecision::Abstain(reason) => (
                    TypeOutcome::Unassigned { reason },
                    format!("abstain:{}", abstain_name(reason)),
                ),
            };
            let decision = Decision {
                decision_id: DecisionId::derive(
                    &q.chunk_id,
                    &["model", &c.binding.id, &c.rev.to_hex(), &key],
                ),
                method: DecisionMethod::Model {
                    binding_ref: c.binding.id.clone(),
                    binding_rev: c.rev,
                },
                decided_at,
            };
            let if_held = q
                .held_record
                .as_ref()
                .map(|h| with_type(h, outcome.clone(), Some(decision.clone())))
                .transpose()?;
            let type_record = Record {
                contract: RECORD_CONTRACT_V1.to_string(),
                record_key: type_decision_record_key(&q.chunk_id),
                scope: q.scope.clone(),
                body: RecordBody::TypeDecision(Box::new(TypeDecisionRecord {
                    chunk_id: q.chunk_id,
                    event_id: q.event_id,
                    outcome,
                    decision,
                    catalogs: catalogs.clone(),
                })),
            };
            updates.push(QueueUpdate {
                chunk_id: q.chunk_id,
                action: QueueAction::Complete {
                    if_held,
                    if_released: OutboxRecord {
                        record_key: type_record.record_key.clone(),
                        record_json: serde_json::to_string(&type_record)?,
                    },
                },
            });
        }
        report.decided += len32(&updates);
        store.apply_classification(updates).map_err(Into::into)
    }

    /// Reserves budget, sends one request and classifies transport errors.
    /// Replies are returned with their attempt still open, so the caller can
    /// record whether validation passed.
    #[allow(clippy::too_many_arguments)]
    fn call(
        &self,
        store: &mut dyn OperationalStore,
        transport: &dyn ModelTransport,
        c: &CompiledClassifier,
        batch: &[QueuedChunk],
        request: &ModelRequest,
        phase: &str,
        now: Timestamp,
    ) -> Result<Call, EngineError> {
        let attempt_id = attempt_id(c, batch, phase, now);
        let reservation = AttemptReservation {
            attempt_id: attempt_id.clone(),
            binding_id: c.binding.id.clone(),
            binding_rev: c.rev,
            scope: batch[0].scope.clone(),
            day: now.utc_date(),
            input_tokens: estimate_input_tokens(request),
            output_tokens: u64::from(request.max_output_tokens),
            chunk_count: len32(batch),
            started_at: now,
            max_requests: c.binding.budget.max_requests_per_day,
            max_tokens: c.binding.budget.max_tokens_per_day,
        };
        if store.reserve_attempt(&reservation)? == ReserveOutcome::Exhausted {
            return Ok(Call::Exhausted);
        }
        match transport.send(&c.binding, request) {
            Ok(response) => Ok(Call::Reply {
                text: response.text,
                attempt_id,
                usage: response.usage,
            }),
            Err(e) => {
                let (state, reason, retry_after_ms) = match &e {
                    TransportError::Transient { retry_after_ms, .. } => (
                        AttemptState::TransientError,
                        PendingReason::ProviderError,
                        *retry_after_ms,
                    ),
                    TransportError::Permanent { .. } => (
                        AttemptState::PermanentError,
                        PendingReason::ProviderError,
                        None,
                    ),
                    TransportError::InDoubt { .. } => {
                        (AttemptState::InDoubt, PendingReason::Timeout, None)
                    }
                };
                store.finish_attempt(&AttemptFinish {
                    attempt_id,
                    state,
                    usage: None,
                    detail: Some(e.to_string()),
                    finished_at: self.clock.now(),
                })?;
                Ok(Call::Failed {
                    reason,
                    retry_after_ms,
                })
            }
        }
    }

    fn count(&self, updates: &[QueueUpdate], report: &mut ClassifyReport) {
        for u in updates {
            match u.action {
                QueueAction::Abandon { .. } => report.abandoned += 1,
                _ => report.retried += 1,
            }
        }
    }
}

/// Retries every chunk at `next`, or abandons those out of attempts.
fn retry(
    c: &CompiledClassifier,
    batch: &[QueuedChunk],
    reason: PendingReason,
    next: Timestamp,
    count_attempt: bool,
) -> Result<Vec<QueueUpdate>, EngineError> {
    batch
        .iter()
        .map(|q| {
            let attempts = q.attempts + u32::from(count_attempt);
            let action = if count_attempt && attempts >= c.settings.max_attempts {
                QueueAction::Abandon {
                    release: q
                        .held_record
                        .as_ref()
                        .map(|h| with_type(h, TypeOutcome::Pending { reason }, None))
                        .transpose()?,
                }
            } else {
                QueueAction::Retry {
                    reason,
                    next_attempt_at: Some(next),
                    count_attempt,
                    release: None,
                }
            };
            Ok(QueueUpdate {
                chunk_id: q.chunk_id,
                action,
            })
        })
        .collect()
}

fn abandon(batch: &[QueuedChunk], reason: PendingReason) -> Result<Vec<QueueUpdate>, EngineError> {
    batch
        .iter()
        .map(|q| {
            Ok(QueueUpdate {
                chunk_id: q.chunk_id,
                action: QueueAction::Abandon {
                    release: q
                        .held_record
                        .as_ref()
                        .map(|h| with_type(h, TypeOutcome::Pending { reason }, None))
                        .transpose()?,
                },
            })
        })
        .collect()
}

/// Exponential backoff from the batch's most-tried chunk, capped at 64x.
fn backoff(c: &CompiledClassifier, batch: &[QueuedChunk]) -> u64 {
    let attempts = batch.iter().map(|q| q.attempts).max().unwrap_or(0).min(6);
    c.settings.retry_backoff_ms.saturating_mul(1 << attempts)
}

/// A held chunk record with its type outcome and decision replaced.
fn with_type(
    held: &OutboxRecord,
    outcome: TypeOutcome,
    decision: Option<Decision>,
) -> Result<OutboxRecord, EngineError> {
    let mut record: Record = serde_json::from_str(&held.record_json)?;
    match &mut record.body {
        RecordBody::Chunk(chunk) => {
            chunk.memory.type_outcome = outcome;
            chunk.decision = decision;
        }
        RecordBody::TypeDecision(_) => {
            return Err(StoreError::Corrupt(format!(
                "held record {} is not a chunk record",
                held.record_key
            ))
            .into());
        }
    }
    Ok(OutboxRecord {
        record_key: held.record_key.clone(),
        record_json: serde_json::to_string(&record)?,
    })
}

/// Reserved input tokens: an upper estimate of half a token per byte. It is
/// a reservation, not a measurement; adapters enforce hard limits.
fn estimate_input_tokens(request: &ModelRequest) -> u64 {
    let bytes =
        request.system.len() + request.user.len() + request.response_schema.to_string().len();
    (bytes as u64).div_ceil(2)
}

fn attempt_id(
    c: &CompiledClassifier,
    batch: &[QueuedChunk],
    phase: &str,
    now: Timestamp,
) -> String {
    let mut h = FieldHasher::new("core-memory/attempt-id/v1");
    h.str(&c.binding.id).str(phase).str(&now.to_rfc3339());
    for q in batch {
        h.bytes(q.chunk_id.0.as_bytes()).u64(u64::from(q.attempts));
    }
    core_memory_contract::hash::to_hex(&h.finish()[..16])
}

fn abstain_name(reason: UnassignedReason) -> &'static str {
    match reason {
        UnassignedReason::NoFit => "no_fit",
        UnassignedReason::InsufficientEvidence => "insufficient_evidence",
        UnassignedReason::Ambiguous => "ambiguous",
    }
}

fn len32<T>(items: &[T]) -> u32 {
    u32::try_from(items.len()).unwrap_or(u32::MAX)
}

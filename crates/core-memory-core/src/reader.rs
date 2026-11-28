//! The reader: has a model read queued chunks, one batch at a time.
//!
//! Every call goes through the same four steps, and the store makes the
//! first and the last of them single transactions:
//!
//! 1. **Permit.** The scope must be active and must have granted the
//!    binding's destination, and the budget must allow the call. If so the
//!    store writes the call's receipt and reads the text, together.
//! 2. **Call** the model through the injected transport.
//! 3. **Validate** the reply against the closed shape. A rejected reply is
//!    repaired once, as a second call with a permit of its own.
//! 4. **Finish.** The store records how the call ended, stores what was
//!    read and settles the work, together.
//!
//! Nothing here logs or returns chunk text or model text.

use std::collections::BTreeSet;

use core_memory_contract::{CallId, ChunkId, ReadDecision, ReaderRef, Sha256Digest, Timestamp};

use crate::engine::Engine;
use crate::error::StoreError;
use crate::error::{ConfigError, EngineError};
use crate::ident::is_identifier;
use crate::model::{
    BindingConfig, ModelRequest, ModelResponse, ModelTransport, TransportError, Usage,
};
use crate::read::{
    ChunkInput, ChunkReading, Fault, MAX_WORKERS, PROMPT_REV, Quotes, ReadChoice, ReaderConfig,
    Rejected, TypeOption, binding_rev, render, repair, validate,
};
use crate::store::{
    CallFinish, CallFinished, CallResult, LeasedChunk, Permit, PermitRequest, PermittedChunk,
    Purpose, ReadBatch, ReadWrite, Sending, Store, WorkItem, WorkNext, WorkUpdate, tokens_in,
};

/// How long work waits when its scope cannot be read for now: it is not
/// active, has not granted the destination, or the day's budget is spent.
pub(crate) const DEFER_MS: u64 = 300_000;
/// How long work waits after the provider turned a request itself down, or
/// its server's certificate was not trusted. That is a problem with the
/// binding or the trust settings, which an operator has to fix.
pub const BINDING_FAULT_BACKOFF_MS: u64 = 600_000;
/// Margin a lease has over the two calls it may cover.
pub(crate) const LEASE_MARGIN_MS: u64 = 10_000;
/// Times the end of a call is offered to a store that is busy.
const FINISH_TRIES: u32 = 3;

/// Records how a call ended. A store that cannot answer for a moment,
/// because another transaction holds the workspace or the connection
/// dropped, is asked again before the failure is reported: what a model
/// said is not thrown away for that, and a call whose end is never
/// recorded is made again when its lease runs out. The store records the
/// end of a call once, so asking again is safe.
pub(crate) fn finish_call(
    store: &mut dyn Store,
    finish: &CallFinish<'_>,
) -> Result<CallFinished, StoreError> {
    asked_again(|| store.finish_call(finish))
}

/// Asks a store that is busy again, up to [`FINISH_TRIES`] times in all.
/// Only for a call the store answers the same way however often it is
/// made, or that leaves nothing behind when it fails: a permit whose
/// transaction failed wrote no receipt.
pub(crate) fn asked_again<T>(
    mut ask: impl FnMut() -> Result<T, StoreError>,
) -> Result<T, StoreError> {
    let mut tries = 1;
    loop {
        match ask() {
            Err(StoreError::Unavailable(_)) if tries < FINISH_TRIES => tries += 1,
            answered => return answered,
        }
    }
}

/// What becomes of work whose permit was refused after a call was already
/// made for it in this round. That attempt was used, so it counts: the
/// work is put off as it would have been, and set aside once its attempts
/// are spent. Otherwise a reply that is never valid and a repair that is
/// never permitted would have the same call made again without end.
pub(crate) fn counted(next: WorkNext, attempts: u32, max_attempts: u32) -> WorkNext {
    match next {
        WorkNext::Defer { .. } if attempts >= max_attempts => WorkNext::Failed,
        WorkNext::Defer { not_before } => WorkNext::Retry {
            not_before,
            alone: false,
        },
        settled => settled,
    }
}

/// What a call that produced no reply means for its record and its work.
pub(crate) struct Failure {
    pub result: CallResult,
    pub problem: &'static str,
    /// Whether each item should be tried on its own from now on.
    pub alone: bool,
    pub wait_ms: u64,
    /// Whether the call used an attempt of the work it was for. A call a
    /// plan turned away at the door, because its allowance is spent for
    /// now, did not: the allowance comes back by itself, and the work is
    /// not to be set aside for an operator while it waits.
    pub counts: bool,
}

impl Failure {
    /// The same failure, for a call made after another in the same round
    /// for the same work. That first call used the attempt, whatever
    /// became of this one.
    pub(crate) fn after_a_call(self) -> Self {
        Self {
            counts: true,
            ..self
        }
    }
}

/// Sorts a transport error. `backoff_ms` is the reader's own wait between
/// attempts.
pub(crate) fn failure(error: &TransportError, backoff_ms: u64) -> Failure {
    let (result, problem, alone, wait_ms) = match error {
        TransportError::Transient { retry_after_ms, .. } => (
            CallResult::NotSent,
            "provider_unavailable",
            false,
            retry_after_ms.unwrap_or(0).max(backoff_ms),
        ),
        TransportError::InDoubt { .. } => (CallResult::InDoubt, "call_in_doubt", false, backoff_ms),
        // The model declined this content, or it did not fit. Alone, each
        // item either reads or fails by itself.
        TransportError::Refused { .. } => (CallResult::Refused, "model_refused", true, backoff_ms),
        TransportError::DidNotFit { .. } => (CallResult::Refused, "did_not_fit", true, backoff_ms),
        TransportError::Permanent { .. } => (
            CallResult::Failed,
            "binding_rejected",
            false,
            BINDING_FAULT_BACKOFF_MS.max(backoff_ms),
        ),
        // Nothing was sent, and nothing will be until an operator changes
        // what the engine trusts: as long a wait as for a binding turned
        // down.
        TransportError::Untrusted { .. } => (
            CallResult::NotSent,
            "endpoint_untrusted",
            false,
            BINDING_FAULT_BACKOFF_MS.max(backoff_ms),
        ),
        // Nothing was processed, and nothing will be until the binding's
        // owner signs in again.
        TransportError::SignInRequired { .. } => (
            CallResult::NotSent,
            "signin_required",
            false,
            BINDING_FAULT_BACKOFF_MS.max(backoff_ms),
        ),
        // The plan's allowance is spent for now. A reply cut off by it may
        // have been processed; one turned away at the door was not.
        TransportError::LimitReached { started, .. } => (
            if *started {
                CallResult::InDoubt
            } else {
                CallResult::NotSent
            },
            "plan_limit_reached",
            false,
            BINDING_FAULT_BACKOFF_MS.max(backoff_ms),
        ),
    };
    Failure {
        result,
        problem,
        alone,
        wait_ms,
        counts: !matches!(error, TransportError::LimitReached { started: false, .. }),
    }
}

/// What becomes of work whose permit was not granted, and why. `None` if
/// there is nothing to settle: the text is gone, and its work went with it.
pub(crate) fn refusal(refused: &Permit, now: Timestamp) -> Option<(WorkNext, &'static str)> {
    let later = |not_before| WorkNext::Defer { not_before };
    match refused {
        Permit::ScopeNotActive(_) => Some((later(now.plus_millis(DEFER_MS)), "scope_not_active")),
        Permit::NoConsent => Some((later(now.plus_millis(DEFER_MS)), "no_consent")),
        // A day's budget comes back at the next UTC midnight.
        Permit::OverBudget => Some((later(now.next_utc_midnight()), "over_budget")),
        // Trying again would find the same text. An operator has to look.
        Permit::Unsendable => Some((WorkNext::Failed, "text_unreadable")),
        Permit::NothingToSend | Permit::Granted { .. } => None,
    }
}

/// A reader ready to run: its settings and its binding, validated.
#[derive(Debug, Clone)]
pub struct CompiledReader {
    pub settings: ReaderConfig,
    pub binding: BindingConfig,
    /// Where calls send text. What a scope consents to.
    pub destination: String,
    pub reader_ref: ReaderRef,
}

impl CompiledReader {
    pub(crate) fn compile(
        settings: ReaderConfig,
        bindings: &[BindingConfig],
    ) -> Result<Self, ConfigError> {
        let Some(binding) = bindings.iter().find(|b| b.id == settings.binding) else {
            // What is not an identifier is not a binding's id, and may be
            // what was meant for another setting. It is not repeated.
            return Err(ConfigError::new(if is_identifier(&settings.binding) {
                format!(
                    "reader.binding `{}` is not a configured [[binding]]",
                    settings.binding
                )
            } else {
                "reader.binding is not the id of a configured [[binding]]".to_string()
            }));
        };
        binding.replies()?;
        if !(1..=64).contains(&settings.batch_size) {
            return Err(ConfigError::new("reader.batch_size must be 1 to 64"));
        }
        if settings.max_attempts == 0 {
            return Err(ConfigError::new("reader.max_attempts must be 1 or more"));
        }
        if !(1..=MAX_WORKERS).contains(&settings.workers) {
            return Err(ConfigError::new(format!(
                "reader.workers must be 1 to {MAX_WORKERS}"
            )));
        }
        let needed = binding
            .timeout_ms
            .saturating_mul(2)
            .saturating_add(LEASE_MARGIN_MS);
        if settings.lease_ms < needed {
            return Err(ConfigError::new(format!(
                "reader.lease_ms must be at least {needed}: two calls of the binding's timeout, \
                 and a margin"
            )));
        }
        let rev: Sha256Digest = binding_rev(binding)
            .map_err(|e| ConfigError::new(format!("cannot hash binding: {e}")))?;
        Ok(Self {
            destination: binding.destination()?,
            reader_ref: ReaderRef {
                binding_id: binding.id.clone(),
                binding_rev: rev,
                model: binding.model.clone(),
                prompt_rev: PROMPT_REV,
            },
            binding: binding.clone(),
            settings,
        })
    }
}

/// What one round of reading did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReadRound {
    /// No reader is configured, or nothing is due.
    Idle,
    Handled(ReadReport),
}

/// One batch, as counts and codes. It holds no text.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ReadReport {
    pub scope: String,
    /// Chunks in the batch.
    pub chunks: u32,
    /// Reads stored.
    pub stored: u32,
    /// Reads not stored, because their chunk was erased or their scope
    /// stopped being active while the model was reading.
    pub dropped: u32,
    /// Tool calls in the round. A round reads chunks or one tool call.
    pub tool_calls: u32,
    /// Model calls made.
    pub calls: u32,
    pub input_tokens: u64,
    pub output_tokens: u64,
    /// Of `input_tokens`, those read from and written to the provider's
    /// prompt cache, which it prices differently.
    pub cache_read_tokens: u64,
    pub cache_write_tokens: u64,
    /// Why the batch was not read, if it was not.
    pub problem: Option<&'static str>,
    /// What was wrong with the replies this round rejected, a first reply
    /// that its repair put right included. Codes only.
    pub reply_faults: Vec<Fault>,
    /// Quotes the stored reads left out, because they were not found in
    /// their chunk on the last attempt, or were past a limit.
    pub quotes_dropped: u32,
}

impl ReadReport {
    /// Notes what was wrong with a rejected reply.
    pub(crate) fn reject(&mut self, rejected: &Rejected) {
        for fault in &rejected.faults {
            if !self.reply_faults.contains(fault) {
                self.reply_faults.push(*fault);
            }
        }
        self.reply_faults.sort();
    }

    /// Adds what one call used. A provider's own count of tokens is a
    /// number from outside, so the sum stops at the largest it can hold.
    pub(crate) fn count(&mut self, usage: &Usage) {
        self.input_tokens = self.input_tokens.saturating_add(usage.input_tokens);
        self.output_tokens = self.output_tokens.saturating_add(usage.output_tokens);
        self.cache_read_tokens = self
            .cache_read_tokens
            .saturating_add(usage.cache_read_tokens);
        self.cache_write_tokens = self
            .cache_write_tokens
            .saturating_add(usage.cache_write_tokens);
    }
}

/// The bytes of text in `chunks`.
fn text_bytes(chunks: &[PermittedChunk]) -> u64 {
    chunks.iter().map(|chunk| chunk.text.len() as u64).sum()
}

/// The call in progress and what it was permitted to send.
struct Sent<'a> {
    scope: &'a str,
    call_id: CallId,
    chunks: &'a [PermittedChunk],
}

impl Engine {
    /// The reader, if one is configured.
    pub fn reader(&self) -> Option<&CompiledReader> {
        self.reader.as_ref()
    }

    /// Reads one batch of queued chunks, if any is due. Returns `Err` only
    /// when the store fails; everything a model or a provider does wrong is
    /// recorded and reported in the round.
    pub fn read_once(
        &self,
        store: &mut dyn Store,
        transport: &dyn ModelTransport,
    ) -> Result<ReadRound, EngineError> {
        let (Some(reader), Some(vocab)) = (&self.reader, &self.vocab) else {
            return Ok(ReadRound::Idle);
        };
        let now = self.clock.now().truncated_to_micros();
        let lease_until = now.plus_millis(reader.settings.lease_ms);
        let Some(batch) = store.claim_reads(
            reader.settings.batch_size,
            reader.settings.max_attempts,
            now,
            lease_until,
        )?
        else {
            return Ok(ReadRound::Idle);
        };
        let mut report = ReadReport {
            scope: batch.scope.clone(),
            chunks: batch.chunks.len() as u32,
            ..ReadReport::default()
        };

        // The types this source's chunks may be given.
        let narrowed = self
            .registry
            .get(&batch.profile.id)
            .filter(|p| p.def.rev == batch.profile.rev)
            .and_then(|p| p.def.types.as_ref());
        let keys: Vec<&str> = match narrowed {
            Some(listed) => listed.iter().map(String::as_str).collect(),
            None => vocab.keys().collect(),
        };
        let types: Vec<TypeOption<'_>> = keys
            .iter()
            .map(|key| TypeOption {
                key,
                definition: vocab.definition(key),
            })
            .collect();
        let allowed: BTreeSet<&str> = keys.iter().copied().collect();
        let fixed_tokens = |request_without_text: usize| {
            u64::from(reader.binding.max_output_tokens) + request_without_text as u64 / 3
        };
        let ids: Vec<ChunkId> = batch.chunks.iter().map(|c| c.chunk_id).collect();
        let instructions = render(&types, &[], reader.binding.max_output_tokens);
        let instructions = instructions.system.len() + instructions.user.len();

        // ---- first call ----
        let request = PermitRequest {
            scope: &batch.scope,
            chunk_ids: &ids,
            purpose: Purpose::ChunkRead,
            destination: &reader.destination,
            reader: &reader.reader_ref,
            attempt: 1,
            repairs: None,
            // A chunk is sent as it is stored.
            sending: &|chunks| {
                let bytes = text_bytes(chunks);
                Some(Sending {
                    bytes,
                    reserved_tokens: fixed_tokens(instructions) + tokens_in(bytes),
                })
            },
            budget: reader.binding.budget,
            at: now,
        };
        // A permit the store could not give for a moment, as when another
        // transaction held the workspace past the lock timeout, wrote
        // nothing: its transaction rolled back. Asking again writes one
        // receipt, as asking once would have.
        let permit = asked_again(|| store.permit(&request))?;
        let (call_id, chunks) = match permit {
            Permit::Granted { call_id, chunks } => (call_id, chunks),
            refused => {
                report.problem = Some(self.defer(store, &batch, &refused, false)?);
                return Ok(ReadRound::Handled(report));
            }
        };
        let first = Sent {
            scope: &batch.scope,
            call_id,
            chunks: &chunks,
        };
        let request = render(&types, &inputs(&chunks), reader.binding.max_output_tokens);
        let texts: Vec<&str> = chunks.iter().map(|c| c.text.as_str()).collect();
        let response = match self.call(transport, reader, &request, &mut report) {
            Ok(response) => response,
            Err(e) => {
                let failed = failure(&e, self.backoff_ms());
                self.finish_failed(store, reader, &batch, &first, failed, &mut report)?;
                return Ok(ReadRound::Handled(report));
            }
        };
        let rejected = match validate(&response.text, &texts, &allowed, Quotes::MustBeFound) {
            Ok(readings) => {
                let stored = self.finish_read(store, reader, &first, &readings, response.usage)?;
                (report.stored, report.dropped) = stored;
                report.quotes_dropped = dropped_quotes(&readings);
                return Ok(ReadRound::Handled(report));
            }
            Err(rejected) => rejected,
        };
        report.reject(&rejected);
        // The reply is recorded as rejected. The work stays leased for the
        // repair, which is a call of its own.
        self.finish(
            store,
            reader,
            Finished {
                scope: &batch.scope,
                call_id: first.call_id,
                result: CallResult::Rejected,
                usage: Some(response.usage),
            },
            &[],
            &[],
        )?;

        // ---- the one repair ----
        let repair_request = repair(&request, &response.text, &rejected.problems);
        // The repair carries the chunks, the rejected reply and what was
        // wrong with it.
        let repair_len = repair_request.system.len() + repair_request.user.len();
        let sent_ids: Vec<ChunkId> = chunks.iter().map(|c| c.chunk_id).collect();
        let request = PermitRequest {
            scope: &batch.scope,
            chunk_ids: &sent_ids,
            purpose: Purpose::ChunkRead,
            destination: &reader.destination,
            reader: &reader.reader_ref,
            attempt: 2,
            repairs: Some(first.call_id),
            // The repair shows the same chunks again.
            sending: &|chunks| {
                Some(Sending {
                    bytes: text_bytes(chunks),
                    reserved_tokens: fixed_tokens(repair_len),
                })
            },
            budget: reader.binding.budget,
            at: self.clock.now().truncated_to_micros(),
        };
        // A permit the store could not give for a moment, as when another
        // transaction held the workspace past the lock timeout, wrote
        // nothing: its transaction rolled back. Asking again writes one
        // receipt, as asking once would have.
        let permit = asked_again(|| store.permit(&request))?;
        let (repair_call, again) = match permit {
            Permit::Granted { call_id, chunks } => (call_id, chunks),
            refused => {
                // The first call was made, so this attempt counts.
                report.problem = Some(self.defer(store, &batch, &refused, true)?);
                return Ok(ReadRound::Handled(report));
            }
        };
        let second = Sent {
            scope: &batch.scope,
            call_id: repair_call,
            chunks: &again,
        };
        if again != chunks {
            // A chunk was erased since the first call, so the repair would
            // talk about a batch that no longer exists. Nothing is sent.
            let work = self.retries(&batch, &again, false, "batch_changed");
            self.finish(
                store,
                reader,
                Finished {
                    scope: &batch.scope,
                    call_id: repair_call,
                    result: CallResult::NotSent,
                    usage: None,
                },
                &[],
                &work,
            )?;
            report.problem = Some("batch_changed");
            return Ok(ReadRound::Handled(report));
        }
        let response = match self.call(transport, reader, &repair_request, &mut report) {
            Ok(response) => response,
            Err(e) => {
                let failed = failure(&e, self.backoff_ms()).after_a_call();
                self.finish_failed(store, reader, &batch, &second, failed, &mut report)?;
                return Ok(ReadRound::Handled(report));
            }
        };
        match validate(&response.text, &texts, &allowed, Quotes::DropUnfound) {
            Ok(readings) => {
                let stored = self.finish_read(store, reader, &second, &readings, response.usage)?;
                (report.stored, report.dropped) = stored;
                report.quotes_dropped = dropped_quotes(&readings);
            }
            Err(rejected) => {
                report.reject(&rejected);
                // Twice invalid. Each chunk is tried on its own from now
                // on, so one the model cannot read holds no other back.
                let work = self.retries(&batch, &again, true, "reply_invalid");
                self.finish(
                    store,
                    reader,
                    Finished {
                        scope: &batch.scope,
                        call_id: repair_call,
                        result: CallResult::Rejected,
                        usage: Some(response.usage),
                    },
                    &[],
                    &work,
                )?;
                report.problem = Some("reply_invalid");
            }
        }
        Ok(ReadRound::Handled(report))
    }

    /// Makes one call and counts it.
    fn call(
        &self,
        transport: &dyn ModelTransport,
        reader: &CompiledReader,
        request: &ModelRequest,
        report: &mut ReadReport,
    ) -> Result<ModelResponse, TransportError> {
        report.calls += 1;
        let response = transport.send(&reader.binding, request)?;
        report.count(&response.usage);
        Ok(response)
    }

    /// Puts a batch back when a permit for it was refused. `called` says
    /// that a call was already made for it in this round, so that the
    /// attempt counts. Returns why the permit was refused.
    fn defer(
        &self,
        store: &mut dyn Store,
        batch: &ReadBatch,
        refused: &Permit,
        called: bool,
    ) -> Result<&'static str, EngineError> {
        let now = self.clock.now().truncated_to_micros();
        let Some((next, problem)) = refusal(refused, now) else {
            // Erasing the chunks removed their work too.
            return Ok("erased");
        };
        let max_attempts = self.reader.as_ref().map_or(1, |r| r.settings.max_attempts);
        let work: Vec<WorkUpdate> = batch
            .chunks
            .iter()
            .map(|c| WorkUpdate {
                item: WorkItem::Chunk(c.chunk_id),
                next: if called {
                    counted(next, c.attempts, max_attempts)
                } else {
                    next
                },
                problem,
            })
            .collect();
        store.update_work(&work)?;
        Ok(problem)
    }

    /// What to do with each sent chunk after a call that did not read it.
    fn retries(
        &self,
        batch: &ReadBatch,
        sent: &[PermittedChunk],
        alone: bool,
        problem: &'static str,
    ) -> Vec<WorkUpdate> {
        self.retries_after(batch, sent, alone, problem, self.backoff_ms())
    }

    fn backoff_ms(&self) -> u64 {
        self.reader
            .as_ref()
            .map_or(DEFER_MS, |r| r.settings.retry_backoff_ms)
    }

    fn retries_after(
        &self,
        batch: &ReadBatch,
        sent: &[PermittedChunk],
        alone: bool,
        problem: &'static str,
        wait_ms: u64,
    ) -> Vec<WorkUpdate> {
        let max_attempts = self.reader.as_ref().map_or(1, |r| r.settings.max_attempts);
        let not_before = self.clock.now().truncated_to_micros().plus_millis(wait_ms);
        sent.iter()
            .map(|chunk| {
                let attempts = batch
                    .chunks
                    .iter()
                    .find(|c| c.chunk_id == chunk.chunk_id)
                    .map_or(max_attempts, |c: &LeasedChunk| c.attempts);
                WorkUpdate {
                    item: WorkItem::Chunk(chunk.chunk_id),
                    next: if attempts >= max_attempts {
                        WorkNext::Failed
                    } else {
                        WorkNext::Retry { not_before, alone }
                    },
                    problem,
                }
            })
            .collect()
    }

    /// Records a call that produced no reply, and requeues its chunks.
    fn finish_failed(
        &self,
        store: &mut dyn Store,
        reader: &CompiledReader,
        batch: &ReadBatch,
        sent: &Sent<'_>,
        failed: Failure,
        report: &mut ReadReport,
    ) -> Result<(), EngineError> {
        let Failure {
            result,
            problem,
            alone,
            wait_ms,
            counts,
        } = failed;
        let work = if !counts {
            // Turned away before anything was read, for a reason that
            // passes by itself: put off, its attempt given back.
            let not_before = self.clock.now().truncated_to_micros().plus_millis(wait_ms);
            sent.chunks
                .iter()
                .map(|chunk| WorkUpdate {
                    item: WorkItem::Chunk(chunk.chunk_id),
                    next: WorkNext::Defer { not_before },
                    problem,
                })
                .collect()
        } else if result == CallResult::Refused && sent.chunks.len() == 1 {
            // The model declined this one chunk, or it does not fit. The
            // same request would get the same answer, so it is not made
            // again until an operator has looked.
            vec![WorkUpdate {
                item: WorkItem::Chunk(sent.chunks[0].chunk_id),
                next: WorkNext::Failed,
                problem,
            }]
        } else {
            self.retries_after(batch, sent.chunks, alone, problem, wait_ms)
        };
        self.finish(
            store,
            reader,
            Finished {
                scope: &batch.scope,
                call_id: sent.call_id,
                result,
                usage: None,
            },
            &[],
            &work,
        )?;
        report.problem = Some(problem);
        Ok(())
    }

    /// Stores a valid reply. Returns how many reads were stored and dropped.
    fn finish_read(
        &self,
        store: &mut dyn Store,
        reader: &CompiledReader,
        sent: &Sent<'_>,
        readings: &[ChunkReading],
        usage: Usage,
    ) -> Result<(u32, u32), EngineError> {
        let Some(vocab) = &self.vocab else {
            return Ok((0, 0));
        };
        let reads: Vec<ReadWrite> = sent
            .chunks
            .iter()
            .zip(readings)
            .filter_map(|(chunk, reading)| {
                let decision = match &reading.choice {
                    ReadChoice::Abstain(reason) => ReadDecision::Abstained { reason: *reason },
                    // The validator let through only keys of the vocabulary.
                    ReadChoice::Type(key) => ReadDecision::Typed {
                        type_ref: vocab.get(key)?.clone(),
                    },
                };
                Some(ReadWrite {
                    chunk_id: chunk.chunk_id,
                    decision,
                    spans: reading.spans.clone(),
                    spans_rejected: reading.spans_rejected,
                })
            })
            .collect();
        self.finish(
            store,
            reader,
            Finished {
                scope: sent.scope,
                call_id: sent.call_id,
                result: CallResult::Accepted,
                usage: Some(usage),
            },
            &reads,
            &[],
        )
    }

    fn finish(
        &self,
        store: &mut dyn Store,
        reader: &CompiledReader,
        finished: Finished<'_>,
        reads: &[ReadWrite],
        work: &[WorkUpdate],
    ) -> Result<(u32, u32), EngineError> {
        let Some(vocab) = &self.vocab else {
            return Ok((0, 0));
        };
        let at: Timestamp = self.clock.now().truncated_to_micros();
        let done = finish_call(
            store,
            &CallFinish {
                call_id: finished.call_id,
                scope: finished.scope,
                result: finished.result,
                usage: finished.usage,
                reads,
                tool_outcome: None,
                vocabulary: Some(vocab.catalog()),
                reader: &reader.reader_ref,
                work,
                at,
                judge_after: None,
            },
        )?;
        Ok((done.stored, done.dropped))
    }
}

/// How a call ended, for the store.
struct Finished<'a> {
    scope: &'a str,
    call_id: CallId,
    result: CallResult,
    usage: Option<Usage>,
}

/// Quotes the readings leave out.
fn dropped_quotes(readings: &[ChunkReading]) -> u32 {
    readings.iter().fold(0u32, |sum, reading| {
        sum.saturating_add(reading.spans_rejected)
    })
}

fn inputs(chunks: &[PermittedChunk]) -> Vec<ChunkInput<'_>> {
    chunks
        .iter()
        .map(|c| ChunkInput {
            text: &c.text,
            role: c.role.as_deref(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use crate::model::TransportError;
    use crate::store::CallResult;

    #[test]
    fn a_reply_cut_off_is_told_from_one_declined() {
        let declined = super::failure(
            &TransportError::Refused {
                message: "declined".into(),
            },
            1_000,
        );
        let cut_off = super::failure(
            &TransportError::DidNotFit {
                message: "cut off".into(),
            },
            1_000,
        );
        assert_eq!(
            (declined.problem, cut_off.problem),
            ("model_refused", "did_not_fit")
        );
        // Either way the chunks are tried again alone, where a smaller
        // request may succeed.
        assert!(declined.alone && cut_off.alone);
        assert_eq!(cut_off.result, CallResult::Refused);
    }

    #[test]
    fn a_server_not_trusted_is_told_from_a_call_in_doubt() {
        let untrusted = super::failure(
            &TransportError::Untrusted {
                message: "certificate not trusted".into(),
            },
            1_000,
        );
        // Nothing was sent: the receipt says so, and the work is not in
        // doubt. It waits as long as for a binding turned down, since every
        // call fails alike until an operator fixes what is trusted.
        assert_eq!(
            (untrusted.result, untrusted.problem, untrusted.alone),
            (CallResult::NotSent, "endpoint_untrusted", false)
        );
        assert_eq!(untrusted.wait_ms, super::BINDING_FAULT_BACKOFF_MS);
        let in_doubt = super::failure(
            &TransportError::InDoubt {
                message: "timed out".into(),
            },
            1_000,
        );
        assert_eq!(
            (in_doubt.result, in_doubt.problem),
            (CallResult::InDoubt, "call_in_doubt")
        );
    }

    #[test]
    fn a_sign_in_or_a_plan_limit_pauses_the_binding() {
        let signin = super::failure(
            &TransportError::SignInRequired {
                message: "signed out".into(),
            },
            1_000,
        );
        // Nothing reached the model, and nothing will until the owner signs
        // in again: as long a wait as for a binding turned down.
        assert_eq!(
            (signin.result, signin.problem, signin.alone),
            (CallResult::NotSent, "signin_required", false)
        );
        assert_eq!(signin.wait_ms, super::BINDING_FAULT_BACKOFF_MS);
        let at_door = super::failure(
            &TransportError::LimitReached {
                message: "limit".into(),
                started: false,
            },
            1_000,
        );
        let mid_reply = super::failure(
            &TransportError::LimitReached {
                message: "limit".into(),
                started: true,
            },
            1_000,
        );
        assert_eq!(
            (at_door.result, at_door.problem),
            (CallResult::NotSent, "plan_limit_reached")
        );
        // A reply cut off by the limit may have been processed.
        assert_eq!(
            (mid_reply.result, mid_reply.problem),
            (CallResult::InDoubt, "plan_limit_reached")
        );
        assert_eq!(at_door.wait_ms, super::BINDING_FAULT_BACKOFF_MS);
        assert!(!at_door.alone && !mid_reply.alone);
        // Turned away at the door, the work keeps its attempt: the
        // allowance comes back by itself. Not so once the reply began, or
        // after another call this round, or for a sign-in that someone
        // must renew.
        assert!(!at_door.counts);
        assert!(mid_reply.counts && signin.counts);
        assert!(at_door.after_a_call().counts);
    }

    use crate::{Engine, EngineConfig, ManualClock};

    fn engine(reader: &str) -> Result<Engine, String> {
        let config: EngineConfig = toml::from_str(&format!(
            r#"
[vocabulary]
id = "acme.types"
types = [{{ key = "decision", rev = 1 }}]

[reader]
{reader}

[[binding]]
id = "small"
provider = "stand-in"
endpoint = "https://models.example.com/v1"
model = "stand-in-1"
max_output_tokens = 512
"#
        ))
        .map_err(|e| e.to_string())?;
        Engine::new(config, Arc::new(ManualClock::at_unix_millis(0))).map_err(|e| e.to_string())
    }

    #[test]
    fn a_reader_names_a_configured_binding() {
        let ok = engine(r#"binding = "small""#).unwrap();
        assert_eq!(ok.reader().unwrap().destination, "models.example.com");
        let unknown = engine(r#"binding = "large""#).unwrap_err();
        assert!(
            unknown.contains("reader.binding `large` is not a configured [[binding]]"),
            "{unknown}"
        );
        // What is put where the binding's id belongs is not repeated
        // unless it could be one.
        let stray = engine(r#"binding = "host=db user=engine password = hunter2""#).unwrap_err();
        assert!(stray.contains("is not the id of a configured"), "{stray}");
        assert!(!stray.contains("hunter2"), "{stray}");
    }
}

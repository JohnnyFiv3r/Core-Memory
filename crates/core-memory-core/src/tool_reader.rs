//! The tool reader: has a model read queued tool calls, one to a call.
//!
//! The steps are those of the chunk reader ([`crate::reader`]): a permit
//! that writes the receipt and reads the text, the call, validation with
//! one repair, and a finish that records the result and stores what was
//! read. The difference is what is sent. A tool call's text is shortened by
//! code first, and the model is shown all of what is left.
//!
//! Nothing here logs or returns a tool's text or a model's.

use core_memory_contract::{CallId, ChunkId, CompressorRef, ReaderRef, Sha256Digest, Timestamp};

use crate::compress::{COMPRESSOR_ID, COMPRESSOR_REV};
use crate::engine::Engine;
use crate::error::{ConfigError, EngineError};
use crate::ident::is_identifier;
use crate::model::{BindingConfig, ModelRequest, ModelTransport, Usage};
use crate::read::repair;
use crate::reader::{
    Failure, LEASE_MARGIN_MS, ReadReport, ReadRound, asked_again, counted, failure, finish_call,
    refusal,
};
use crate::store::{
    CallFinish, CallResult, LeasedToolCall, Permit, PermitRequest, PermittedChunk, Purpose,
    Sending, Store, ToolOutcomeWrite, WorkItem, WorkNext, WorkUpdate, tokens_in,
};
use crate::tool::{
    TOOL_PROMPT_REV, ToolReaderConfig, ToolReading, instructions_bytes, render, show,
    tool_binding_rev, validate,
};

/// A tool reader ready to run: its settings and its binding, validated.
#[derive(Debug, Clone)]
pub struct CompiledToolReader {
    pub settings: ToolReaderConfig,
    pub binding: BindingConfig,
    /// Where calls send text. What a scope consents to.
    pub destination: String,
    pub reader_ref: ReaderRef,
}

impl CompiledToolReader {
    pub(crate) fn compile(
        settings: ToolReaderConfig,
        bindings: &[BindingConfig],
    ) -> Result<Self, ConfigError> {
        let Some(binding) = bindings.iter().find(|b| b.id == settings.binding) else {
            // What is not an identifier is not a binding's id, and may be
            // what was meant for another setting. It is not repeated.
            return Err(ConfigError::new(if is_identifier(&settings.binding) {
                format!(
                    "tool_reader.binding `{}` is not a configured [[binding]]",
                    settings.binding
                )
            } else {
                "tool_reader.binding is not the id of a configured [[binding]]".to_string()
            }));
        };
        if settings.max_attempts == 0 {
            return Err(ConfigError::new(
                "tool_reader.max_attempts must be 1 or more",
            ));
        }
        let needed = binding
            .timeout_ms
            .saturating_mul(2)
            .saturating_add(LEASE_MARGIN_MS);
        if settings.lease_ms < needed {
            return Err(ConfigError::new(format!(
                "tool_reader.lease_ms must be at least {needed}: two calls of the binding's \
                 timeout, and a margin"
            )));
        }
        let rev: Sha256Digest = tool_binding_rev(binding)
            .map_err(|e| ConfigError::new(format!("cannot hash binding: {e}")))?;
        Ok(Self {
            destination: binding.destination()?,
            reader_ref: ReaderRef {
                binding_id: binding.id.clone(),
                binding_rev: rev,
                model: binding.model.clone(),
                prompt_rev: TOOL_PROMPT_REV,
            },
            binding: binding.clone(),
            settings,
        })
    }
}

/// How one model call for a tool call ended, for the store.
struct Ended<'a> {
    call_id: CallId,
    result: CallResult,
    usage: Option<Usage>,
    outcome: Option<&'a ToolOutcomeWrite>,
    /// What becomes of the work, if it is not done and not still in hand.
    work: Option<(WorkNext, &'static str)>,
}

impl Engine {
    /// The tool reader, if one is configured.
    pub fn tool_reader(&self) -> Option<&CompiledToolReader> {
        self.tool_reader.as_ref()
    }

    /// Reads one queued tool call, if one is due. Returns `Err` only when
    /// the store fails; everything a model or a provider does wrong is
    /// recorded and reported in the round.
    pub fn read_tool_once(
        &self,
        store: &mut dyn Store,
        transport: &dyn ModelTransport,
    ) -> Result<ReadRound, EngineError> {
        let Some(reader) = &self.tool_reader else {
            return Ok(ReadRound::Idle);
        };
        let now = self.clock.now().truncated_to_micros();
        let lease_until = now.plus_millis(reader.settings.lease_ms);
        let Some(call) = store.claim_tool_read(reader.settings.max_attempts, now, lease_until)?
        else {
            return Ok(ReadRound::Idle);
        };
        let mut report = ReadReport {
            scope: call.scope.clone(),
            tool_calls: 1,
            ..ReadReport::default()
        };
        let reply_tokens = u64::from(reader.binding.max_output_tokens);
        let request_tokens = |request: &ModelRequest| {
            reply_tokens + tokens_in((request.system.len() + request.user.len()) as u64)
        };

        // ---- first call ----
        // What the call sends is the text once code has shortened it,
        // which is only known once the store has read it. Text that does
        // not hold what its event says it holds is not sent at all: the
        // store then permits nothing, and the call is set aside.
        let sending = |chunks: &[PermittedChunk]| {
            let shown = show(&call.tool_call, &whole(chunks))?;
            Some(Sending {
                bytes: shown.compressed_bytes,
                reserved_tokens: reply_tokens
                    + tokens_in(instructions_bytes() + shown.user.len() as u64),
            })
        };
        let Some((call_id, chunks)) =
            self.permit_tool(store, reader, &call, 1, None, &sending, &mut report)?
        else {
            return Ok(ReadRound::Handled(report));
        };
        let Some(shown) = show(&call.tool_call, &whole(&chunks)) else {
            // The permit was granted for text that could be shown, so this
            // is the same text and can be shown. If it ever cannot, nothing
            // is sent, and the receipt says so.
            return self.end_tool(
                store,
                reader,
                &call,
                Ended {
                    call_id,
                    result: CallResult::NotSent,
                    usage: None,
                    outcome: None,
                    work: Some((WorkNext::Failed, "text_unreadable")),
                },
                report,
            );
        };
        let request = render(&shown, reader.binding.max_output_tokens);
        report.calls += 1;
        let response = match transport.send(&reader.binding, &request) {
            Ok(response) => response,
            Err(e) => {
                let failed = failure(&e, reader.settings.retry_backoff_ms);
                return self.fail_tool(store, reader, &call, call_id, failed, report);
            }
        };
        report.count(&response.usage);
        let errors = match validate(&response.text) {
            Ok(reading) => {
                let outcome = outcome(&call, reading, shown.compressed_bytes);
                return self.end_tool(
                    store,
                    reader,
                    &call,
                    Ended {
                        call_id,
                        result: CallResult::Accepted,
                        usage: Some(response.usage),
                        outcome: Some(&outcome),
                        work: None,
                    },
                    report,
                );
            }
            Err(errors) => errors,
        };
        // The reply is recorded as rejected. The work stays in hand for
        // the repair, which is a call of its own.
        self.finish_tool(
            store,
            reader,
            &call,
            &Ended {
                call_id,
                result: CallResult::Rejected,
                usage: Some(response.usage),
                outcome: None,
                work: None,
            },
        )?;

        // ---- the one repair ----
        let repair_request = repair(&request, &response.text, &errors);
        // The repair shows the same shortened text again.
        let repair_sends = Sending {
            bytes: shown.compressed_bytes,
            reserved_tokens: request_tokens(&repair_request),
        };
        let Some((repair_call, again)) = self.permit_tool(
            store,
            reader,
            &call,
            2,
            Some(call_id),
            &|_| Some(repair_sends),
            &mut report,
        )?
        else {
            return Ok(ReadRound::Handled(report));
        };
        // An event is erased whole, so the text is either what it was or
        // gone, and a permit for text that is gone is not granted.
        debug_assert_eq!(again.len(), chunks.len());
        report.calls += 1;
        let response = match transport.send(&reader.binding, &repair_request) {
            Ok(response) => response,
            Err(e) => {
                let failed = failure(&e, reader.settings.retry_backoff_ms);
                return self.fail_tool(store, reader, &call, repair_call, failed, report);
            }
        };
        report.count(&response.usage);
        match validate(&response.text) {
            Ok(reading) => {
                let outcome = outcome(&call, reading, shown.compressed_bytes);
                self.end_tool(
                    store,
                    reader,
                    &call,
                    Ended {
                        call_id: repair_call,
                        result: CallResult::Accepted,
                        usage: Some(response.usage),
                        outcome: Some(&outcome),
                        work: None,
                    },
                    report,
                )
            }
            Err(_) => {
                let next = self.tool_retry(reader, &call, reader.settings.retry_backoff_ms);
                self.end_tool(
                    store,
                    reader,
                    &call,
                    Ended {
                        call_id: repair_call,
                        result: CallResult::Rejected,
                        usage: Some(response.usage),
                        outcome: None,
                        work: Some((next, "reply_invalid")),
                    },
                    report,
                )
            }
        }
    }

    /// Asks for a permit. `None` means it was not granted: the work was
    /// put off and the report says why.
    #[allow(clippy::too_many_arguments)]
    fn permit_tool(
        &self,
        store: &mut dyn Store,
        reader: &CompiledToolReader,
        call: &LeasedToolCall,
        attempt: u8,
        repairs: Option<CallId>,
        sending: &(dyn Fn(&[PermittedChunk]) -> Option<Sending> + Sync),
        report: &mut ReadReport,
    ) -> Result<Option<(CallId, Vec<PermittedChunk>)>, EngineError> {
        let now = self.clock.now().truncated_to_micros();
        let chunk_ids: &[ChunkId] = &call.chunk_ids;
        let request = PermitRequest {
            scope: &call.scope,
            chunk_ids,
            purpose: Purpose::ToolRead,
            destination: &reader.destination,
            reader: &reader.reader_ref,
            attempt,
            repairs,
            sending,
            budget: reader.binding.budget,
            at: now,
        };
        // A permit the store could not give for a moment, as when another
        // transaction held the workspace past the lock timeout, wrote
        // nothing: its transaction rolled back. Asking again writes one
        // receipt, as asking once would have.
        let permit = asked_again(|| store.permit(&request))?;
        match permit {
            Permit::Granted { call_id, chunks } => Ok(Some((call_id, chunks))),
            refused => {
                report.problem = Some(match refusal(&refused, now) {
                    // Erasing the event removed its work too.
                    None => "erased",
                    Some((next, problem)) => {
                        store.update_work(&[WorkUpdate {
                            item: WorkItem::ToolCall(call.event_id),
                            // A refused repair comes after a call that was
                            // made, so that attempt counts.
                            next: if repairs.is_some() {
                                counted(next, call.attempts, reader.settings.max_attempts)
                            } else {
                                next
                            },
                            problem,
                        }])?;
                        problem
                    }
                });
                Ok(None)
            }
        }
    }

    /// What becomes of a tool call after a model call that did not read
    /// it: another attempt after `wait_ms`, or set aside.
    fn tool_retry(
        &self,
        reader: &CompiledToolReader,
        call: &LeasedToolCall,
        wait_ms: u64,
    ) -> WorkNext {
        if call.attempts >= reader.settings.max_attempts {
            WorkNext::Failed
        } else {
            WorkNext::Retry {
                not_before: self.clock.now().truncated_to_micros().plus_millis(wait_ms),
                // A tool call is always read alone.
                alone: false,
            }
        }
    }

    /// Records a call that produced no reply, and requeues the tool call.
    fn fail_tool(
        &self,
        store: &mut dyn Store,
        reader: &CompiledToolReader,
        call: &LeasedToolCall,
        call_id: CallId,
        failed: Failure,
        report: ReadReport,
    ) -> Result<ReadRound, EngineError> {
        let next = if failed.result == CallResult::Refused {
            // The model declined this call, or it does not fit. A tool
            // call is always sent alone, so the same request would get the
            // same answer. It is not made again until an operator has
            // looked.
            WorkNext::Failed
        } else {
            self.tool_retry(reader, call, failed.wait_ms)
        };
        self.end_tool(
            store,
            reader,
            call,
            Ended {
                call_id,
                result: failed.result,
                usage: None,
                outcome: None,
                work: Some((next, failed.problem)),
            },
            report,
        )
    }

    /// Records the last model call of a round and completes its report.
    fn end_tool(
        &self,
        store: &mut dyn Store,
        reader: &CompiledToolReader,
        call: &LeasedToolCall,
        ended: Ended<'_>,
        mut report: ReadReport,
    ) -> Result<ReadRound, EngineError> {
        let (stored, dropped) = self.finish_tool(store, reader, call, &ended)?;
        report.stored = stored;
        report.dropped = dropped;
        report.problem = ended.work.map(|(_, problem)| problem);
        Ok(ReadRound::Handled(report))
    }

    fn finish_tool(
        &self,
        store: &mut dyn Store,
        reader: &CompiledToolReader,
        call: &LeasedToolCall,
        ended: &Ended<'_>,
    ) -> Result<(u32, u32), EngineError> {
        let at: Timestamp = self.clock.now().truncated_to_micros();
        let work: Vec<WorkUpdate> = ended
            .work
            .iter()
            .map(|(next, problem)| WorkUpdate {
                item: WorkItem::ToolCall(call.event_id),
                next: *next,
                problem,
            })
            .collect();
        let done = finish_call(
            store,
            &CallFinish {
                call_id: ended.call_id,
                scope: &call.scope,
                result: ended.result,
                usage: ended.usage,
                reads: &[],
                tool_outcome: ended.outcome,
                vocabulary: None,
                reader: &reader.reader_ref,
                work: &work,
                at,
                // What the model wrote is what a judge is shown of the
                // call, so the call's bead is owed to the judge from now.
                judge_after: self
                    .judge
                    .as_ref()
                    .filter(|_| ended.outcome.is_some())
                    .map(|judge| at.plus_millis(judge.settings.debounce_ms)),
            },
        )?;
        Ok((done.stored, done.dropped))
    }
}

/// The whole text of a tool call: its chunks' text, in order.
fn whole(chunks: &[PermittedChunk]) -> String {
    chunks.iter().map(|chunk| chunk.text.as_str()).collect()
}

fn outcome(call: &LeasedToolCall, reading: ToolReading, compressed_bytes: u64) -> ToolOutcomeWrite {
    ToolOutcomeWrite {
        event_id: call.event_id,
        status: reading.status,
        action: reading.action,
        outcome: reading.outcome,
        compressor: CompressorRef {
            id: COMPRESSOR_ID.to_string(),
            rev: COMPRESSOR_REV,
        },
        compressed_bytes,
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use crate::{Engine, EngineConfig, ManualClock};

    fn engine(tool_reader: &str, binding: &str) -> Result<Engine, String> {
        let config: EngineConfig = toml::from_str(&format!(
            r#"
[tool_reader]
{tool_reader}

[[binding]]
id = "small"
provider = "stand-in"
endpoint = "https://models.example.com/v1"
model = "stand-in-1"
max_output_tokens = 300
{binding}
"#
        ))
        .map_err(|e| e.to_string())?;
        Engine::new(config, Arc::new(ManualClock::at_unix_millis(0))).map_err(|e| e.to_string())
    }

    #[test]
    fn a_tool_reader_needs_a_binding_and_time_for_two_calls() {
        let engine_with = |tool_reader: &str, binding: &str| engine(tool_reader, binding);
        let ok = engine_with(r#"binding = "small""#, "").unwrap();
        let reader = ok.tool_reader().unwrap();
        assert_eq!(reader.destination, "models.example.com");
        assert_eq!(reader.reader_ref.binding_id, "small");
        // It gives no types, so it needs no vocabulary and no chunk reader.
        assert!(ok.vocabulary().is_none() && ok.reader().is_none());

        let refused = |tool_reader: &str, binding: &str| engine(tool_reader, binding).unwrap_err();
        assert!(
            refused(r#"binding = "large""#, "")
                .contains("tool_reader.binding `large` is not a configured [[binding]]")
        );
        assert!(refused("binding = \"small\"\nmax_attempts = 0", "").contains("max_attempts"));
        // A call may take 200 s, and a rejected reply is repaired once.
        assert!(refused(r#"binding = "small""#, "timeout_ms = 200000").contains("lease_ms"));
        assert!(
            engine_with(
                "binding = \"small\"\nlease_ms = 600000",
                "timeout_ms = 200000"
            )
            .is_ok()
        );
        // A setting it does not have is refused, not ignored.
        assert!(refused("binding = \"small\"\nbatch_size = 4", "").contains("unknown field"));
        // What is put where the binding's id belongs is not repeated
        // unless it could be one.
        let stray = refused(r#"binding = "host=db user=engine password = hunter2""#, "");
        assert!(stray.contains("is not the id of a configured"), "{stray}");
        assert!(!stray.contains("hunter2"), "{stray}");
    }
}

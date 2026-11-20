//! The judge: has a model read new beads against the beads near them, and
//! records what it says.
//!
//! The steps are those of the readers ([`crate::reader`]): a permit that
//! decides what may be shown, reads it and writes the receipt; the call;
//! validation with one repair; and a finish that records the result and
//! stores the judgment. Two things differ. A judge is shown beads of every
//! scope of a workspace that allows it, so the permit decides bead by
//! bead. And a label that changes what readers take as current is held
//! until a second read of the same beads, made without the first, gives
//! the same label.
//!
//! Nothing here logs or returns a bead's text or a model's.

use core_memory_contract::{BeadId, CallId, Label, ReaderRef, Sha256Digest, Timestamp};

use crate::engine::Engine;
use crate::error::{ConfigError, EngineError};
use crate::ident::is_identifier;
use crate::judge::{
    JudgeConfig, Judged, Names, RUBRIC_REV, Recording, content_bytes, judge_binding_rev, judgment,
    render, second_judgment, validate,
};
use crate::model::{BindingConfig, ModelRequest, ModelTransport, Usage};
use crate::read::repair;
use crate::reader::{DEFER_MS, Failure, LEASE_MARGIN_MS, asked_again, counted, failure};
use crate::store::{
    CallResult, JudgeBatch, JudgeClaim, JudgePermit, JudgePermitRequest, JudgeSettle, JudgeWork,
    JudgmentFinish, JudgmentFinished, JudgmentWrite, LeasedBead, Purpose, Sending, Shown, Store,
    WindowOf, Withheld, WorkItem, WorkNext, WorkUpdate, tokens_in,
};

/// A judge ready to run: its settings and its binding, validated.
#[derive(Debug, Clone)]
pub struct CompiledJudge {
    pub settings: JudgeConfig,
    pub binding: BindingConfig,
    /// Where calls send text. What a scope consents to.
    pub destination: String,
    pub reader_ref: ReaderRef,
}

impl CompiledJudge {
    pub(crate) fn compile(
        settings: JudgeConfig,
        bindings: &[BindingConfig],
    ) -> Result<Self, ConfigError> {
        let Some(binding) = bindings.iter().find(|b| b.id == settings.binding) else {
            // What is not an identifier is not a binding's id, and may be
            // what was meant for another setting. It is not repeated.
            return Err(ConfigError::new(if is_identifier(&settings.binding) {
                format!(
                    "judge.binding `{}` is not a configured [[binding]]",
                    settings.binding
                )
            } else {
                "judge.binding is not the id of a configured [[binding]]".to_string()
            }));
        };
        let within = |name: &str, value: u64, least: u64, most: u64| {
            if (least..=most).contains(&value) {
                Ok(())
            } else {
                Err(ConfigError::new(format!(
                    "judge.{name} must be {least} to {most}"
                )))
            }
        };
        within("max_new_beads", u64::from(settings.max_new_beads), 1, 32)?;
        within(
            "max_window_beads",
            u64::from(settings.max_window_beads),
            1,
            400,
        )?;
        within("max_window_bytes", settings.max_window_bytes, 1, 16 << 20)?;
        within(
            "max_conflict_beads",
            u64::from(settings.max_conflict_beads),
            0,
            u64::from(settings.max_window_beads),
        )?;
        // A year in milliseconds is far more than any window, and keeps
        // sums of times well inside what a timestamp holds.
        within("window_ms", settings.window_ms, 1, 366 * 86_400_000)?;
        if settings.max_attempts == 0 {
            return Err(ConfigError::new("judge.max_attempts must be 1 or more"));
        }
        let needed = binding
            .timeout_ms
            .saturating_mul(2)
            .saturating_add(LEASE_MARGIN_MS);
        if settings.lease_ms < needed {
            return Err(ConfigError::new(format!(
                "judge.lease_ms must be at least {needed}: two calls of the binding's timeout, \
                 and a margin"
            )));
        }
        let rev: Sha256Digest = judge_binding_rev(binding)
            .map_err(|e| ConfigError::new(format!("cannot hash binding: {e}")))?;
        Ok(Self {
            destination: binding.destination()?,
            reader_ref: ReaderRef {
                binding_id: binding.id.clone(),
                binding_rev: rev,
                model: binding.model.clone(),
                prompt_rev: RUBRIC_REV,
            },
            binding: binding.clone(),
            settings,
        })
    }
}

/// What one round of judging did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JudgeRound {
    /// No judge is configured, or nothing is due.
    Idle,
    Handled(JudgeReport),
}

/// One read, as counts and codes. It holds no text.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct JudgeReport {
    pub workspace: String,
    /// Whether this was the second read of a held label.
    pub second_read: bool,
    /// New beads handed to the judge.
    pub new_beads: u32,
    /// Beads shown beside them.
    pub window_beads: u32,
    /// New beads whose judgment was stored.
    pub judged: u32,
    pub verdicts: u32,
    /// Relationships written, apart from those of labels and groups.
    pub edges: u32,
    /// Labels written.
    pub labels: u32,
    /// Labels held for a second read.
    pub held: u32,
    /// Labels not written: one of their beads was no longer current, or
    /// the source already carried a label this one could not join.
    pub labels_dropped: u32,
    /// Groups made or added to.
    pub groups: u32,
    /// Name links written.
    pub names: u32,
    /// Name links the reply gave that could not be kept.
    pub names_rejected: u32,
    /// Verdicts not stored, because a bead was erased or its scope stopped
    /// being active while the judge was reading.
    pub dropped: u32,
    /// Model calls made.
    pub calls: u32,
    pub input_tokens: u64,
    pub output_tokens: u64,
    /// Why the beads were not judged, if they were not.
    pub problem: Option<&'static str>,
}

impl JudgeReport {
    fn count(&mut self, usage: &Usage) {
        self.calls += 1;
        self.input_tokens = self.input_tokens.saturating_add(usage.input_tokens);
        self.output_tokens = self.output_tokens.saturating_add(usage.output_tokens);
    }

    fn stored(&mut self, finished: JudgmentFinished, judged: &Judged) {
        self.judged = finished.judged;
        self.verdicts = finished.verdicts;
        self.edges = finished.edges;
        self.labels = finished.labels;
        self.held = finished.held;
        self.labels_dropped = finished.labels_dropped;
        self.groups = finished.groups;
        self.names = finished.names;
        self.names_rejected = judged.names_rejected;
        self.dropped = finished.dropped;
    }
}

/// One read in progress: the beads to judge and where they came from.
struct Reading<'a> {
    judge: &'a CompiledJudge,
    batch: &'a JudgeBatch,
    purpose: Purpose,
    /// The beads handed out for this read.
    leased: Vec<LeasedBead>,
    /// The lease the claim set on them. Whatever settles their work names
    /// it, so a worker whose lease ran out changes nothing.
    lease: Timestamp,
    /// For a second read: the label that is held and the beads it follows.
    held: Option<(Label, &'a [BeadId])>,
}

impl Reading<'_> {
    fn item(&self, bead: BeadId) -> WorkItem {
        match self.purpose {
            Purpose::SecondRead => WorkItem::ProposedLabel(bead),
            _ => WorkItem::NewBead(bead),
        }
    }

    fn attempts(&self, bead: BeadId) -> u32 {
        self.leased
            .iter()
            .find(|leased| leased.bead_id == bead)
            // A bead that was not handed out is not tried again.
            .map_or(u32::MAX, |leased| leased.attempts)
    }

    /// What becomes of a bead after a call that did not judge it: another
    /// attempt, or set aside once its attempts are spent.
    fn retry(&self, bead: BeadId, not_before: Timestamp, alone: bool) -> WorkNext {
        if self.attempts(bead) >= self.judge.settings.max_attempts {
            WorkNext::Failed
        } else {
            WorkNext::Retry { not_before, alone }
        }
    }
}

/// How one model call of a read ended, for the store.
struct Ended<'a> {
    call_id: CallId,
    result: CallResult,
    usage: Option<Usage>,
    judgment: Option<&'a JudgmentWrite>,
    /// The source of the held label a valid second read settles.
    settles: Option<BeadId>,
    work: Vec<WorkUpdate>,
}

/// What becomes of a new bead that may not be shown, and why.
fn withheld(reason: Withheld, now: Timestamp) -> (WorkNext, &'static str) {
    let later = WorkNext::Defer {
        not_before: now.plus_millis(DEFER_MS),
    };
    match reason {
        Withheld::ScopeNotActive => (later, "scope_not_active"),
        Withheld::NoConsent => (later, "no_consent"),
        // Erasing a bead removes its work, so this settles nothing then.
        // A live bead with nothing to show stays that way until someone
        // looks.
        Withheld::NothingToShow => (WorkNext::Failed, "nothing_to_show"),
    }
}

impl Engine {
    /// The judge, if one is configured.
    pub fn judge(&self) -> Option<&CompiledJudge> {
        self.judge.as_ref()
    }

    /// Judges the new beads of one workspace that are due, or reads one
    /// held label a second time. Returns `Err` only when the store fails;
    /// everything a model or a provider does wrong is recorded and
    /// reported in the round.
    pub fn judge_once(
        &self,
        store: &mut dyn Store,
        transport: &dyn ModelTransport,
    ) -> Result<JudgeRound, EngineError> {
        let Some(judge) = &self.judge else {
            return Ok(JudgeRound::Idle);
        };
        let now = self.clock.now().truncated_to_micros();
        let claim = JudgeClaim {
            now,
            lease_until: now.plus_millis(judge.settings.lease_ms),
            max_new: judge.settings.max_new_beads,
            max_attempts: judge.settings.max_attempts,
            // Any two beads this far from one bead are within the window
            // of each other.
            within_ms: judge.settings.window_ms / 2,
            // A chunk's bead waits for its read only if something reads.
            wait_for_reads_ms: self
                .reader
                .as_ref()
                .map(|_| judge.settings.max_wait_for_reads_ms),
        };
        let Some(batch) = store.claim_judgment(&claim)? else {
            return Ok(JudgeRound::Idle);
        };
        let reading = match &batch.work {
            JudgeWork::Window(leased) => Reading {
                judge,
                batch: &batch,
                purpose: Purpose::Judge,
                leased: leased.clone(),
                lease: claim.lease_until,
                held: None,
            },
            JudgeWork::SecondRead {
                source,
                label,
                heads,
            } => Reading {
                judge,
                batch: &batch,
                purpose: Purpose::SecondRead,
                leased: vec![*source],
                lease: claim.lease_until,
                held: Some((*label, heads)),
            },
        };
        let mut report = JudgeReport {
            workspace: batch.workspace.clone(),
            second_read: reading.held.is_some(),
            new_beads: reading.leased.len() as u32,
            ..JudgeReport::default()
        };
        self.read(store, transport, &reading, &mut report)?;
        Ok(JudgeRound::Handled(report))
    }

    fn read(
        &self,
        store: &mut dyn Store,
        transport: &dyn ModelTransport,
        reading: &Reading<'_>,
        report: &mut JudgeReport,
    ) -> Result<(), EngineError> {
        let judge = reading.judge;
        let settings = &judge.settings;
        let reply_tokens = u64::from(judge.binding.max_output_tokens);
        let costs = |request: &ModelRequest, shown: &Shown| {
            let bytes = content_bytes(shown);
            // A call that would show nothing is not made.
            (bytes > 0).then(|| Sending {
                bytes,
                reserved_tokens: reply_tokens
                    + tokens_in((request.system.len() + request.user.len()) as u64),
            })
        };
        let new: Vec<BeadId> = reading.leased.iter().map(|bead| bead.bead_id).collect();
        let window = match reading.held {
            Some((_, heads)) => WindowOf::These(heads),
            None => WindowOf::Near {
                within_ms: settings.window_ms,
                max_beads: settings.max_window_beads,
                max_bytes: settings.max_window_bytes,
                conflict_beads: settings.max_conflict_beads,
            },
        };

        // ---- first call ----
        let now = self.clock.now().truncated_to_micros();
        let permit = store.permit_judgment(&JudgePermitRequest {
            workspace: &reading.batch.workspace,
            purpose: reading.purpose,
            new: &new,
            window,
            destination: &judge.destination,
            reader: &judge.reader_ref,
            attempt: 1,
            repairs: None,
            sending: &|shown| costs(&render(shown, judge.binding.max_output_tokens), shown),
            budget: judge.binding.budget,
            at: now,
        })?;
        let (call_id, shown, not_shown) = match permit {
            JudgePermit::Granted {
                call_id,
                shown,
                withheld,
            } => (call_id, shown, withheld),
            refused => return self.put_off(store, reading, &refused, &new, false, report),
        };
        report.window_beads = shown.window.len() as u32;
        // New beads that may not be shown are settled with the first call.
        let mut aside: Vec<WorkUpdate> = not_shown
            .iter()
            .map(|(bead, reason)| {
                let (next, problem) = withheld(*reason, now);
                WorkUpdate {
                    item: reading.item(*bead),
                    next,
                    problem,
                }
            })
            .collect();
        let shown_new: Vec<BeadId> = shown.new.iter().map(|bead| bead.bead_id).collect();
        let shown_window: Vec<BeadId> = shown.window.iter().map(|bead| bead.bead_id).collect();

        let request = render(&shown, judge.binding.max_output_tokens);
        let response = match transport.send(&judge.binding, &request) {
            Ok(response) => response,
            Err(e) => {
                report.calls += 1;
                let failed = failure(&e, settings.retry_backoff_ms);
                aside.extend(self.after_failure(reading, &shown_new, &failed));
                report.problem = Some(failed.problem);
                self.finish_judgment(
                    store,
                    reading,
                    Ended {
                        call_id,
                        result: failed.result,
                        usage: None,
                        judgment: None,
                        settles: None,
                        work: aside,
                    },
                )?;
                return Ok(());
            }
        };
        report.count(&response.usage);
        let errors = match validate(&response.text, &shown, Names::MustBeFound) {
            Ok(judged) => {
                return self.store_judgment(
                    store,
                    reading,
                    (call_id, response.usage),
                    (&shown, &judged),
                    aside,
                    report,
                );
            }
            Err(errors) => errors,
        };
        // The reply is recorded as rejected. The work stays in hand for
        // the repair, which is a call of its own.
        self.finish_judgment(
            store,
            reading,
            Ended {
                call_id,
                result: CallResult::Rejected,
                usage: Some(response.usage),
                judgment: None,
                settles: None,
                work: aside,
            },
        )?;

        // ---- the one repair ----
        let repair_request = repair(&request, &response.text, &errors);
        let permit = store.permit_judgment(&JudgePermitRequest {
            workspace: &reading.batch.workspace,
            purpose: reading.purpose,
            new: &shown_new,
            // The same beads again, and no others.
            window: WindowOf::These(&shown_window),
            destination: &judge.destination,
            reader: &judge.reader_ref,
            attempt: 2,
            repairs: Some(call_id),
            // The repair talks about the beads of the first call. If one
            // of them may no longer be shown, nothing is sent.
            sending: &|again| {
                (*again == shown)
                    .then(|| costs(&repair_request, again))
                    .flatten()
            },
            budget: judge.binding.budget,
            at: self.clock.now().truncated_to_micros(),
        })?;
        let repair_call = match permit {
            JudgePermit::Granted { call_id, .. } => call_id,
            // The beads that were withheld from the first call were
            // settled with it.
            refused => return self.put_off(store, reading, &refused, &shown_new, true, report),
        };
        let response = match transport.send(&judge.binding, &repair_request) {
            Ok(response) => response,
            Err(e) => {
                report.calls += 1;
                let failed = failure(&e, settings.retry_backoff_ms);
                report.problem = Some(failed.problem);
                self.finish_judgment(
                    store,
                    reading,
                    Ended {
                        call_id: repair_call,
                        result: failed.result,
                        usage: None,
                        judgment: None,
                        settles: None,
                        work: self.after_failure(reading, &shown_new, &failed),
                    },
                )?;
                return Ok(());
            }
        };
        report.count(&response.usage);
        match validate(&response.text, &shown, Names::DropUnfound) {
            Ok(judged) => self.store_judgment(
                store,
                reading,
                (repair_call, response.usage),
                (&shown, &judged),
                Vec::new(),
                report,
            ),
            Err(_) => {
                // Twice invalid. Each bead is judged on its own from now
                // on, so one the model cannot answer for holds no other
                // back.
                let not_before = self
                    .clock
                    .now()
                    .truncated_to_micros()
                    .plus_millis(settings.retry_backoff_ms);
                let work = shown_new
                    .iter()
                    .map(|bead| WorkUpdate {
                        item: reading.item(*bead),
                        next: reading.retry(*bead, not_before, true),
                        problem: "reply_invalid",
                    })
                    .collect();
                report.problem = Some("reply_invalid");
                self.finish_judgment(
                    store,
                    reading,
                    Ended {
                        call_id: repair_call,
                        result: CallResult::Rejected,
                        usage: Some(response.usage),
                        judgment: None,
                        settles: None,
                        work,
                    },
                )?;
                Ok(())
            }
        }
    }

    /// Stores what a valid reply says.
    fn store_judgment(
        &self,
        store: &mut dyn Store,
        reading: &Reading<'_>,
        (call_id, usage): (CallId, Usage),
        (shown, judged): (&Shown, &Judged),
        work: Vec<WorkUpdate>,
        report: &mut JudgeReport,
    ) -> Result<(), EngineError> {
        let id_key = &reading.batch.id_key;
        let (write, settles) = match reading.held {
            Some(held) => (
                second_judgment(shown, judged, id_key, held),
                shown.new.first().map(|source| source.bead_id),
            ),
            None => (
                judgment(
                    shown,
                    judged,
                    Recording {
                        id_key,
                        hold_labels: reading.judge.settings.second_read,
                    },
                ),
                None,
            ),
        };
        let finished = self.finish_judgment(
            store,
            reading,
            Ended {
                call_id,
                result: CallResult::Accepted,
                usage: Some(usage),
                judgment: Some(&write),
                settles,
                work,
            },
        )?;
        report.stored(finished, judged);
        Ok(())
    }

    /// What becomes of the beads a call was made for and did not judge.
    fn after_failure(
        &self,
        reading: &Reading<'_>,
        shown_new: &[BeadId],
        failed: &Failure,
    ) -> Vec<WorkUpdate> {
        let not_before = self
            .clock
            .now()
            .truncated_to_micros()
            .plus_millis(failed.wait_ms);
        // The model declined, or the request does not fit. With one new
        // bead the same request would get the same answer, so the bead is
        // set aside at once. With several, each is tried alone.
        let declined_alone = failed.result == CallResult::Refused && shown_new.len() == 1;
        shown_new
            .iter()
            .map(|bead| WorkUpdate {
                item: reading.item(*bead),
                next: if declined_alone {
                    WorkNext::Failed
                } else {
                    reading.retry(*bead, not_before, failed.alone)
                },
                problem: failed.problem,
            })
            .collect()
    }

    /// Settles the work of `beads`, for which a permit was refused.
    /// `called` says that a call was already made for them in this round,
    /// so that the attempt counts.
    fn put_off(
        &self,
        store: &mut dyn Store,
        reading: &Reading<'_>,
        refused: &JudgePermit,
        beads: &[BeadId],
        called: bool,
        report: &mut JudgeReport,
    ) -> Result<(), EngineError> {
        let now = self.clock.now().truncated_to_micros();
        let backoff = now.plus_millis(reading.judge.settings.retry_backoff_ms);
        let all =
            |next: WorkNext, problem: &'static str| -> Vec<(BeadId, WorkNext, &'static str)> {
                beads.iter().map(|bead| (*bead, next, problem)).collect()
            };
        let settled = match refused {
            // A bead of the first call may no longer be shown, so the
            // repair, which talks about all of them, is not sent. What is
            // left is judged afresh later.
            JudgePermit::NothingToJudge(_) | JudgePermit::Unsendable if called => all(
                WorkNext::Defer {
                    not_before: backoff,
                },
                "batch_changed",
            ),
            // A second read shows the held label's beads together or not
            // at all. The source waits, whichever of them may not be shown.
            JudgePermit::NothingToJudge(not_shown) if reading.held.is_some() => {
                let (next, problem) = not_shown.first().map_or(
                    (
                        WorkNext::Defer {
                            not_before: backoff,
                        },
                        "batch_changed",
                    ),
                    |(_, reason)| withheld(*reason, now),
                );
                all(next, problem)
            }
            JudgePermit::NothingToJudge(not_shown) => not_shown
                .iter()
                .map(|(bead, reason)| {
                    let (next, problem) = withheld(*reason, now);
                    (*bead, next, problem)
                })
                .collect(),
            // No pair to read: the one bead that may be shown is settled
            // without a call, and meets later beads when theirs come.
            JudgePermit::NothingToCompare(not_shown) => beads
                .iter()
                .map(|bead| {
                    match not_shown
                        .iter()
                        .find(|(withheld_bead, _)| withheld_bead == bead)
                    {
                        Some((_, reason)) => {
                            let (next, problem) = withheld(*reason, now);
                            (*bead, next, problem)
                        }
                        None => (*bead, WorkNext::Done, "nothing_to_compare"),
                    }
                })
                .collect(),
            // A day's budget comes back at the next UTC midnight.
            JudgePermit::OverBudget => all(
                WorkNext::Defer {
                    not_before: now.next_utc_midnight(),
                },
                "over_budget",
            ),
            // Before any call this means there is nothing to show at all.
            JudgePermit::Unsendable => all(WorkNext::Failed, "nothing_to_show"),
            JudgePermit::Granted { .. } => Vec::new(),
        };
        report.problem = settled
            .iter()
            .find(|(_, next, _)| *next == WorkNext::Done)
            .or(settled.first())
            .map(|(_, _, problem)| *problem);
        let work: Vec<WorkUpdate> = settled
            .into_iter()
            .map(|(bead, next, problem)| WorkUpdate {
                item: reading.item(bead),
                next: if called {
                    counted(
                        next,
                        reading.attempts(bead),
                        reading.judge.settings.max_attempts,
                    )
                } else {
                    next
                },
                problem,
            })
            .collect();
        store.settle_judgment(&JudgeSettle {
            lease: reading.lease,
            work: &work,
        })?;
        Ok(())
    }

    fn finish_judgment(
        &self,
        store: &mut dyn Store,
        reading: &Reading<'_>,
        ended: Ended<'_>,
    ) -> Result<JudgmentFinished, EngineError> {
        let finish = JudgmentFinish {
            call_id: ended.call_id,
            workspace: &reading.batch.workspace,
            lease: reading.lease,
            result: ended.result,
            usage: ended.usage,
            judgment: ended.judgment,
            settles: ended.settles,
            reader: &reading.judge.reader_ref,
            work: &ended.work,
            at: self.clock.now().truncated_to_micros(),
        };
        // The store records the end of a call once, so a store that is
        // busy for a moment can be asked again.
        Ok(asked_again(|| store.finish_judgment(&finish))?)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use crate::{Engine, EngineConfig, ManualClock};

    fn engine(judge: &str, binding: &str) -> Result<Engine, String> {
        let config: EngineConfig = toml::from_str(&format!(
            r#"
[judge]
{judge}

[[binding]]
id = "careful"
provider = "stand-in"
endpoint = "https://judge.example.com/v1"
model = "stand-in-1"
max_output_tokens = 2000
{binding}
"#
        ))
        .map_err(|e| e.to_string())?;
        Engine::new(config, Arc::new(ManualClock::at_unix_millis(0))).map_err(|e| e.to_string())
    }

    #[test]
    fn a_judge_needs_a_binding_and_settings_that_can_work() {
        let ok = engine(r#"binding = "careful""#, "").unwrap();
        let judge = ok.judge().unwrap();
        assert_eq!(judge.destination, "judge.example.com");
        assert_eq!(judge.reader_ref.binding_id, "careful");
        // It needs no vocabulary and no reader: it is shown what there is.
        assert!(ok.vocabulary().is_none() && ok.reader().is_none());
        // A label that changes what is current is read twice by default.
        assert!(judge.settings.second_read);
        assert_eq!(
            (
                judge.settings.max_new_beads,
                judge.settings.max_window_beads
            ),
            (8, 40)
        );

        let refused = |judge: &str, binding: &str| engine(judge, binding).unwrap_err();
        assert!(
            refused(r#"binding = "hasty""#, "")
                .contains("judge.binding `hasty` is not a configured [[binding]]")
        );
        for (setting, problem) in [
            ("max_new_beads = 0", "judge.max_new_beads must be 1 to 32"),
            ("max_new_beads = 33", "judge.max_new_beads must be 1 to 32"),
            (
                "max_window_beads = 0",
                "judge.max_window_beads must be 1 to 400",
            ),
            ("max_window_bytes = 0", "judge.max_window_bytes"),
            ("window_ms = 0", "judge.window_ms"),
            (
                "max_conflict_beads = 41",
                "judge.max_conflict_beads must be 0 to 40",
            ),
            ("max_attempts = 0", "judge.max_attempts"),
        ] {
            let said = refused(&format!("binding = \"careful\"\n{setting}"), "");
            assert!(said.contains(problem), "{setting}: {said}");
        }
        // A call may take 200 s, and a rejected reply is repaired once.
        assert!(refused(r#"binding = "careful""#, "timeout_ms = 200000").contains("lease_ms"));
        // A setting it does not have is refused, not ignored.
        assert!(refused("binding = \"careful\"\nbatch_size = 4", "").contains("unknown field"));
        // What is put where the binding's id belongs is not repeated
        // unless it could be one.
        let stray = refused(r#"binding = "host=db user=engine password = hunter2""#, "");
        assert!(stray.contains("is not the id of a configured"), "{stray}");
        assert!(!stray.contains("hunter2"), "{stray}");
    }
}

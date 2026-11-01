//! Forgetting chunks a system of record erased (PRD §13, safeguard 8).
//!
//! The engine has no deletion authority while a system of record is
//! attached: memoriesQL governs erasure, then names the chunks it erased.
//! The engine drops what it still holds of them, so their text never goes
//! to a model provider or a lagging sink afterwards, and tombstones each
//! event once all its chunks are forgotten. Plans, limits and approvals
//! belong to the system of record and are not repeated here.

use std::collections::{BTreeSet, HashSet};

use core_memory_contract::forget::MAX_FORGET_CHUNKS;
use core_memory_contract::{FieldError, ForgetOutcome, ForgetRefusal, ForgetRequest};

use crate::engine::{Engine, ScopeGrants};
use crate::error::EngineError;
use crate::store::OperationalStore;

impl Engine {
    /// Forgets the chunks in `request`. Forgetting a chunk again, or one the
    /// engine never held, changes nothing and is reported. `grants` are the
    /// caller's and must cover every known chunk's scope.
    pub fn forget(
        &self,
        store: &mut dyn OperationalStore,
        grants: &ScopeGrants,
        request: &ForgetRequest,
    ) -> Result<ForgetOutcome, EngineError> {
        if request.chunk_ids.len() > MAX_FORGET_CHUNKS {
            return Ok(refused(
                ForgetRefusal::TooManyChunks,
                vec![FieldError::new(
                    "chunk_ids",
                    format!("at most {MAX_FORGET_CHUNKS} chunks per request; split it"),
                )],
            ));
        }
        let mut problems = Vec::new();
        if request.chunk_ids.is_empty() {
            problems.push(FieldError::new("chunk_ids", "name at least one chunk"));
        }
        let mut seen = HashSet::new();
        for (i, id) in request.chunk_ids.iter().enumerate() {
            if !seen.insert(id) {
                problems.push(FieldError::new(format!("chunk_ids[{i}]"), "named twice"));
            }
        }
        if !problems.is_empty() {
            return Ok(refused(ForgetRefusal::Malformed, problems));
        }

        let mut unknown = Vec::new();
        let mut to_forget = Vec::new();
        let mut already = 0u64;
        let mut denied = BTreeSet::new();
        for id in &request.chunk_ids {
            match store.chunk_home(id)? {
                None => unknown.push(*id),
                Some(home) => {
                    if !grants.allows(&home.scope) {
                        denied.insert(home.scope);
                    } else if home.forgotten {
                        already += 1;
                    } else {
                        to_forget.push(*id);
                    }
                }
            }
        }
        if !denied.is_empty() {
            let problems = denied
                .into_iter()
                .map(|s| FieldError::new("chunk_ids", format!("no grant for scope `{s}`")))
                .collect();
            return Ok(refused(ForgetRefusal::ScopeDenied, problems));
        }

        let report = store.forget_chunks(&to_forget, self.clock.now())?;
        // A concurrent request may have forgotten some of them first.
        already += to_forget.len() as u64 - report.forgotten;
        let count = |n: u64| u32::try_from(n).unwrap_or(u32::MAX);
        Ok(ForgetOutcome::Forgotten {
            forgotten: count(report.forgotten),
            already_forgotten: count(already),
            unknown,
            queued_dropped: count(report.queued_dropped),
            records_dropped: count(report.records_dropped),
            events_tombstoned: count(report.events_tombstoned),
        })
    }
}

fn refused(reason: ForgetRefusal, problems: Vec<FieldError>) -> ForgetOutcome {
    ForgetOutcome::Refused { reason, problems }
}

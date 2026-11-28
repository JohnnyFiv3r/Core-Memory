//! A small fixed-size pool of store connections.
//!
//! Each admission is a short transaction on one connection. The pool hands
//! out up to `size` stores and makes further callers wait, up to a limit,
//! so a burst queues briefly instead of opening unbounded connections.

use std::ops::{Deref, DerefMut};
use std::sync::{Condvar, Mutex, PoisonError};
use std::time::{Duration, Instant};

use core_memory_contract::{Checkpoint, EventId, RecallAnswer, Sha256Digest, Timestamp};
use core_memory_core::store::{
    AdmissionCommit, AdmissionWrite, CallFinish, CallFinished, Change, CheckpointAdvance,
    CheckpointState, EmbedBatch, EmbedFinish, JudgeBatch, JudgeClaim, JudgePermit,
    JudgePermitRequest, JudgeSettle, JudgmentFinish, JudgmentFinished, LeasedToolCall,
    MeaningReady, Permit, PermitRequest, PinKind, PinResult, Purpose, ReadBatch, ScopeInfo, Store,
    StoredEvent, WorkCounts, WorkUpdate,
};
use core_memory_core::{RecallQuery, ScopeGrants, StoreError};

use crate::connect::{ConnectConfig, Connector};
use crate::store::PostgresStore;

#[derive(Debug)]
struct State {
    idle: Vec<PostgresStore>,
    /// Stores handed out or idle. Never more than `size`.
    open: usize,
}

/// A pool of [`PostgresStore`]s on one database.
#[derive(Debug)]
pub struct PostgresPool {
    connector: Connector,
    size: usize,
    state: Mutex<State>,
    returned: Condvar,
}

/// Why no store could be handed out.
#[derive(Debug, thiserror::Error)]
pub enum CheckoutError {
    /// Every connection stayed busy for the whole wait.
    #[error("every store connection is busy")]
    Busy,
    #[error(transparent)]
    Store(#[from] StoreError),
}

impl PostgresPool {
    /// Validates the settings without connecting. `size` is at least 1.
    pub fn new(config: &ConnectConfig, size: usize) -> Result<Self, StoreError> {
        Ok(Self {
            connector: Connector::new(config)?,
            size: size.max(1),
            state: Mutex::new(State {
                idle: Vec::new(),
                open: 0,
            }),
            returned: Condvar::new(),
        })
    }

    /// A store, waiting up to `wait` for one to come back if all are out.
    pub fn get(&self, wait: Duration) -> Result<Pooled<'_>, CheckoutError> {
        // No deadline means a wait too long to add to the clock: forever.
        let deadline = Instant::now().checked_add(wait);
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        loop {
            if let Some(store) = state.idle.pop() {
                return Ok(self.hand_out(store));
            }
            if state.open < self.size {
                state.open += 1;
                return Ok(self.hand_out(PostgresStore::with_connector(self.connector.clone())));
            }
            let left = match deadline {
                Some(deadline) => deadline.saturating_duration_since(Instant::now()),
                None => Duration::from_secs(3_600),
            };
            if left.is_zero() {
                return Err(CheckoutError::Busy);
            }
            state = self
                .returned
                .wait_timeout(state, left)
                .unwrap_or_else(PoisonError::into_inner)
                .0;
        }
    }

    fn hand_out(&self, store: PostgresStore) -> Pooled<'_> {
        Pooled {
            pool: self,
            store: Some(store),
        }
    }

    /// A [`Store`] that borrows a connection for each call, waiting up to
    /// `wait` for one, and gives it back straight after.
    pub fn store(&self, wait: Duration) -> PoolStore<'_> {
        PoolStore { pool: self, wait }
    }
}

/// A [`Store`] over a pool. Each call is one transaction on a connection
/// held only for that call, so the work an admission does between store
/// calls (validating, chunking, computing facets) holds no connection.
#[derive(Debug, Clone, Copy)]
pub struct PoolStore<'a> {
    pool: &'a PostgresPool,
    wait: Duration,
}

impl PoolStore<'_> {
    fn checkout(&self) -> Result<Pooled<'_>, StoreError> {
        self.pool.get(self.wait).map_err(|e| match e {
            // The caller may try again, as for any store that is busy.
            CheckoutError::Busy => StoreError::Unavailable(e.to_string()),
            CheckoutError::Store(e) => e,
        })
    }
}

impl Store for PoolStore<'_> {
    fn scope(&mut self, scope: &str) -> Result<Option<ScopeInfo>, StoreError> {
        self.checkout()?.scope(scope)
    }

    fn event(&mut self, event_id: &EventId) -> Result<Option<StoredEvent>, StoreError> {
        self.checkout()?.event(event_id)
    }

    fn commit_admission(
        &mut self,
        write: &AdmissionWrite<'_>,
    ) -> Result<AdmissionCommit, StoreError> {
        self.checkout()?.commit_admission(write)
    }

    fn advance_checkpoint(
        &mut self,
        scope: &str,
        checkpoint: &Checkpoint,
        at: Timestamp,
    ) -> Result<CheckpointAdvance, StoreError> {
        self.checkout()?.advance_checkpoint(scope, checkpoint, at)
    }

    fn checkpoint(
        &mut self,
        scope: &str,
        key: &str,
    ) -> Result<Option<CheckpointState>, StoreError> {
        self.checkout()?.checkpoint(scope, key)
    }

    fn pin(
        &mut self,
        kind: PinKind,
        id: &str,
        rev: u32,
        hash: &Sha256Digest,
    ) -> Result<PinResult, StoreError> {
        self.checkout()?.pin(kind, id, rev, hash)
    }

    fn export(
        &mut self,
        workspace: &str,
        after: u64,
        limit: u32,
        scope: Option<&str>,
    ) -> Result<Vec<Change>, StoreError> {
        self.checkout()?.export(workspace, after, limit, scope)
    }

    fn recall(&mut self, query: &RecallQuery) -> Result<RecallAnswer, StoreError> {
        self.checkout()?.recall(query)
    }

    fn claim_reads(
        &mut self,
        limit: u32,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<ReadBatch>, StoreError> {
        self.checkout()?
            .claim_reads(limit, max_attempts, now, lease_until)
    }

    fn claim_tool_read(
        &mut self,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<LeasedToolCall>, StoreError> {
        self.checkout()?
            .claim_tool_read(max_attempts, now, lease_until)
    }

    fn permit(&mut self, request: &PermitRequest<'_>) -> Result<Permit, StoreError> {
        self.checkout()?.permit(request)
    }

    fn claim_judgment(&mut self, claim: &JudgeClaim) -> Result<Option<JudgeBatch>, StoreError> {
        self.checkout()?.claim_judgment(claim)
    }

    fn permit_judgment(
        &mut self,
        request: &JudgePermitRequest<'_>,
    ) -> Result<JudgePermit, StoreError> {
        self.checkout()?.permit_judgment(request)
    }

    fn finish_judgment(
        &mut self,
        finish: &JudgmentFinish<'_>,
    ) -> Result<JudgmentFinished, StoreError> {
        self.checkout()?.finish_judgment(finish)
    }

    fn finish_call(&mut self, finish: &CallFinish<'_>) -> Result<CallFinished, StoreError> {
        self.checkout()?.finish_call(finish)
    }

    fn update_work(&mut self, work: &[WorkUpdate]) -> Result<(), StoreError> {
        self.checkout()?.update_work(work)
    }

    fn claim_embeddings(
        &mut self,
        limit: u32,
        max_attempts: u32,
        now: Timestamp,
        lease_until: Timestamp,
    ) -> Result<Option<EmbedBatch>, StoreError> {
        self.checkout()?
            .claim_embeddings(limit, max_attempts, now, lease_until)
    }

    fn finish_embedding(&mut self, finish: &EmbedFinish<'_>) -> Result<CallFinished, StoreError> {
        self.checkout()?.finish_embedding(finish)
    }

    fn meaning_ready(
        &mut self,
        scopes: &ScopeGrants,
        destination: &str,
        embedder_rev: &Sha256Digest,
    ) -> Result<MeaningReady, StoreError> {
        self.checkout()?
            .meaning_ready(scopes, destination, embedder_rev)
    }

    fn settle_judgment(&mut self, settle: &JudgeSettle<'_>) -> Result<(), StoreError> {
        self.checkout()?.settle_judgment(settle)
    }

    fn work_counts(&mut self, purpose: Purpose) -> Result<WorkCounts, StoreError> {
        self.checkout()?.work_counts(purpose)
    }
}

/// A store borrowed from a [`PostgresPool`]. It goes back when dropped.
#[derive(Debug)]
pub struct Pooled<'a> {
    pool: &'a PostgresPool,
    store: Option<PostgresStore>,
}

impl Deref for Pooled<'_> {
    type Target = PostgresStore;

    fn deref(&self) -> &PostgresStore {
        match &self.store {
            Some(store) => store,
            None => unreachable!("a pooled store is present until it is dropped"),
        }
    }
}

impl DerefMut for Pooled<'_> {
    fn deref_mut(&mut self) -> &mut PostgresStore {
        match &mut self.store {
            Some(store) => store,
            None => unreachable!("a pooled store is present until it is dropped"),
        }
    }
}

impl Drop for Pooled<'_> {
    fn drop(&mut self) {
        let mut state = self
            .pool
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        match self.store.take() {
            // A dead connection is not worth keeping; its slot frees up.
            Some(store) if !store.is_closed() => state.idle.push(store),
            _ => state.open = state.open.saturating_sub(1),
        }
        drop(state);
        self.pool.returned.notify_one();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hands_out_at_most_size_stores_and_makes_the_rest_wait() {
        // Stores connect on first use, so this needs no server.
        let config = ConnectConfig::new("postgresql://localhost/none");
        let pool = PostgresPool::new(&config, 2).unwrap();
        let a = pool.get(Duration::ZERO).unwrap();
        let b = pool.get(Duration::ZERO).unwrap();
        assert!(matches!(
            pool.get(Duration::from_millis(20)),
            Err(CheckoutError::Busy)
        ));
        drop(a);
        let c = pool.get(Duration::ZERO).unwrap();
        // A waiter is woken when a store comes back.
        std::thread::scope(|s| {
            let waiter = s.spawn(|| pool.get(Duration::from_secs(5)).is_ok());
            std::thread::sleep(Duration::from_millis(50));
            drop(b);
            assert!(waiter.join().unwrap_or(false));
        });
        drop(c);
    }

    #[test]
    fn a_wait_too_long_to_add_to_the_clock_means_forever() {
        let config = ConnectConfig::new("postgresql://localhost/none");
        let pool = PostgresPool::new(&config, 1).unwrap();
        let held = pool.get(Duration::MAX).unwrap();
        std::thread::scope(|s| {
            let waiter = s.spawn(|| pool.get(Duration::MAX).is_ok());
            std::thread::sleep(Duration::from_millis(50));
            drop(held);
            assert!(waiter.join().unwrap_or(false));
        });
    }
}

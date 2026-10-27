//! The sink conformance suite (CMP-02).
//!
//! Every sink adapter runs the same checks. The engine admits a fixed set
//! of events, classifies them with a deterministic stand-in model and
//! delivers them through the sink under test; the suite then reads back
//! what the sink holds and compares it with what the engine sent. An
//! adapter implements [`Harness`] and adds one line to a test file:
//!
//! ```ignore
//! core_memory_conformance::sink_conformance!(MyHarness::new());
//! ```
//!
//! That expands to one test per check in [`checks`]: delivery of every
//! record once and in order, exact text, replays and refusals adding
//! nothing, a late type following its pending chunk, redelivery after a
//! crash, size limits, scope mapping, and first-writer-wins for systems of
//! record. Tombstone, relationship and erase-plan checks join when the
//! engine produces those record kinds.
//!
//! `golden/standard.jsonl` and `golden/late-type.jsonl` hold the exact
//! records the standard and late-type runs produce. A change to either
//! file is a change to what every sink receives, so it needs the same
//! review as a contract change.

use std::sync::Arc;

use core_memory_contract::{AdmissionOutcome, SourceEvent};
use core_memory_core::store::OutboxEntry;
use core_memory_core::{
    ClassifyReport, DeliveryReport, Engine, ManualClock, MemoryStore, ModelTransport,
    OperationalStore, Sink, deliver_all,
};
use serde_json::Value;

pub mod checks;
pub mod fixture;

/// What the suite needs from a sink adapter.
pub trait Harness {
    /// Opens the sink under test with the given record size limit. The
    /// suite may open it more than once in a check to model a restart; a
    /// new instance sees everything earlier ones wrote.
    fn open(&mut self, max_record_bytes: u64) -> Box<dyn Sink>;

    /// Every record the sink holds, in the order a consumer reads them,
    /// including any copies an at-least-once redelivery left behind.
    fn read(&mut self) -> Vec<Value>;
}

/// Records per sink write in the suite's delivery rounds.
pub const BATCH: usize = 4;

/// A size limit no fixture record comes near.
pub const NO_LIMIT: u64 = 1 << 24;

/// An engine on an in-memory store with the fixture configuration and a
/// manual clock.
#[derive(Debug)]
pub struct Scenario {
    pub engine: Engine,
    pub store: MemoryStore,
    pub clock: Arc<ManualClock>,
}

impl Default for Scenario {
    fn default() -> Self {
        Self::new()
    }
}

impl Scenario {
    pub fn new() -> Self {
        let clock = Arc::new(ManualClock::at_unix_millis(fixture::T0));
        let engine = Engine::new(fixture::config(), Vec::new(), clock.clone())
            .expect("the fixture configuration is valid");
        Self {
            engine,
            store: MemoryStore::new(),
            clock,
        }
    }

    /// Every fixture event admitted and classified by the stand-in model.
    pub fn standard() -> Self {
        let mut s = Self::new();
        for event in fixture::events() {
            let outcome = s.admit(&event);
            assert!(
                matches!(outcome, AdmissionOutcome::Accepted { .. }),
                "{}: {outcome:?}",
                event.external_id
            );
        }
        let report = s.classify(&fixture::keywords());
        assert_eq!(
            s.store.classification_queue_len().expect("store reads"),
            0,
            "{report:?}"
        );
        s
    }

    /// One chunk whose provider stays down past its deadline, so its record
    /// is released as pending, and which is then decided. `between` runs
    /// after the release and again after the decision.
    pub fn late_type(mut between: impl FnMut(&mut Scenario)) -> Self {
        let mut s = Self::new();
        s.admit(&fixture::events()[0]);
        s.classify(&fixture::outage());
        s.clock.advance_millis(fixture::DEADLINE_MS + 1_000);
        s.classify(&fixture::outage());
        between(&mut s);
        s.clock.advance_millis(10_000);
        s.classify(&fixture::keywords());
        between(&mut s);
        s
    }

    pub fn admit(&mut self, event: &SourceEvent) -> AdmissionOutcome {
        self.engine
            .admit(&mut self.store, &fixture::grants(), event)
            .expect("admission does not fail")
    }

    pub fn classify(&mut self, transport: &dyn ModelTransport) -> ClassifyReport {
        self.engine
            .classify_due(&mut self.store, transport, usize::MAX)
            .expect("classification does not fail")
    }

    pub fn deliver(&mut self, sink: &mut dyn Sink) -> DeliveryReport {
        deliver_all(&mut self.store, sink, BATCH).expect("delivery does not fail")
    }

    /// Every record the engine has queued for delivery, in order.
    pub fn outbox(&self) -> Vec<OutboxEntry> {
        self.store.outbox_after(0, usize::MAX).expect("store reads")
    }

    /// The outbox records as JSON.
    pub fn records(&self) -> Vec<Value> {
        self.outbox()
            .iter()
            .map(|e| serde_json::from_str(&e.record_json).expect("outbox records are JSON"))
            .collect()
    }
}

/// Expands to one `#[test]` per check, each with a fresh harness from
/// `$harness`.
#[macro_export]
macro_rules! sink_conformance {
    ($harness:expr) => {
        mod conformance {
            use super::*;

            $crate::sink_conformance!(@tests $harness;
                delivers_every_record_once_in_order,
                keeps_text_exact,
                replays_and_refusals_add_nothing,
                a_late_type_follows_its_pending_chunk,
                redelivery_after_a_crash_is_harmless,
                oversized_records_are_withheld_whole,
                records_keep_their_scope,
                a_system_of_record_keeps_the_first_writer,
            );
        }
    };
    (@tests $harness:expr; $($check:ident),+ $(,)?) => {
        $(
            #[test]
            fn $check() {
                let mut harness = $harness;
                $crate::checks::$check(&mut harness);
            }
        )+
    };
}

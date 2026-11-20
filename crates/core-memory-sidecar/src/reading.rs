//! Having a model read what is queued: what `core-memory read run` does
//! once, and what runs beside `core-memory serve` for as long as it serves.
//!
//! Three kinds of work are done: chunks are read, in batches; tool calls
//! are read, one to a model call; and new beads are judged against the
//! recent ones of their workspace. The engine does the work, one round at
//! a time. This module gives each kind a lane: a thread, a store
//! connection and a transport of its own, so that a slow call of one kind
//! holds no other back. Chunks get as many lanes as `reader.workers` says.
//! The store hands each queued item to one lane at a time. What is logged
//! about a round is counts and codes, never text.

use std::sync::mpsc;
use std::sync::mpsc::Receiver;
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use core_memory_core::{
    CompiledJudge, Engine, EngineError, JudgeReport, JudgeRound, ModelTransport, ReadReport,
    ReadRound, StoreError,
};
use core_memory_store_postgres::PostgresStore;
use serde_json::json;

use crate::config::Loaded;
use crate::report::{self, Stop};
use crate::runtime::{model_roots, open_store, open_transport};

/// How long a lane waits before it looks again when nothing is due.
const IDLE_WAIT: Duration = Duration::from_millis(1_000);
/// How long it waits after the store could not be reached.
const STORE_WAIT: Duration = Duration::from_millis(5_000);
/// How long a lane makes no call after the provider turned its binding
/// down, or its server's certificate was not trusted: a wrong key, model or
/// certificate authority fails every call alike. As long as the engine puts
/// the work of that call off.
const REJECTED_WAIT: Duration =
    Duration::from_millis(core_memory_core::reader::BINDING_FAULT_BACKOFF_MS);
/// What a round reports when the provider turned the binding down, when
/// the server's certificate was not trusted, when the binding's sign-in
/// cannot be used, and when its plan's allowance is spent. Each fails every
/// call alike until someone acts, so the lane waits instead of asking again.
const BINDING_FAULTS: [&str; 4] = [
    "binding_rejected",
    "endpoint_untrusted",
    "signin_required",
    "plan_limit_reached",
];

/// A kind of work.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Chunks,
    ToolCalls,
    Judge,
}

/// One kind of work, done by one thread with a store connection and a
/// transport of its own.
#[derive(Debug)]
struct Lane {
    kind: Kind,
    engine: Arc<Engine>,
    store: PostgresStore,
    transport: Box<dyn ModelTransport>,
    /// Until when lanes of this kind start no call, after the provider
    /// turned their binding down. Shared by the lanes of one kind: once one
    /// call is turned down, no lane starts another. A lane already calling
    /// may be turned down too, so a wrong key is tried at most once by each
    /// worker, not once a round.
    rejected: Arc<Mutex<Option<Instant>>>,
}

/// The configured readers and judge, each kind in lanes of its own.
#[derive(Debug)]
pub struct Reader {
    engine: Arc<Engine>,
    lanes: Vec<Lane>,
}

/// What one round did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Round {
    /// A batch of chunks or one tool call was read.
    Read(ReadReport),
    /// New beads were judged, or one held arrow was read a second time.
    Judged(JudgeReport),
}

/// What a run of the reader did, in total.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Totals {
    pub rounds: u64,
    pub chunks: u64,
    pub tool_calls: u64,
    /// Chunks and tool calls of which something was stored.
    pub stored: u64,
    /// New beads whose judgment was stored.
    pub judged: u64,
    pub verdicts: u64,
    pub calls: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read_tokens: u64,
    pub cache_write_tokens: u64,
}

impl Totals {
    fn add(&mut self, round: &Round) {
        self.rounds += 1;
        let (calls, input_tokens, output_tokens, cache_read, cache_write) = match round {
            Round::Read(report) => {
                self.chunks += u64::from(report.chunks);
                self.tool_calls += u64::from(report.tool_calls);
                self.stored += u64::from(report.stored);
                (
                    report.calls,
                    report.input_tokens,
                    report.output_tokens,
                    report.cache_read_tokens,
                    report.cache_write_tokens,
                )
            }
            Round::Judged(report) => {
                self.judged += u64::from(report.judged);
                self.verdicts += u64::from(report.verdicts);
                (
                    report.calls,
                    report.input_tokens,
                    report.output_tokens,
                    report.cache_read_tokens,
                    report.cache_write_tokens,
                )
            }
        };
        self.calls += u64::from(calls);
        self.input_tokens = self.input_tokens.saturating_add(input_tokens);
        self.output_tokens = self.output_tokens.saturating_add(output_tokens);
        self.cache_read_tokens = self.cache_read_tokens.saturating_add(cache_read);
        self.cache_write_tokens = self.cache_write_tokens.saturating_add(cache_write);
    }
}

impl Round {
    fn problem(&self) -> Option<&'static str> {
        match self {
            Round::Read(report) => report.problem,
            Round::Judged(report) => report.problem,
        }
    }
}

/// One round as a log line.
pub fn describe(round: &Round) -> serde_json::Value {
    match round {
        Round::Read(report) => json!({"read": {
            "scope": report.scope,
            "chunks": report.chunks,
            "tool_calls": report.tool_calls,
            "stored": report.stored,
            "dropped": report.dropped,
            "calls": report.calls,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "cache_read_tokens": report.cache_read_tokens,
            "cache_write_tokens": report.cache_write_tokens,
            "problem": report.problem,
            "reply_faults": report.reply_faults.iter().map(|fault| fault.as_str()).collect::<Vec<_>>(),
            "quotes_dropped": report.quotes_dropped,
        }}),
        Round::Judged(report) => json!({"judged": {
            "workspace": report.workspace,
            "second_read": report.second_read,
            "new_beads": report.new_beads,
            "window_beads": report.window_beads,
            "judged": report.judged,
            "verdicts": report.verdicts,
            "edges": report.edges,
            "chains": report.chains,
            "held": report.held,
            "chains_dropped": report.chains_dropped,
            "groups": report.groups,
            "names": report.names,
            "names_rejected": report.names_rejected,
            "dropped": report.dropped,
            "calls": report.calls,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "cache_read_tokens": report.cache_read_tokens,
            "cache_write_tokens": report.cache_write_tokens,
            "problem": report.problem,
            "reply_faults": report.reply_faults.iter().map(|fault| fault.as_str()).collect::<Vec<_>>(),
        }}),
    }
}

/// A judge as a log line: its binding, where it sends text, and the
/// settings that decide what one read costs.
pub fn describe_judge(judge: &CompiledJudge) -> serde_json::Value {
    json!({
        "binding": judge.binding.id,
        "model": judge.binding.model,
        "destination": judge.destination,
        "second_read": judge.settings.second_read,
        "max_new_beads": judge.settings.max_new_beads,
        "max_window_beads": judge.settings.max_window_beads,
        "max_window_bytes": judge.settings.max_window_bytes,
    })
}

impl Reader {
    /// The readers and the judge `loaded` configures, or `None` if it
    /// configures none of them. Nothing is sent to a model here.
    pub fn open(loaded: &Loaded, engine: Arc<Engine>) -> Result<Option<Self>, Stop> {
        let roots = model_roots(loaded)?;
        let mut lanes = Vec::new();
        let mut add = |kind, binding, count: u32| -> Result<(), Stop> {
            let rejected = Arc::new(Mutex::new(None));
            for _ in 0..count {
                lanes.push(Lane {
                    kind,
                    engine: Arc::clone(&engine),
                    store: open_store(loaded)?,
                    transport: open_transport(loaded, binding, roots.as_ref())?,
                    rejected: Arc::clone(&rejected),
                });
            }
            Ok(())
        };
        if let Some(reader) = engine.reader() {
            add(Kind::Chunks, &reader.binding, reader.settings.workers)?;
        }
        if let Some(reader) = engine.tool_reader() {
            add(Kind::ToolCalls, &reader.binding, 1)?;
        }
        if let Some(judge) = engine.judge() {
            add(Kind::Judge, &judge.binding, 1)?;
        }
        if lanes.is_empty() {
            return Ok(None);
        }
        Ok(Some(Self { engine, lanes }))
    }

    /// The bindings that read and judge, and where they send text.
    pub fn describe(&self) -> serde_json::Value {
        let mut said = serde_json::Map::new();
        if let Some(reader) = self.engine.reader() {
            said.insert(
                "reader".into(),
                json!({
                    "binding": reader.binding.id,
                    "model": reader.binding.model,
                    "destination": reader.destination,
                    "workers": reader.settings.workers,
                }),
            );
        }
        if let Some(reader) = self.engine.tool_reader() {
            said.insert(
                "tool_reader".into(),
                json!({
                    "binding": reader.binding.id,
                    "model": reader.binding.model,
                    "destination": reader.destination,
                }),
            );
        }
        if let Some(judge) = self.engine.judge() {
            said.insert("judge".into(), describe_judge(judge));
        }
        serde_json::Value::Object(said)
    }

    /// Does everything that is due now, every lane at once, and returns
    /// once no lane has anything left to do. `each` is called on this
    /// thread, once for every round, in the order the rounds ended.
    ///
    /// Work that was put off, because its scope gave no consent or a call
    /// failed, is due later and is not waited for, and neither is work
    /// whose binding was turned down. Nor is a bead that waits to be
    /// judged: for a moment after it arrives, so that what arrives with it
    /// is judged with it. A bead that waits for a model to read its chunk
    /// is judged once that chunk is read, in the same run.
    pub fn drain(&mut self, mut each: impl FnMut(&Round)) -> Result<Totals, Stop> {
        let idle = Idle::new(self.lanes.len());
        let (sent, rounds) = mpsc::channel::<Round>();
        let mut totals = Totals::default();
        let ended = std::thread::scope(|scope| {
            let lanes: Vec<_> = self
                .lanes
                .iter_mut()
                .map(|lane| {
                    let (idle, sent) = (&idle, sent.clone());
                    scope.spawn(move || lane.drain(idle, &sent))
                })
                .collect();
            drop(sent);
            // Ends once every lane has stopped and dropped its sender.
            for round in rounds {
                totals.add(&round);
                each(&round);
            }
            let mut first = Ok(());
            for lane in lanes {
                let ended = lane
                    .join()
                    .unwrap_or_else(|panic| std::panic::resume_unwind(panic));
                if first.is_ok() {
                    first = ended;
                }
            }
            first
        });
        ended?;
        Ok(totals)
    }

    /// Works until `stop` is signalled or its sender is dropped, every
    /// lane on a thread of its own, looking again every second while
    /// nothing is due. A store that cannot be reached is waited for, not
    /// given up on. Returns once every lane has finished the round it was
    /// on.
    pub fn run(mut self, stop: &Receiver<()>) {
        let stopping = Stopping::default();
        std::thread::scope(|scope| {
            for lane in &mut self.lanes {
                let stopping = &stopping;
                scope.spawn(move || lane.run(stopping));
            }
            // Nothing is ever sent: the sender is dropped to stop.
            let _ = stop.recv();
            stopping.stop();
        });
    }
}

impl Lane {
    /// One round of this lane's kind of work. `None` when none is due.
    ///
    /// Lanes whose binding the provider turned down make no call for a
    /// while, whatever they have queued: the next call would be turned down
    /// too, and would still count against the day's budget.
    fn round(&mut self) -> Result<Option<Round>, EngineError> {
        let now = Instant::now();
        let rejected = || self.rejected.lock().unwrap_or_else(PoisonError::into_inner);
        if rejected().is_some_and(|until| now < until) {
            return Ok(None);
        }
        let transport = self.transport.as_ref();
        let did = match self.kind {
            Kind::Chunks => read(self.engine.read_once(&mut self.store, transport)?),
            Kind::ToolCalls => read(self.engine.read_tool_once(&mut self.store, transport)?),
            Kind::Judge => match self.engine.judge_once(&mut self.store, transport)? {
                JudgeRound::Idle => None,
                JudgeRound::Handled(report) => Some(Round::Judged(report)),
            },
        };
        if did
            .as_ref()
            .and_then(Round::problem)
            .is_some_and(|problem| BINDING_FAULTS.contains(&problem))
        {
            *self.rejected.lock().unwrap_or_else(PoisonError::into_inner) =
                Some(now + REJECTED_WAIT);
        }
        Ok(did)
    }

    /// This lane's part of [`Reader::drain`].
    fn drain(&mut self, idle: &Idle, rounds: &mpsc::Sender<Round>) -> Result<(), EngineError> {
        drain_lane(idle, || self.round(), rounds)
    }

    /// This lane's part of [`Reader::run`].
    fn run(&mut self, stopping: &Stopping) {
        loop {
            let pause = match self.round() {
                Ok(Some(round)) => {
                    report::log(describe(&round));
                    Duration::ZERO
                }
                Ok(None) => IDLE_WAIT,
                Err(EngineError::Store(StoreError::Unavailable(cause))) => {
                    report::log(json!({"reader_store_unavailable": cause}));
                    STORE_WAIT
                }
                Err(e) => {
                    report::log(json!({"reader_error": e.to_string()}));
                    STORE_WAIT
                }
            };
            if stopping.waited(pause) {
                return;
            }
        }
    }
}

/// One lane's part of a drain: rounds until no lane has anything to do,
/// each sent to `rounds`.
fn drain_lane<E>(
    idle: &Idle,
    mut round: impl FnMut() -> Result<Option<Round>, E>,
    rounds: &mpsc::Sender<Round>,
) -> Result<(), E> {
    // A lane that panics ends the drain, or the others would wait for it
    // for good.
    let _ending = GiveUpOnPanic(idle);
    loop {
        let Some(seen) = idle.done_so_far() else {
            // Another lane failed.
            return Ok(());
        };
        match round() {
            Ok(Some(done)) => {
                idle.did_a_round();
                // The receiver stops only once every lane has ended.
                let _ = rounds.send(done);
            }
            Ok(None) => {
                if !idle.wait_for_more(seen) {
                    return Ok(());
                }
            }
            Err(e) => {
                idle.give_up();
                return Err(e);
            }
        }
    }
}

/// When the lanes of a drain are done: when every one of them has found
/// nothing to do since the last round any lane did. A round of one kind
/// can make work of another due, as reading a chunk does for judging its
/// bead, so a lane that found nothing waits while others work, and looks
/// again each time one of them finishes a round.
#[derive(Debug)]
struct Idle {
    state: Mutex<IdleState>,
    changed: Condvar,
}

#[derive(Debug)]
struct IdleState {
    /// Lanes that are working or about to look again.
    working: usize,
    /// Rounds done by all lanes so far.
    rounds: u64,
    /// Whether the drain is over.
    done: bool,
}

impl Idle {
    fn new(lanes: usize) -> Self {
        Self {
            state: Mutex::new(IdleState {
                working: lanes,
                rounds: 0,
                done: lanes == 0,
            }),
            changed: Condvar::new(),
        }
    }

    fn lock(&self) -> MutexGuard<'_, IdleState> {
        // The state is counts, consistent between statements, so a lane
        // that panicked while holding it left nothing half done.
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Rounds done so far, to tell later whether any was done since.
    /// `None` once the drain is over.
    fn done_so_far(&self) -> Option<u64> {
        let state = self.lock();
        (!state.done).then_some(state.rounds)
    }

    fn did_a_round(&self) {
        self.lock().rounds += 1;
        self.changed.notify_all();
    }

    /// Ends the drain, for a lane that failed.
    fn give_up(&self) {
        self.lock().done = true;
        self.changed.notify_all();
    }

    /// Called by a lane that found nothing to do, having seen `seen`
    /// rounds before it looked. `true` when it should look again, because
    /// another lane did a round since; `false` when the drain is over.
    fn wait_for_more(&self, seen: u64) -> bool {
        let mut state = self.lock();
        if state.done {
            return false;
        }
        if state.rounds != seen {
            return true;
        }
        state.working -= 1;
        if state.working == 0 {
            state.done = true;
            self.changed.notify_all();
            return false;
        }
        let mut state = self
            .changed
            .wait_while(state, |state| !state.done && state.rounds == seen)
            .unwrap_or_else(PoisonError::into_inner);
        if state.done {
            return false;
        }
        state.working += 1;
        true
    }
}

/// Ends a drain when the lane that holds it panics.
struct GiveUpOnPanic<'a>(&'a Idle);

impl Drop for GiveUpOnPanic<'_> {
    fn drop(&mut self) {
        if std::thread::panicking() {
            self.0.give_up();
        }
    }
}

/// Tells the lanes of [`Reader::run`] to stop.
#[derive(Debug, Default)]
struct Stopping {
    stopped: Mutex<bool>,
    changed: Condvar,
}

impl Stopping {
    fn stop(&self) {
        *self.stopped.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.changed.notify_all();
    }

    /// Waits up to `pause`, or not at all if it is zero. `true` once the
    /// lanes are told to stop.
    fn waited(&self, pause: Duration) -> bool {
        let stopped = self.stopped.lock().unwrap_or_else(PoisonError::into_inner);
        if *stopped || pause.is_zero() {
            return *stopped;
        }
        let (stopped, _) = self
            .changed
            .wait_timeout_while(stopped, pause, |stopped| !*stopped)
            .unwrap_or_else(PoisonError::into_inner);
        *stopped
    }
}

fn read(round: ReadRound) -> Option<Round> {
    match round {
        ReadRound::Idle => None,
        ReadRound::Handled(report) => Some(Round::Read(report)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A round of nothing in particular.
    fn a_round() -> Round {
        Round::Read(ReadReport::default())
    }

    /// Drains `lanes` as [`Reader::drain`] does, each a function that
    /// says whether it did a round. Returns the rounds done.
    fn drained(
        lanes: Vec<Box<dyn FnMut() -> Result<bool, &'static str> + Send + '_>>,
    ) -> (usize, Result<(), &'static str>) {
        let idle = Idle::new(lanes.len());
        let (sent, rounds) = mpsc::channel();
        let ended = std::thread::scope(|scope| {
            let handles: Vec<_> = lanes
                .into_iter()
                .map(|mut lane| {
                    let (idle, sent) = (&idle, sent.clone());
                    scope.spawn(move || {
                        drain_lane(idle, || lane().map(|did| did.then(a_round)), &sent)
                    })
                })
                .collect();
            drop(sent);
            handles
                .into_iter()
                .map(|h| h.join().unwrap())
                .find(Result::is_err)
                .unwrap_or(Ok(()))
        });
        (rounds.iter().count(), ended)
    }

    #[test]
    fn a_lane_waits_while_another_works_and_takes_what_that_work_made_due() {
        // Four chunks to read, and a bead to judge for each, due only once
        // its chunk is read: the judge finds nothing at first.
        let done = Mutex::new((0u32, 0u32));
        let reader = || {
            std::thread::sleep(Duration::from_millis(5));
            let mut done = done.lock().unwrap();
            let more = done.0 < 4;
            done.0 += u32::from(more);
            Ok(more)
        };
        let judge = || {
            let mut done = done.lock().unwrap();
            let more = done.1 < done.0;
            done.1 += u32::from(more);
            Ok(more)
        };
        let (rounds, ended) = drained(vec![Box::new(reader), Box::new(judge)]);
        assert_eq!(ended, Ok(()));
        assert_eq!(rounds, 8);
        assert_eq!(done.into_inner().unwrap(), (4, 4));
    }

    #[test]
    fn a_drain_with_nothing_due_ends_at_once() {
        let (rounds, ended) = drained(vec![Box::new(|| Ok(false)), Box::new(|| Ok(false))]);
        assert_eq!((rounds, ended), (0, Ok(())));
    }

    #[test]
    fn a_lane_that_fails_ends_the_drain_for_every_lane() {
        // The other lane would work for ever.
        let busy = || Ok(true);
        let failing = || Err("the store failed");
        let (_, ended) = drained(vec![Box::new(busy), Box::new(failing)]);
        assert_eq!(ended, Err("the store failed"));
    }

    #[test]
    fn a_lane_that_panics_ends_the_drain_for_every_lane() {
        let idle = Idle::new(2);
        let (sent, _rounds) = mpsc::channel();
        let panicked = std::thread::scope(|scope| {
            let waiting = {
                let (idle, sent) = (&idle, sent.clone());
                scope.spawn(move || drain_lane(idle, || Ok::<_, ()>(None), &sent))
            };
            let panicking = {
                let (idle, sent) = (&idle, sent.clone());
                scope.spawn(move || {
                    drain_lane(
                        idle,
                        || -> Result<Option<Round>, ()> {
                            std::thread::sleep(Duration::from_millis(20));
                            panic!("a bug")
                        },
                        &sent,
                    )
                })
            };
            assert_eq!(waiting.join().unwrap(), Ok(()));
            panicking.join().is_err()
        });
        assert!(panicked);
    }

    #[test]
    fn a_lane_told_to_stop_stops_waiting() {
        let stopping = Stopping::default();
        assert!(!stopping.waited(Duration::ZERO));
        assert!(!stopping.waited(Duration::from_millis(1)));
        std::thread::scope(|scope| {
            let waiting = scope.spawn(|| stopping.waited(Duration::from_secs(60)));
            std::thread::sleep(Duration::from_millis(20));
            stopping.stop();
            assert!(waiting.join().unwrap());
        });
        assert!(stopping.waited(Duration::ZERO));
    }
}

//! Having a model read what is queued: what `core-memory read run` does
//! once, and what runs beside `core-memory serve` for as long as it serves.
//!
//! Three kinds of work are done: chunks are read, in batches; tool calls
//! are read, one to a model call; and new beads are judged against the
//! recent ones of their workspace. The engine does the work, one round at
//! a time. This module gives it a store connection of its own and a
//! transport for each kind, and decides when to ask again. What it logs
//! about a round is counts and codes, never text.

use std::sync::Arc;
use std::sync::mpsc::{Receiver, RecvTimeoutError};
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

/// How long the reader waits before it looks again when nothing is due.
const IDLE_WAIT: Duration = Duration::from_millis(1_000);
/// How long it waits after the store could not be reached.
const STORE_WAIT: Duration = Duration::from_millis(5_000);
/// The kinds of work: chunks, tool calls and judging.
const KINDS: usize = 3;
/// How long a kind of work makes no call after the provider turned its
/// binding down: a wrong key or model fails every call alike. As long as
/// the engine puts the work of that call off.
const REJECTED_WAIT: Duration =
    Duration::from_millis(core_memory_core::reader::BINDING_FAULT_BACKOFF_MS);
/// What a round reports when the provider turned the binding down.
const BINDING_REJECTED: &str = "binding_rejected";

/// The configured readers and judge with a store connection of their own.
#[derive(Debug)]
pub struct Reader {
    engine: Arc<Engine>,
    store: PostgresStore,
    /// The transport of the chunk reader's binding, if one is configured.
    chunks: Option<Box<dyn ModelTransport>>,
    /// The transport of the tool reader's binding, if one is configured.
    tool_calls: Option<Box<dyn ModelTransport>>,
    /// The transport of the judge's binding, if one is configured.
    judge: Option<Box<dyn ModelTransport>>,
    /// Which kind of work the next round looks for first, so that a long
    /// queue of one kind does not hold the others back.
    next: usize,
    /// Until when each kind makes no call, after its binding was turned
    /// down.
    rejected: [Option<Instant>; KINDS],
}

/// What one round did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Round {
    /// A batch of chunks or one tool call was read.
    Read(ReadReport),
    /// New beads were judged, or one held label was read a second time.
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
}

impl Totals {
    fn add(&mut self, round: &Round) {
        self.rounds += 1;
        let (calls, input_tokens, output_tokens) = match round {
            Round::Read(report) => {
                self.chunks += u64::from(report.chunks);
                self.tool_calls += u64::from(report.tool_calls);
                self.stored += u64::from(report.stored);
                (report.calls, report.input_tokens, report.output_tokens)
            }
            Round::Judged(report) => {
                self.judged += u64::from(report.judged);
                self.verdicts += u64::from(report.verdicts);
                (report.calls, report.input_tokens, report.output_tokens)
            }
        };
        self.calls += u64::from(calls);
        self.input_tokens = self.input_tokens.saturating_add(input_tokens);
        self.output_tokens = self.output_tokens.saturating_add(output_tokens);
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
            "problem": report.problem,
        }}),
        Round::Judged(report) => json!({"judged": {
            "workspace": report.workspace,
            "second_read": report.second_read,
            "new_beads": report.new_beads,
            "window_beads": report.window_beads,
            "judged": report.judged,
            "verdicts": report.verdicts,
            "edges": report.edges,
            "labels": report.labels,
            "held": report.held,
            "labels_dropped": report.labels_dropped,
            "groups": report.groups,
            "names": report.names,
            "names_rejected": report.names_rejected,
            "dropped": report.dropped,
            "calls": report.calls,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
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
        if engine.reader().is_none() && engine.tool_reader().is_none() && engine.judge().is_none() {
            return Ok(None);
        }
        let roots = model_roots(loaded)?;
        let chunks = engine
            .reader()
            .map(|reader| open_transport(&reader.binding, roots.as_ref()))
            .transpose()?;
        let tool_calls = engine
            .tool_reader()
            .map(|reader| open_transport(&reader.binding, roots.as_ref()))
            .transpose()?;
        let judge = engine
            .judge()
            .map(|judge| open_transport(&judge.binding, roots.as_ref()))
            .transpose()?;
        let store = open_store(loaded)?;
        Ok(Some(Self {
            engine,
            store,
            chunks,
            tool_calls,
            judge,
            next: 0,
            rejected: [None; KINDS],
        }))
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

    /// One round of chunks, if a chunk reader is configured.
    fn chunk_round(&mut self) -> Result<Option<Round>, EngineError> {
        Ok(match &self.chunks {
            Some(transport) => read(self.engine.read_once(&mut self.store, transport.as_ref())?),
            None => None,
        })
    }

    /// One tool call, if a tool reader is configured.
    fn tool_round(&mut self) -> Result<Option<Round>, EngineError> {
        Ok(match &self.tool_calls {
            Some(transport) => read(
                self.engine
                    .read_tool_once(&mut self.store, transport.as_ref())?,
            ),
            None => None,
        })
    }

    /// One read by the judge, if a judge is configured.
    fn judge_round(&mut self) -> Result<Option<Round>, EngineError> {
        Ok(match &self.judge {
            Some(transport) => {
                match self
                    .engine
                    .judge_once(&mut self.store, transport.as_ref())?
                {
                    JudgeRound::Idle => None,
                    JudgeRound::Handled(report) => Some(Round::Judged(report)),
                }
            }
            None => None,
        })
    }

    /// One round of whichever kind of work is due, each kind going first
    /// in its turn when more than one is. `None` only when none is.
    ///
    /// A kind whose binding the provider turned down makes no call for a
    /// while, whatever else it has queued: the next call would be turned
    /// down too, and would still count against the day's budget.
    fn round(&mut self) -> Result<Option<Round>, EngineError> {
        let first = self.next;
        self.next = (first + 1) % KINDS;
        let now = Instant::now();
        for turn in 0..KINDS {
            let kind = (first + turn) % KINDS;
            if self.rejected[kind].is_some_and(|until| now < until) {
                continue;
            }
            let did = match kind {
                0 => self.chunk_round()?,
                1 => self.tool_round()?,
                _ => self.judge_round()?,
            };
            if let Some(round) = &did {
                if round.problem() == Some(BINDING_REJECTED) {
                    self.rejected[kind] = Some(now + REJECTED_WAIT);
                }
                return Ok(did);
            }
        }
        Ok(None)
    }

    /// Does everything that is due now. Work that was put off, because its
    /// scope gave no consent or a call failed, is due later and is not
    /// waited for, and neither is work whose binding was turned down. Nor is a bead that waits to be judged: for a moment
    /// after it arrives, so that what arrives with it is judged with it,
    /// and for a model to read its chunk.
    pub fn drain(&mut self, mut each: impl FnMut(&Round)) -> Result<Totals, Stop> {
        let mut totals = Totals::default();
        while let Some(round) = self.round()? {
            totals.add(&round);
            each(&round);
        }
        Ok(totals)
    }

    /// Works until `stop` is signalled or its sender is dropped, looking
    /// again every second while nothing is due. A store that cannot be
    /// reached is waited for, not given up on.
    pub fn run(mut self, stop: &Receiver<()>) {
        let wait =
            |pause: Duration| matches!(stop.recv_timeout(pause), Err(RecvTimeoutError::Timeout));
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
            if !wait(pause) {
                return;
            }
        }
    }
}

fn read(round: ReadRound) -> Option<Round> {
    match round {
        ReadRound::Idle => None,
        ReadRound::Handled(report) => Some(Round::Read(report)),
    }
}

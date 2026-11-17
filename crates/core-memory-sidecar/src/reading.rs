//! Having a model read what is queued: what `core-memory read run` does
//! once, and what runs beside `core-memory serve` for as long as it serves.
//!
//! Two kinds of work are read: chunks, in batches, and tool calls, one to
//! a model call. The engine does the work, one round at a time. This
//! module gives it a store connection of its own and a transport for each
//! kind, and decides when to ask again. What it logs about a round is
//! counts and codes, never text.

use std::sync::Arc;
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::time::Duration;

use core_memory_core::{Engine, EngineError, ModelTransport, ReadReport, ReadRound, StoreError};
use core_memory_store_postgres::PostgresStore;
use serde_json::json;

use crate::config::Loaded;
use crate::report::{self, Stop};
use crate::runtime::{open_store, open_transport};

/// How long the reader waits before it looks again when nothing is due.
const IDLE_WAIT: Duration = Duration::from_millis(1_000);
/// How long it waits after the store could not be reached.
const STORE_WAIT: Duration = Duration::from_millis(5_000);

/// The configured readers with a store connection of their own.
#[derive(Debug)]
pub struct Reader {
    engine: Arc<Engine>,
    store: PostgresStore,
    /// The transport of the chunk reader's binding, if one is configured.
    chunks: Option<Box<dyn ModelTransport>>,
    /// The transport of the tool reader's binding, if one is configured.
    tool_calls: Option<Box<dyn ModelTransport>>,
    /// Which kind of work the next round looks for first, so that a long
    /// queue of one kind does not hold the other back.
    tool_calls_first: bool,
}

/// What a run of the reader did, in total.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Totals {
    pub rounds: u64,
    pub chunks: u64,
    pub tool_calls: u64,
    pub stored: u64,
    pub calls: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

impl Totals {
    fn add(&mut self, report: &ReadReport) {
        self.rounds += 1;
        self.chunks += u64::from(report.chunks);
        self.tool_calls += u64::from(report.tool_calls);
        self.stored += u64::from(report.stored);
        self.calls += u64::from(report.calls);
        self.input_tokens += report.input_tokens;
        self.output_tokens += report.output_tokens;
    }
}

/// One round as a log line.
pub fn describe(report: &ReadReport) -> serde_json::Value {
    json!({"read": {
        "scope": report.scope,
        "chunks": report.chunks,
        "tool_calls": report.tool_calls,
        "stored": report.stored,
        "dropped": report.dropped,
        "calls": report.calls,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "problem": report.problem,
    }})
}

impl Reader {
    /// The readers `loaded` configures, or `None` if it configures none.
    /// Nothing is sent to a model here.
    pub fn open(loaded: &Loaded, engine: Arc<Engine>) -> Result<Option<Self>, Stop> {
        let chunks = engine
            .reader()
            .map(|reader| open_transport(&reader.binding))
            .transpose()?;
        let tool_calls = engine
            .tool_reader()
            .map(|reader| open_transport(&reader.binding))
            .transpose()?;
        if chunks.is_none() && tool_calls.is_none() {
            return Ok(None);
        }
        let store = open_store(loaded)?;
        Ok(Some(Self {
            engine,
            store,
            chunks,
            tool_calls,
            tool_calls_first: false,
        }))
    }

    /// The bindings that read and where they send text.
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
        serde_json::Value::Object(said)
    }

    /// One round of chunks, if a chunk reader is configured.
    fn chunk_round(&mut self) -> Result<ReadRound, EngineError> {
        match &self.chunks {
            Some(transport) => self.engine.read_once(&mut self.store, transport.as_ref()),
            None => Ok(ReadRound::Idle),
        }
    }

    /// One tool call, if a tool reader is configured.
    fn tool_round(&mut self) -> Result<ReadRound, EngineError> {
        match &self.tool_calls {
            Some(transport) => self
                .engine
                .read_tool_once(&mut self.store, transport.as_ref()),
            None => Ok(ReadRound::Idle),
        }
    }

    /// One round of whichever kind of work is due, taking turns between
    /// the two when both are. Idle only when neither is.
    fn round(&mut self) -> Result<ReadRound, EngineError> {
        let tool_calls_first = self.tool_calls_first;
        self.tool_calls_first = !tool_calls_first;
        let first = if tool_calls_first {
            self.tool_round()?
        } else {
            self.chunk_round()?
        };
        if first != ReadRound::Idle {
            return Ok(first);
        }
        if tool_calls_first {
            self.chunk_round()
        } else {
            self.tool_round()
        }
    }

    /// Reads everything that is due now. Work that was put off, because
    /// its scope gave no consent or a call failed, is due later and is not
    /// waited for.
    pub fn drain(&mut self, mut each: impl FnMut(&ReadReport)) -> Result<Totals, Stop> {
        let mut totals = Totals::default();
        loop {
            match self.round()? {
                ReadRound::Idle => return Ok(totals),
                ReadRound::Handled(report) => {
                    totals.add(&report);
                    each(&report);
                }
            }
        }
    }

    /// Reads until `stop` is signalled or its sender is dropped, looking
    /// again every second while nothing is due. A store that cannot be
    /// reached is waited for, not given up on.
    pub fn run(mut self, stop: &Receiver<()>) {
        let wait =
            |pause: Duration| matches!(stop.recv_timeout(pause), Err(RecvTimeoutError::Timeout));
        loop {
            let pause = match self.round() {
                Ok(ReadRound::Handled(report)) => {
                    report::log(describe(&report));
                    Duration::ZERO
                }
                Ok(ReadRound::Idle) => IDLE_WAIT,
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

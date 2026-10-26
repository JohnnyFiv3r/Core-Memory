//! `core-memory`: run the Core Memory engine over JSON Lines.

mod config;

use std::collections::BTreeMap;
use std::io::{self, BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use clap::{Parser, Subcommand};
use core_memory_contract::schema::Contract;
use core_memory_contract::{AdmissionOutcome, FieldError, RefusalReason};
use core_memory_core::store::OperationalStore;
use core_memory_core::{DeliveryReport, Engine, Sink, SystemClock, deliver_all};
use core_memory_sink_jsonl::JsonlSink;
use core_memory_source_jsonl::{JsonlSource, SourceItem};
use core_memory_store_sqlite::SqliteStore;
use serde_json::json;

use crate::config::{Loaded, SinkKind};

/// Busy retries per event before giving up on it.
const BUSY_ATTEMPTS: u32 = 5;
const DELIVERY_BATCH: usize = 512;

#[derive(Parser)]
#[command(name = "core-memory", version, about = "Core Memory engine")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Admit SourceEvents from JSON Lines and deliver records to the sinks.
    /// Prints one admission outcome per input line.
    Ingest {
        #[arg(long, default_value = "core-memory.toml")]
        config: PathBuf,
        /// Input file, or `-` for standard input.
        #[arg(long, default_value = "-")]
        input: String,
        /// Queue records without delivering them.
        #[arg(long)]
        no_deliver: bool,
    },
    /// Deliver queued records to every configured sink.
    Deliver {
        #[arg(long, default_value = "core-memory.toml")]
        config: PathBuf,
    },
    /// Validate a config file and print its profiles. Does not open the store.
    Check {
        #[arg(long, default_value = "core-memory.toml")]
        config: PathBuf,
    },
    /// Print a contract's JSON Schema, or list contracts and their digests.
    Schema { name: Option<String> },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let result = match cli.command {
        Command::Ingest {
            config,
            input,
            no_deliver,
        } => ingest(&config, &input, !no_deliver),
        Command::Deliver { config } => Runtime::open(&config).and_then(|mut rt| rt.deliver()),
        Command::Check { config } => check(&config),
        Command::Schema { name } => schema(name.as_deref()),
    };
    match result {
        Ok(code) => code,
        Err(message) => {
            eprintln!("core-memory: {message}");
            ExitCode::FAILURE
        }
    }
}

struct Runtime {
    loaded: Loaded,
    engine: Engine,
    store: SqliteStore,
    sinks: Vec<JsonlSink>,
}

impl Runtime {
    fn open(config_path: &std::path::Path) -> Result<Self, String> {
        let loaded = Loaded::read(config_path)?;
        let engine = Engine::new(
            loaded.engine_config(),
            loaded.required_sinks(),
            Arc::new(SystemClock),
        )
        .map_err(|e| format!("configuration: {e}"))?;
        let store_path = loaded.resolve(&loaded.config.store.path);
        let mut store =
            SqliteStore::open(&store_path).map_err(|e| format!("{}: {e}", store_path.display()))?;
        engine.pin_profiles(&mut store).map_err(|e| e.to_string())?;
        let mut sinks = Vec::new();
        for s in &loaded.config.sinks {
            match s.kind {
                SinkKind::Jsonl => {
                    let path = loaded.resolve(&s.path);
                    sinks.push(
                        JsonlSink::open(&s.id, &path, s.max_record_bytes)
                            .map_err(|e| format!("sink {}: {}: {e}", s.id, path.display()))?,
                    );
                }
            }
        }
        Ok(Self {
            loaded,
            engine,
            store,
            sinks,
        })
    }

    /// Delivers to every sink, prunes what the required sinks have taken,
    /// and prints a report per sink to stderr.
    fn deliver(&mut self) -> Result<ExitCode, String> {
        let mut pending = false;
        for sink in &mut self.sinks {
            let report =
                deliver_all(&mut self.store, sink, DELIVERY_BATCH).map_err(|e| e.to_string())?;
            pending |= report.retry_after_ms.is_some();
            eprintln!("{}", delivery_line(sink.id(), &report));
        }
        self.store
            .prune_outbox(self.engine.required_sinks())
            .map_err(|e| e.to_string())?;
        Ok(if pending {
            ExitCode::from(2)
        } else {
            ExitCode::SUCCESS
        })
    }
}

fn delivery_line(id: &str, r: &DeliveryReport) -> String {
    json!({
        "sink": id,
        "accepted": r.accepted,
        "already_accepted": r.already_accepted,
        "failed": r.failed,
        "skipped_gap": r.skipped_gap,
        "retry_after_ms": r.retry_after_ms,
    })
    .to_string()
}

fn ingest(config_path: &std::path::Path, input: &str, deliver: bool) -> Result<ExitCode, String> {
    let mut rt = Runtime::open(config_path)?;
    let grants = rt.loaded.grants();
    let reader: Box<dyn BufRead> = if input == "-" {
        Box::new(BufReader::new(io::stdin().lock()))
    } else {
        let file = std::fs::File::open(input).map_err(|e| format!("{input}: {e}"))?;
        Box::new(BufReader::new(file))
    };
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut counts: BTreeMap<&'static str, u64> = BTreeMap::new();

    for item in JsonlSource::new(reader) {
        let item = item.map_err(|e| format!("{input}: {e}"))?;
        let (line, external_id, outcome) = match item {
            SourceItem::Unreadable { line, problem } => (
                line,
                None,
                AdmissionOutcome::Refused {
                    reason: RefusalReason::Malformed,
                    problems: vec![FieldError::new("line", problem)],
                },
            ),
            SourceItem::Event { line, event } => {
                let mut attempt = 0;
                let outcome = loop {
                    let outcome = rt
                        .engine
                        .admit(&mut rt.store, &grants, &event)
                        .map_err(|e| e.to_string())?;
                    let AdmissionOutcome::Busy { retry_after_ms, .. } = outcome else {
                        break outcome;
                    };
                    attempt += 1;
                    if attempt >= BUSY_ATTEMPTS {
                        break outcome;
                    }
                    // The sinks are in this process: drain them, then retry.
                    if deliver {
                        rt.deliver()?;
                    } else {
                        std::thread::sleep(Duration::from_millis(retry_after_ms));
                    }
                };
                (line, Some(event.external_id.clone()), outcome)
            }
        };
        *counts.entry(outcome_name(&outcome)).or_default() += 1;
        let line_json = json!({"line": line, "external_id": external_id, "result": outcome});
        writeln!(out, "{line_json}").map_err(|e| format!("stdout: {e}"))?;
    }
    out.flush().map_err(|e| format!("stdout: {e}"))?;
    eprintln!("{}", json!({"admitted": counts}));
    if deliver {
        rt.deliver()
    } else {
        Ok(ExitCode::SUCCESS)
    }
}

fn outcome_name(o: &AdmissionOutcome) -> &'static str {
    match o {
        AdmissionOutcome::Accepted { .. } => "accepted",
        AdmissionOutcome::Replay { .. } => "replay",
        AdmissionOutcome::IdentityConflict { .. } => "identity_conflict",
        AdmissionOutcome::Refused { .. } => "refused",
        AdmissionOutcome::Busy { .. } => "busy",
    }
}

fn check(config_path: &std::path::Path) -> Result<ExitCode, String> {
    let loaded = Loaded::read(config_path)?;
    let engine = Engine::new(
        loaded.engine_config(),
        loaded.required_sinks(),
        Arc::new(SystemClock),
    )
    .map_err(|e| format!("configuration: {e}"))?;
    for p in engine.registry().iter() {
        println!(
            "{}",
            json!({
                "profile": p.def.id,
                "rev": p.def.rev,
                "hash": p.hash,
                "content": p.def.content,
                "chunker": p.chunker_ref,
                "rules": p.rules.len(),
            })
        );
    }
    Ok(ExitCode::SUCCESS)
}

fn schema(name: Option<&str>) -> Result<ExitCode, String> {
    match name {
        None => {
            for c in Contract::ALL {
                println!("{}\t{}", c.name(), c.digest());
            }
        }
        Some(name) => {
            let contract = Contract::from_name(name).ok_or_else(|| {
                let names: Vec<_> = Contract::ALL.iter().map(|c| c.name()).collect();
                format!(
                    "unknown contract `{name}`; choose one of {}",
                    names.join(", ")
                )
            })?;
            let text =
                serde_json::to_string_pretty(&contract.schema()).map_err(|e| e.to_string())?;
            println!("{text}");
        }
    }
    Ok(ExitCode::SUCCESS)
}

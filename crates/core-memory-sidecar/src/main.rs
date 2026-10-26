//! `core-memory`: run the Core Memory engine over JSON Lines or HTTP.

use std::collections::BTreeMap;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use clap::{Parser, Subcommand};
use core_memory_contract::schema::Contract;
use core_memory_contract::{AdmissionOutcome, FieldError, RefusalReason, Sha256Digest};
use core_memory_sidecar::config::Loaded;
use core_memory_sidecar::runtime::{Runtime, build_engine};
use core_memory_sidecar::{openapi, server};
use core_memory_source_jsonl::{JsonlSource, SourceItem};
use serde_json::json;

/// Busy retries per event before giving up on it.
const BUSY_ATTEMPTS: u32 = 5;

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
        /// Keep records held for a model type until their deadline instead
        /// of releasing them as pending when the run ends.
        #[arg(long)]
        keep_holds: bool,
    },
    /// Run a model classification pass (retries and late decisions), then
    /// deliver.
    Classify {
        #[arg(long, default_value = "core-memory.toml")]
        config: PathBuf,
        #[arg(long)]
        keep_holds: bool,
    },
    /// Serve the engine over HTTP, delivering records in the background.
    Serve {
        #[arg(long, default_value = "core-memory.toml")]
        config: PathBuf,
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
    /// Generate an HTTP bearer token and the config lines that accept it.
    Token {
        /// Token id to put in the config.
        #[arg(long, default_value = "ingest")]
        id: String,
    },
    /// Print a contract's JSON Schema, or list contracts and their digests.
    Schema { name: Option<String> },
    /// Print the HTTP API's OpenAPI document.
    Openapi,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let result = match cli.command {
        Command::Ingest {
            config,
            input,
            no_deliver,
            keep_holds,
        } => ingest(&config, &input, !no_deliver, keep_holds),
        Command::Classify { config, keep_holds } => Runtime::open(&config)
            .and_then(|mut rt| {
                rt.classify(keep_holds)?;
                rt.deliver()
            })
            .map(exit_for_delivery),
        Command::Serve { config } => serve(&config),
        Command::Deliver { config } => Runtime::open(&config)
            .and_then(|mut rt| rt.deliver())
            .map(exit_for_delivery),
        Command::Check { config } => check(&config),
        Command::Token { id } => token(&id),
        Command::Schema { name } => schema(name.as_deref()),
        Command::Openapi => print_json(&openapi::document()),
    };
    match result {
        Ok(code) => code,
        Err(message) => {
            eprintln!("core-memory: {message}");
            ExitCode::FAILURE
        }
    }
}

/// Exit status 2 when a sink asked to retry later and records remain queued.
fn exit_for_delivery(complete: bool) -> ExitCode {
    if complete {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(2)
    }
}

fn ingest(
    config_path: &Path,
    input: &str,
    deliver: bool,
    keep_holds: bool,
) -> Result<ExitCode, String> {
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
    rt.classify(keep_holds)?;
    if deliver {
        rt.deliver().map(exit_for_delivery)
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

fn serve(config_path: &Path) -> Result<ExitCode, String> {
    let loaded = Loaded::read(config_path)?;
    let prepared = server::prepare(&loaded, true)?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("cannot start the async runtime: {e}"))?;
    let router = prepared.router.clone();
    let served = runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind(&prepared.listen)
            .await
            .map_err(|e| format!("cannot listen on {}: {e}", prepared.listen))?;
        let addr = listener.local_addr().map_err(|e| e.to_string())?;
        eprintln!("{}", json!({"listening": addr.to_string()}));
        axum::serve(listener, router)
            .with_graceful_shutdown(shutdown_signal())
            .await
            .map_err(|e| format!("server: {e}"))
    });
    // Deliver what was admitted before shutting down (ENG-03).
    prepared.shutdown();
    served.map(|()| ExitCode::SUCCESS)
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut s) => {
                s.recv().await;
            }
            Err(_) => std::future::pending::<()>().await,
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! {
        () = ctrl_c => {},
        () = terminate => {},
    }
    eprintln!("{}", json!({"shutdown": "draining"}));
}

fn check(config_path: &Path) -> Result<ExitCode, String> {
    let loaded = Loaded::read(config_path)?;
    let engine = build_engine(&loaded)?;
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

fn token(id: &str) -> Result<ExitCode, String> {
    if !core_memory_core::is_identifier(id) {
        return Err(format!("token id `{id}` is not a valid identifier"));
    }
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("cannot generate a token: {e}"))?;
    let token = format!("cm_{}", core_memory_contract::hash::to_hex(&bytes));
    let hash = Sha256Digest::of(token.as_bytes());
    println!("token: {token}");
    println!();
    println!(
        "Give the token to the caller. Add this to core-memory.toml; it stores only the hash:"
    );
    println!();
    println!("[[http.token]]");
    println!("id = \"{id}\"");
    println!("sha256 = \"{hash}\"");
    println!("scopes = [\"<scope>\"]");
    Ok(ExitCode::SUCCESS)
}

fn schema(name: Option<&str>) -> Result<ExitCode, String> {
    match name {
        None => {
            for c in Contract::ALL {
                println!("{}\t{}", c.name(), c.digest());
            }
            Ok(ExitCode::SUCCESS)
        }
        Some(name) => {
            let contract = Contract::from_name(name).ok_or_else(|| {
                let names: Vec<_> = Contract::ALL.iter().map(|c| c.name()).collect();
                format!(
                    "unknown contract `{name}`; choose one of {}",
                    names.join(", ")
                )
            })?;
            print_json(&contract.schema())
        }
    }
}

fn print_json(value: &serde_json::Value) -> Result<ExitCode, String> {
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    println!("{text}");
    Ok(ExitCode::SUCCESS)
}

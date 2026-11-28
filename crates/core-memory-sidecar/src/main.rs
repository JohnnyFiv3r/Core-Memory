//! `core-memory`: run the Core Memory engine over JSON Lines or HTTP.

use std::collections::BTreeMap;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use clap::{Parser, Subcommand};
use core_memory_contract::schema::Contract;
use core_memory_contract::{
    AdmissionOutcome, BusyCause, EventId, EventKey, FieldError, RECALL_DEFAULT_FOLLOW,
    RECALL_DEFAULT_LIMIT, RecallMatch, RecallRequest, RecallSearch, Record, RecordKind,
    RefusalReason, Sha256Digest,
};
use core_memory_core::store::{Change, Purpose, ScopeState, Store};
use core_memory_core::{
    Clock, EngineError, RecallQuery, ScopeGrants, Search, StoreError, SystemClock,
};
use core_memory_sidecar::config::Loaded;
use core_memory_sidecar::login;
use core_memory_sidecar::reading::{self, Reader};
use core_memory_sidecar::report::{self, Stop};
use core_memory_sidecar::runtime::{
    Runtime, build_engine, check_binding, check_embed_binding, model_roots,
    open_question_transport, open_store,
};
use core_memory_sidecar::{openapi, server};
use core_memory_source_jsonl::{JsonlSource, SourceItem};
use core_memory_store_postgres::{Enrollment, EraseOutcome, QueuedReads, Reread};
use serde_json::json;

/// Tries per event before an unavailable store is reported as busy.
const BUSY_ATTEMPTS: u32 = 5;
const BUSY_WAIT: Duration = Duration::from_millis(1_000);
/// Events read per round of `export`.
const EXPORT_PAGE: u32 = 500;
/// Events erased per transaction by `erase scope`. Each transaction holds
/// the scope's workspace, so it is kept short.
const ERASE_BATCH: u32 = 1_000;

/// The kinds of work a model does, in the order they are reported.
const PURPOSES: [Purpose; 5] = [
    Purpose::ChunkRead,
    Purpose::ToolRead,
    Purpose::Judge,
    Purpose::SecondRead,
    Purpose::Embed,
];

/// Exit status when no event is stored under the ID that was named. 2 is a
/// usage error.
const EXIT_NOT_FOUND: u8 = 3;
/// Exit status when the store could not be reached or could not take the
/// work now, and the same command will work later (`EX_TEMPFAIL`).
const EXIT_BUSY: u8 = 75;

#[derive(Parser)]
#[command(
    name = "core-memory",
    version,
    about = "Core Memory engine",
    after_help = "Exit status: 0 done; 1 failed; 2 wrong usage; 3 no event is stored under \
                  that ID; 75 the store was busy or unreachable, run the command again."
)]
struct Cli {
    /// The config file.
    #[arg(long, global = true, default_value = "core-memory.toml")]
    config: PathBuf,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Create or upgrade the Core Memory schema in the configured database.
    Migrate,
    /// Create a workspace: a set of scopes whose beads may be judged together.
    Workspace {
        #[command(subcommand)]
        command: WorkspaceCommand,
    },
    /// Enroll, list, pause, resume or revoke scopes, and say which models a
    /// scope's text may be sent to. A scope is one connection to one source.
    Scope {
        #[command(subcommand)]
        command: ScopeCommand,
    },
    /// Have a model read what is stored. Of each chunk: its type, and where
    /// its entities, actions and outcomes are, which needs a [reader] in
    /// the config. Of each tool call: what it did and what came of it,
    /// which needs a [tool_reader]. Of each new bead: how it relates to the
    /// recent beads of its workspace, which needs a [judge].
    Read {
        #[command(subcommand)]
        command: ReadCommand,
    },
    /// Admit SourceEvents from JSON Lines. Prints one admission outcome per
    /// input line, in order. Stops at the first event the store cannot take
    /// now, so no later event is stored ahead of it.
    Ingest {
        /// Input file, or `-` for standard input.
        #[arg(long, default_value = "-")]
        input: String,
    },
    /// Write what is stored as JSON Lines records, workspace by workspace,
    /// each in the order it was committed: events with their chunks, what
    /// a model read in them, what a judge said of how they relate, and
    /// erasures. With --scope, of what a judge said only what names no
    /// bead of another scope.
    Export {
        /// Only this workspace.
        #[arg(long, allow_hyphen_values = true)]
        workspace: Option<String>,
        /// Only this scope.
        #[arg(long, allow_hyphen_values = true)]
        scope: Option<String>,
        /// Only what was committed after this `seq`. Each workspace counts
        /// on its own, so this needs --workspace or --scope.
        #[arg(long)]
        after: Option<u64>,
    },
    /// Find what is stored that holds some words, and what a judge said of
    /// how it relates. Prints one JSON answer: the matches, best first, with
    /// their text verbatim; for a match that was replaced or withdrawn,
    /// what now stands in its place; the beads the judge's arrows lead to;
    /// and the arrows between them.
    Recall {
        /// The words to look for, or a question in plain language. Put
        /// `--` before a query that starts with `-`.
        #[arg(required = true, num_args = 1..)]
        query: Vec<String>,
        /// How the words match. `any`: a chunk that holds any of them
        /// matches, and those that hold more of them, or rarer ones, come
        /// first. `all`: every word is required, and the query is read as
        /// a web search reads it: "quoted words" stand together, `or`
        /// takes either and `-word` leaves out what holds it.
        #[arg(long = "match", value_enum, default_value_t = MatchArg::Any)]
        matching: MatchArg,
        /// Only this scope. Repeat it for more. Without it, every scope.
        #[arg(long = "scope", allow_hyphen_values = true)]
        scopes: Vec<String>,
        /// Most matches, from 1 to 50.
        #[arg(long, default_value_t = RECALL_DEFAULT_LIMIT)]
        limit: u32,
        /// Steps to take along the judge's arrows from each match, from 0
        /// to 2.
        #[arg(long, default_value_t = RECALL_DEFAULT_FOLLOW)]
        follow: u32,
        /// Also return matches that are no longer current, marked so.
        #[arg(long)]
        include_ended: bool,
        /// Search one way only: `words`, which sends the query nowhere, or
        /// `meaning`. Without it both are searched where an [embedder] is
        /// configured, and the answer says which were.
        #[arg(long, value_enum)]
        search: Option<SearchArg>,
    },
    /// Erase whole messages. Their text and metadata are removed for good;
    /// their IDs stay and refuse the same messages if they are offered again.
    Erase {
        /// Why, as a short code such as `user_request`. Kept in the journal.
        #[arg(long, allow_hyphen_values = true)]
        reason: String,
        /// Who asked. Kept in the journal.
        #[arg(long, default_value = "operator", allow_hyphen_values = true)]
        actor: String,
        #[command(subcommand)]
        target: EraseTarget,
    },
    /// Serve the engine over HTTP.
    Serve,
    /// Validate the config file and print its profiles. Does not connect.
    Check,
    /// Sign in to a ChatGPT Plus or Pro account, so the model calls of an
    /// `openai-chatgpt` binding use its plan instead of API credits. Asks
    /// first whether Core Memory may use the plan in the background, and
    /// signs nothing in without a yes.
    Login {
        #[arg(value_enum)]
        provider: SignInProvider,
        #[command(flatten)]
        options: LoginArgs,
    },
    /// Sign out of a ChatGPT account: end its session with OpenAI and clear
    /// its tokens here. The account's registration and this machine's host
    /// ID are kept for the next sign-in.
    Logout {
        #[arg(value_enum)]
        provider: SignInProvider,
        /// The credentials file. Default: the one the config's
        /// `openai-chatgpt` binding names, else the default file.
        #[arg(long)]
        credentials: Option<PathBuf>,
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

/// What can be signed in to.
#[derive(Clone, Copy, clap::ValueEnum)]
enum SignInProvider {
    /// A ChatGPT account, through Sign in with ChatGPT.
    Chatgpt,
}

#[derive(clap::Args)]
struct LoginArgs {
    /// The credentials file. Default: the one the config's
    /// `openai-chatgpt` binding names, else
    /// ~/.config/core-memory/chatgpt.json.
    #[arg(long)]
    credentials: Option<PathBuf>,
    /// Show the sign-in, without signing in. Sends nothing.
    #[arg(long, conflicts_with_all = ["models", "import", "consent", "allow_background_use"])]
    status: bool,
    /// List the models the account may use: what a binding's `model` may
    /// name.
    #[arg(long, conflicts_with_all = ["import", "consent", "allow_background_use"])]
    models: bool,
    /// Import a sign-in made on another machine, for a server with no
    /// browser: the credentials file copied here over SSH. It keeps this
    /// machine's own host ID.
    #[arg(long, value_name = "FILE", conflicts_with = "consent")]
    import: Option<PathBuf>,
    /// Ask OpenAI to show its consent screen again, as to let Core Memory
    /// use the plan after declining it.
    #[arg(long)]
    consent: bool,
    /// Agree to Core Memory using the plan in the background without being
    /// asked, for scripts. This is the same consent the question asks for.
    #[arg(long)]
    allow_background_use: bool,
    /// Print the sign-in URL without opening a browser.
    #[arg(long)]
    no_browser: bool,
}

#[derive(Subcommand)]
enum WorkspaceCommand {
    /// Create a workspace. Does nothing if it exists.
    Add { workspace: String },
}

#[derive(Subcommand)]
enum ScopeCommand {
    /// Enroll a scope in a workspace, creating the workspace if needed.
    /// Enrolling a revoked scope reconnects it.
    Add {
        scope: String,
        #[arg(long, allow_hyphen_values = true)]
        workspace: String,
    },
    /// List every scope with its state and event counts.
    List,
    /// Stop capture for a scope until it is resumed.
    Pause { scope: String },
    /// Resume a paused scope.
    Resume { scope: String },
    /// Withdraw a scope. Its events stay until they are erased.
    Revoke { scope: String },
    /// Let a scope's text be sent to a model. Consent is to a destination:
    /// the host, and port if any, of the model's endpoint.
    Allow {
        scope: String,
        #[command(flatten)]
        to: Destination,
    },
    /// Stop sending a scope's text to a model. A call already permitted is
    /// not recalled; no later one is permitted.
    Disallow {
        scope: String,
        #[command(flatten)]
        to: Destination,
    },
}

/// Where text would be sent, named one of two ways.
#[derive(clap::Args)]
#[group(required = true, multiple = false)]
struct Destination {
    /// A [[binding]] in the config. Its endpoint's host is the destination.
    #[arg(long, allow_hyphen_values = true)]
    binding: Option<String>,
    /// The destination itself, such as `api.anthropic.com` or
    /// `127.0.0.1:11434`.
    #[arg(long, allow_hyphen_values = true)]
    destination: Option<String>,
}

/// How a query's words match.
#[derive(Clone, Copy, clap::ValueEnum)]
enum MatchArg {
    Any,
    All,
}

/// One way to search.
#[derive(Clone, Copy, clap::ValueEnum)]
enum SearchArg {
    Words,
    Meaning,
}

#[derive(Subcommand)]
enum ReadCommand {
    /// Do everything that is due now, then stop. Prints one line per
    /// batch of chunks, per tool call and per read by the judge. Work
    /// whose scope has not allowed the model's destination is put off, not
    /// done. A bead that arrived a moment ago, or whose chunk a model is
    /// still to read, is judged by a later run.
    Run,
    /// Show how much is queued, being worked on and set aside as failed:
    /// one line each for chunks, tool calls, beads to judge and arrows to
    /// read a second time.
    Status,
    /// Queue a scope's chunks and tool calls that no model has read, and,
    /// when a [judge] is configured, its beads that no judge has read.
    /// Admission queues what it stores itself; this is for what was stored
    /// before a reader or a judge was configured. Prints one line for each
    /// kind.
    Queue {
        scope: String,
        /// Also queue what was only read with another model or prompt than
        /// the configured one's, or, for chunks, another vocabulary.
        #[arg(long)]
        reread: bool,
    },
    /// Put reads that were set aside as failed back in the queue.
    Retry {
        /// Only this scope's.
        #[arg(long, allow_hyphen_values = true)]
        scope: Option<String>,
    },
}

#[derive(Subcommand)]
enum EraseTarget {
    /// One event, by its ID.
    Event { event_id: String },
    /// Every revision of one source object, by its key. With --revision,
    /// that revision only.
    Key {
        #[arg(long, allow_hyphen_values = true)]
        scope: String,
        #[arg(long, allow_hyphen_values = true)]
        source_ns: String,
        #[arg(long, allow_hyphen_values = true)]
        external_id: String,
        #[arg(long, allow_hyphen_values = true)]
        revision: Option<String>,
    },
    /// Every live event of a scope. Pause or revoke the scope first if
    /// nothing more should be admitted to it.
    Scope { scope: String },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let config = cli.config.as_path();
    let result = match cli.command {
        Command::Migrate => migrate(config),
        Command::Workspace {
            command: WorkspaceCommand::Add { workspace },
        } => workspace_add(config, &workspace),
        Command::Scope { command } => scope(config, command),
        Command::Read { command } => read(config, command),
        Command::Ingest { input } => ingest(config, &input),
        Command::Export {
            workspace,
            scope,
            after,
        } => export(config, workspace, scope.as_deref(), after),
        Command::Recall {
            query,
            matching,
            scopes,
            limit,
            follow,
            include_ended,
            search,
        } => recall(
            config,
            RecallRequest {
                query: query.join(" "),
                matching: Some(match matching {
                    MatchArg::Any => RecallMatch::Any,
                    MatchArg::All => RecallMatch::All,
                }),
                scopes: (!scopes.is_empty()).then_some(scopes),
                limit: Some(limit),
                follow: Some(follow),
                include_ended,
                search: search.map(|search| match search {
                    SearchArg::Words => RecallSearch::Words,
                    SearchArg::Meaning => RecallSearch::Meaning,
                }),
            },
        ),
        Command::Erase {
            reason,
            actor,
            target,
        } => erase(config, &reason, &actor, target),
        Command::Serve => serve(config),
        Command::Check => check(config),
        Command::Login {
            provider: SignInProvider::Chatgpt,
            options,
        } => login(config, options),
        Command::Logout {
            provider: SignInProvider::Chatgpt,
            credentials,
        } => login::logout(config, credentials.as_deref())
            .and_then(report::print)
            .map(|()| ExitCode::SUCCESS),
        Command::Token { id } => token(&id),
        Command::Schema { name } => schema(name.as_deref()),
        Command::Openapi => print_json(&openapi::document()),
    };
    match result {
        Ok(code) => code,
        Err(stop) => {
            report::stopped(&stop);
            match stop {
                Stop::Failed(_) => ExitCode::FAILURE,
                Stop::StoreBusy(_) => ExitCode::from(EXIT_BUSY),
            }
        }
    }
}

fn migrate(config_path: &Path) -> Result<ExitCode, Stop> {
    let loaded = Loaded::read(config_path)?;
    let report = open_store(&loaded)?.migrate()?;
    report::print(json!({"schema_version": report.to, "was": report.from}))?;
    Ok(ExitCode::SUCCESS)
}

/// A store that is reachable and at this build's schema version. Commands
/// that govern need no engine and pin no profiles.
fn governing_store(config_path: &Path) -> Result<core_memory_store_postgres::PostgresStore, Stop> {
    let loaded = Loaded::read(config_path)?;
    let mut store = open_store(&loaded)?;
    store.check()?;
    Ok(store)
}

fn workspace_add(config_path: &Path, workspace: &str) -> Result<ExitCode, Stop> {
    let created = governing_store(config_path)?.create_workspace(workspace)?;
    report::print(json!({"workspace": workspace, "created": created}))?;
    Ok(ExitCode::SUCCESS)
}

/// The destination a command names: given outright, or as the host of a
/// configured binding's endpoint.
fn destination(loaded: &Loaded, to: Destination) -> Result<String, Stop> {
    match (to.destination, to.binding) {
        (Some(destination), _) => Ok(destination),
        (None, Some(id)) => loaded
            .config
            .bindings
            .iter()
            .find(|b| b.id == id)
            .ok_or_else(|| format!("no [[binding]] in the config has the id `{id}`"))?
            .destination()
            .map_err(|e| Stop::Failed(format!("configuration: {e}"))),
        (None, None) => Err("give --binding or --destination".into()),
    }
}

fn scope(config_path: &Path, command: ScopeCommand) -> Result<ExitCode, Stop> {
    let loaded = Loaded::read(config_path)?;
    let mut store = open_store(&loaded)?;
    store.check()?;
    let set =
        |store: &mut core_memory_store_postgres::PostgresStore, scope: &str, state: ScopeState| {
            let was = store.set_scope_state(scope, state)?;
            report::print(json!({"scope": scope, "state": state.as_str(), "was": was.as_str()}))
        };
    match command {
        ScopeCommand::Add { scope, workspace } => {
            store.create_workspace(&workspace)?;
            let result = match store.enroll_scope(&scope, &workspace)? {
                Enrollment::Enrolled => "enrolled",
                Enrollment::Reactivated => "reactivated",
                Enrollment::Unchanged => "unchanged",
            };
            report::print(json!({"scope": scope, "workspace": workspace, "result": result}))?;
        }
        ScopeCommand::List => {
            for s in store.scopes()? {
                report::print(json!({
                    "scope": s.scope,
                    "workspace": s.workspace,
                    "state": s.state.as_str(),
                    "live_events": s.live_events,
                    "erased_events": s.erased_events,
                    "allowed": s.destinations,
                }))?;
            }
        }
        ScopeCommand::Pause { scope } => set(&mut store, &scope, ScopeState::Paused)?,
        ScopeCommand::Resume { scope } => set(&mut store, &scope, ScopeState::Active)?,
        ScopeCommand::Revoke { scope } => set(&mut store, &scope, ScopeState::Revoked)?,
        ScopeCommand::Allow { scope, to } => {
            let destination = destination(&loaded, to)?;
            let changed = store.grant_destination(&scope, &destination)?;
            report::print(
                json!({"scope": scope, "destination": destination, "allowed": true, "changed": changed}),
            )?;
        }
        ScopeCommand::Disallow { scope, to } => {
            let destination = destination(&loaded, to)?;
            let changed = store.revoke_destination(&scope, &destination)?;
            report::print(
                json!({"scope": scope, "destination": destination, "allowed": false, "changed": changed}),
            )?;
        }
    }
    Ok(ExitCode::SUCCESS)
}

fn read(config_path: &Path, command: ReadCommand) -> Result<ExitCode, Stop> {
    let loaded = Loaded::read(config_path)?;
    let engine = Arc::new(build_engine(&loaded)?);
    let now = SystemClock.now().truncated_to_micros();
    match command {
        ReadCommand::Run => {
            let mut reader = Reader::open(&loaded, Arc::clone(&engine))?.ok_or(
                "add a [reader] section to the config to have a model read chunks, \
                 a [tool_reader] section to have one read tool calls, \
                 a [judge] section to have one judge how beads relate, \
                 or an [embedder] section to have one give chunks vectors",
            )?;
            report::log(reader.describe());
            let stdout = io::stdout();
            let mut out = stdout.lock();
            let mut written = Ok(());
            let totals = reader.drain(|round| {
                if written.is_ok() {
                    written = writeln!(out, "{}", reading::describe(round));
                }
            })?;
            written
                .and_then(|()| out.flush())
                .map_err(report::not_written)?;
            // What is left is due later: its scope has not allowed the
            // destination, the day's budget is spent, a call failed, or it
            // is a bead that waits to be judged.
            let mut store = open_store(&loaded)?;
            let mut left = serde_json::Map::new();
            for purpose in PURPOSES {
                let counts = store.work_counts(purpose)?;
                left.insert(
                    purpose.as_str().to_string(),
                    json!({"ready": counts.ready, "leased": counts.leased, "failed": counts.failed}),
                );
            }
            report::log(json!({
                "read": {
                    "rounds": totals.rounds,
                    "chunks": totals.chunks,
                    "tool_calls": totals.tool_calls,
                    "stored": totals.stored,
                    "judged": totals.judged,
                    "verdicts": totals.verdicts,
                    "embedded": totals.embedded,
                    "calls": totals.calls,
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "cache_read_tokens": totals.cache_read_tokens,
                    "cache_write_tokens": totals.cache_write_tokens,
                },
                "queued": left,
            }));
        }
        ReadCommand::Status => {
            let mut store = open_store(&loaded)?;
            store.check()?;
            for purpose in PURPOSES {
                let counts = store.work_counts(purpose)?;
                let problems: BTreeMap<String, u64> = counts.problems.into_iter().collect();
                report::print(json!({
                    "work": purpose.as_str(),
                    "ready": counts.ready,
                    "leased": counts.leased,
                    "failed": counts.failed,
                    "problems": problems,
                }))?;
            }
        }
        ReadCommand::Queue { scope, reread } => {
            // What was read by the configured readers stays read. A kind
            // with no reader configured has nothing to compare against,
            // and only what was never read of it is queued.
            let mut again = Reread::default();
            let judge = engine.judge();
            // A chunk with no vector of the embedder as it is configured
            // now is owed one, whatever else is asked.
            let embedder = engine.embedder();
            again.vectors = embedder.map(|embedder| &embedder.embedder_ref.binding_rev);
            if reread {
                if let (Some(reader), Some(vocabulary)) = (engine.reader(), engine.vocabulary()) {
                    again.chunks = Some((&reader.reader_ref, vocabulary.catalog()));
                }
                again.tool_calls = engine.tool_reader().map(|reader| &reader.reader_ref);
                if again.chunks.is_none() && again.tool_calls.is_none() && judge.is_none() {
                    return Err(
                        "--reread compares against what is configured; add a [reader], a \
                         [tool_reader] or a [judge] section"
                            .into(),
                    );
                }
            }
            let mut store = open_store(&loaded)?;
            store.check()?;
            let queued = |queued: QueuedReads| match queued {
                QueuedReads::Queued {
                    chunks,
                    tool_calls,
                    embeddings,
                } => Ok((chunks, tool_calls, embeddings)),
                QueuedReads::ScopeNotActive(None) => {
                    Err(Stop::Failed(format!("scope `{scope}` is not enrolled")))
                }
                QueuedReads::ScopeNotActive(Some(state)) => Err(Stop::Failed(format!(
                    "scope `{scope}` is {}; nothing is read for a scope that is not active",
                    state.as_str()
                ))),
            };
            let (chunks, tool_calls, embeddings) = queued(store.queue_reads(&scope, again, now)?)?;
            for (purpose, queued) in [
                (Purpose::ChunkRead, chunks),
                (Purpose::ToolRead, tool_calls),
            ] {
                report::print(json!({"scope": scope, "work": purpose.as_str(), "queued": queued}))?;
            }
            // Chunks are queued for an embedder only when one is
            // configured, as beads are for a judge.
            if embedder.is_some() {
                report::print(json!({
                    "scope": scope,
                    "work": Purpose::Embed.as_str(),
                    "queued": embeddings,
                }))?;
            }
            // Beads are queued for a judge only when one is configured.
            // Nothing else would ever take them, and a judge added later
            // should judge what its operator then asks it to. A tool
            // call's bead is queued once a model has said what the call
            // did, which is what a judge is shown of it.
            if let Some(judge) = judge {
                let again = reread.then_some(&judge.reader_ref);
                let (chunks, tool_calls, _) = queued(store.queue_judgments(&scope, again, now)?)?;
                report::print(json!({
                    "scope": scope,
                    "work": Purpose::Judge.as_str(),
                    "queued": chunks + tool_calls,
                }))?;
            }
        }
        ReadCommand::Retry { scope } => {
            let mut store = open_store(&loaded)?;
            store.check()?;
            let retried = store.retry_failed_reads(scope.as_deref(), now)?;
            report::print(json!({"scope": scope, "retried": retried}))?;
        }
    }
    Ok(ExitCode::SUCCESS)
}

fn ingest(config_path: &Path, input: &str) -> Result<ExitCode, Stop> {
    let mut rt = Runtime::open(config_path)?;
    let reader: Box<dyn BufRead> = if input == "-" {
        Box::new(BufReader::new(io::stdin().lock()))
    } else {
        let file = std::fs::File::open(input).map_err(|e| format!("{input}: {e}"))?;
        Box::new(BufReader::new(file))
    };
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut counts: BTreeMap<&'static str, u64> = BTreeMap::new();
    let mut stopped_at = None;

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
                    // The operator who holds the database may write any scope.
                    match rt.engine.admit(&mut rt.store, &ScopeGrants::All, &event) {
                        Ok(outcome) => break outcome,
                        Err(EngineError::Store(StoreError::Unavailable(cause))) => {
                            attempt += 1;
                            report::log(json!({"store_unavailable": cause, "line": line}));
                            if attempt >= BUSY_ATTEMPTS {
                                break AdmissionOutcome::Busy {
                                    retry_after_ms: BUSY_WAIT.as_millis() as u64,
                                    cause: BusyCause::Capacity,
                                };
                            }
                            std::thread::sleep(BUSY_WAIT);
                        }
                        Err(e) => return Err(e.into()),
                    }
                };
                (line, Some(event.external_id.clone()), outcome)
            }
        };
        *counts.entry(outcome_name(&outcome)).or_default() += 1;
        let busy = matches!(outcome, AdmissionOutcome::Busy { .. });
        let line_json = json!({"line": line, "external_id": external_id, "result": outcome});
        writeln!(out, "{line_json}").map_err(report::not_written)?;
        if busy {
            // Storing later lines first would put this one after them for
            // good. Running the command again resumes here: what was
            // stored comes back as replays.
            stopped_at = Some(line);
            break;
        }
    }
    out.flush().map_err(report::not_written)?;
    report::log(json!({"admitted": counts, "stopped_at_line": stopped_at}));
    Ok(if stopped_at.is_some() {
        ExitCode::from(EXIT_BUSY)
    } else {
        ExitCode::SUCCESS
    })
}

fn outcome_name(o: &AdmissionOutcome) -> &'static str {
    match o {
        AdmissionOutcome::Accepted { .. } => "accepted",
        AdmissionOutcome::Replay { .. } => "replay",
        AdmissionOutcome::IdentityConflict { .. } => "identity_conflict",
        AdmissionOutcome::CheckpointConflict { .. } => "checkpoint_conflict",
        AdmissionOutcome::Refused { .. } => "refused",
        AdmissionOutcome::Busy { .. } => "busy",
    }
}

fn export(
    config_path: &Path,
    workspace: Option<String>,
    scope: Option<&str>,
    after: Option<u64>,
) -> Result<ExitCode, Stop> {
    let mut store = governing_store(config_path)?;
    let of_scope = match scope {
        None => None,
        Some(scope) => Some(
            store
                .scope(scope)?
                .ok_or_else(|| format!("scope `{scope}` is not enrolled"))?
                .workspace,
        ),
    };
    let workspaces = match (workspace, of_scope) {
        (Some(named), Some(actual)) if named != actual => {
            return Err(Stop::Failed(format!(
                "scope `{}` is in workspace `{actual}`, not `{named}`",
                scope.unwrap_or_default()
            )));
        }
        (Some(one), _) | (None, Some(one)) => vec![one],
        (None, None) if after.is_some() => {
            return Err(
                "--after needs --workspace or --scope: each workspace counts on its own".into(),
            );
        }
        (None, None) => store.workspaces()?,
    };

    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut counts: BTreeMap<&'static str, u64> = BTreeMap::new();
    let mut last_seq = BTreeMap::new();
    for workspace in workspaces {
        let mut after = after.unwrap_or(0);
        loop {
            let page = store.export(&workspace, after, EXPORT_PAGE, scope)?;
            let Some(last) = page.last() else { break };
            after = last.seq();
            for change in page {
                let records: Vec<Record> = match change {
                    Change::Event(exported) => {
                        let exported = *exported;
                        let scope = exported.event.key.scope.clone();
                        std::iter::once(Record::event(exported.event))
                            .chain(
                                exported
                                    .chunks
                                    .into_iter()
                                    .map(|c| Record::chunk(scope.clone(), c)),
                            )
                            .collect()
                    }
                    Change::Read { scope, read } => vec![Record::chunk_read(scope, *read)],
                    Change::ToolOutcome { scope, outcome } => {
                        vec![Record::tool_outcome(scope, *outcome)]
                    }
                    Change::Bead(bead) => vec![Record::bead(*bead)],
                    Change::Edge(edge) => vec![Record::edge(*edge)],
                    Change::Verdict(verdict) => vec![Record::verdict(*verdict)],
                    Change::NameLink(link) => vec![Record::name_link(*link)],
                    Change::Erasure { scope, erasure } => vec![Record::erasure(scope, erasure)],
                };
                for record in records {
                    *counts.entry(record_kind(&record)).or_default() += 1;
                    let line = serde_json::to_string(&record).map_err(|e| e.to_string())?;
                    writeln!(out, "{line}").map_err(report::not_written)?;
                }
            }
        }
        last_seq.insert(workspace, after);
    }
    out.flush().map_err(report::not_written)?;
    // Passing a workspace's last seq back as --after resumes where this
    // export stopped.
    report::log(json!({"exported": {"records": counts, "last_seq": last_seq}}));
    Ok(ExitCode::SUCCESS)
}

fn recall(config_path: &Path, request: RecallRequest) -> Result<ExitCode, Stop> {
    // The command runs with the store's own login, which may read every
    // scope: what narrows it is what it is asked.
    let query = RecallQuery::from_request(&request, &ScopeGrants::All)
        .map_err(|refusal| Stop::Failed(refusal.to_string()))?;
    let loaded = Loaded::read(config_path)?;
    let engine = build_engine(&loaded)?;
    // A search by the words alone opens no way to the embedder, and needs
    // no key for it.
    let ask = match query.search {
        Search::Words => None,
        Search::Both | Search::Meaning => open_question_transport(&loaded, &engine)
            // Most often the embedder's key is not in this shell's
            // environment. The words need neither.
            .map_err(|stop| match stop {
                Stop::Failed(why) => Stop::Failed(format!(
                    "{why}. To search by the words alone, add --search words"
                )),
                busy => busy,
            })?,
    };
    let mut store = open_store(&loaded)?;
    store.check()?;
    if let Some(scopes) = &request.scopes {
        for scope in scopes {
            if store.scope(scope)?.is_none() {
                return Err(Stop::Failed(format!("scope `{scope}` is not enrolled")));
            }
        }
    }
    let answer = engine.recall(&mut store, ask.as_deref(), query)?;
    let line = serde_json::to_string(&answer).map_err(|e| e.to_string())?;
    report::print(line)?;
    // Counts only: the answer itself holds what sources said.
    report::log(json!({"recalled": {
        "found": answer.found,
        "meaning": answer.meaning,
        "beads": answer.beads.len(),
        "arrows": answer.arrows.len(),
    }}));
    Ok(ExitCode::SUCCESS)
}

fn record_kind(record: &Record) -> &'static str {
    match record.kind() {
        RecordKind::Event => "events",
        RecordKind::Chunk => "chunks",
        RecordKind::ChunkRead => "chunk_reads",
        RecordKind::ToolOutcome => "tool_outcomes",
        RecordKind::Bead => "beads",
        RecordKind::Edge => "edges",
        RecordKind::Verdict => "verdicts",
        RecordKind::NameLink => "name_links",
        RecordKind::Erasure => "erasures",
    }
}

fn erase(
    config_path: &Path,
    reason: &str,
    actor: &str,
    target: EraseTarget,
) -> Result<ExitCode, Stop> {
    let mut store = governing_store(config_path)?;
    let event_id = match target {
        EraseTarget::Scope { scope } => {
            // In batches, so no one transaction holds the workspace long.
            let mut erased = 0u64;
            loop {
                let batch = store.erase_scope(&scope, reason, actor, Some(ERASE_BATCH))?;
                if batch == 0 {
                    break;
                }
                erased += batch;
            }
            report::print(json!({"scope": scope, "erased_events": erased}))?;
            return Ok(ExitCode::SUCCESS);
        }
        EraseTarget::Key {
            scope,
            source_ns,
            external_id,
            revision: None,
        } => {
            let erased = store.erase_key(&scope, &source_ns, &external_id, reason, actor)?;
            report::print(json!({
                "scope": scope,
                "source_ns": source_ns,
                "external_id": external_id,
                "erased_events": erased,
            }))?;
            // None left to erase is what the command is for, whether they
            // were erased before or never stored: an erased event keeps no
            // key to tell the two apart by. The count says what happened.
            return Ok(ExitCode::SUCCESS);
        }
        EraseTarget::Event { event_id } => event_id
            .parse::<EventId>()
            .map_err(|e| format!("`{event_id}` is not an event ID: {e}"))?,
        EraseTarget::Key {
            scope,
            source_ns,
            external_id,
            revision,
        } => {
            let info = store
                .scope(&scope)?
                .ok_or_else(|| format!("scope `{scope}` is not enrolled"))?;
            EventId::derive(
                &info.id_key,
                &EventKey {
                    scope,
                    source_ns,
                    external_id,
                    revision,
                },
            )
        }
    };
    let outcome = store.erase_event(&event_id, reason, actor)?;
    let (name, code) = match outcome {
        EraseOutcome::Erased => ("erased", ExitCode::SUCCESS),
        EraseOutcome::AlreadyErased => ("already_erased", ExitCode::SUCCESS),
        // No event is stored under that ID.
        EraseOutcome::Unknown => ("unknown", ExitCode::from(EXIT_NOT_FOUND)),
    };
    report::print(json!({"event_id": event_id, "result": name}))?;
    Ok(code)
}

fn serve(config_path: &Path) -> Result<ExitCode, Stop> {
    let loaded = Loaded::read(config_path)?;
    let server::Prepared {
        router,
        listen,
        limits,
        reader,
    } = server::prepare(&loaded)?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("cannot start the async runtime: {e}"))?;

    // The readers and the judge work on a thread and a store connection of
    // their own. Dropping `stop` tells it to finish the round it is on.
    let (stop, stopped) = std::sync::mpsc::channel::<()>();
    let (ended, reader_ended) = std::sync::mpsc::channel::<()>();
    let reading = match reader {
        None => None,
        Some(reader) => {
            report::log(reader.describe());
            std::thread::Builder::new()
                .name("reader".into())
                .spawn(move || {
                    reader.run(&stopped);
                    drop(ended);
                })
                .map_err(|e| format!("cannot start the reader: {e}"))?;
            Some(reader_ended)
        }
    };

    // The router owns the store's connections, and closing one blocks the
    // thread that does it, which a thread running async tasks must not do.
    // This function keeps a router of its own until the runtime is gone,
    // so the last one is never dropped inside it.
    let served = runtime.block_on({
        let router = router.clone();
        async {
            let listener = tokio::net::TcpListener::bind(&listen)
                .await
                .map_err(|e| format!("cannot listen on {listen}: {e}"))?;
            let addr = listener.local_addr().map_err(|e| e.to_string())?;
            report::log(json!({"listening": addr.to_string()}));
            // Requests in flight get a bounded time to finish (ENG-03). An
            // admission that is cut off was either committed or not, and
            // its caller finds out which by sending it again.
            let shutdown = async move {
                shutdown_signal().await;
                drop(stop);
            };
            server::serve(listener, router, limits, shutdown)
                .await
                .map_err(|e| format!("server: {e}"))
        }
    });
    // A store call still running would otherwise keep the process alive
    // for as long as its statement timeout.
    runtime.shutdown_timeout(Duration::from_secs(1));
    drop(router);
    // A model call in flight gets the same grace as a request. Work that
    // is cut off stays reserved until its lease runs out, and is then done
    // again.
    if let Some(reader_ended) = reading {
        let _ = reader_ended.recv_timeout(limits.shutdown_grace);
    }
    served.map_err(Stop::Failed)?;
    Ok(ExitCode::SUCCESS)
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
}

fn check(config_path: &Path) -> Result<ExitCode, Stop> {
    let loaded = Loaded::read(config_path)?;
    let engine = build_engine(&loaded)?;
    if let Some(roots) = model_roots(&loaded)? {
        report::print(json!({"models": {"private_certificates": roots.private()}}))?;
    }
    for binding in &loaded.config.bindings {
        check_binding(binding)?;
        report::print(json!({
            "binding": binding.id,
            "provider": binding.provider,
            "model": binding.model,
            "destination": binding.destination().map_err(|e| e.to_string())?,
        }))?;
    }
    if let Some(reader) = engine.reader() {
        report::print(
            json!({"reader": {"binding": reader.binding.id, "destination": reader.destination}}),
        )?;
    }
    if let Some(reader) = engine.tool_reader() {
        report::print(json!({
            "tool_reader": {"binding": reader.binding.id, "destination": reader.destination}
        }))?;
    }
    if let Some(judge) = engine.judge() {
        report::print(json!({"judge": reading::describe_judge(judge)}))?;
    }
    if let Some(embedder) = engine.embedder() {
        check_embed_binding(&embedder.binding)?;
        report::print(json!({"embedder": {
            "binding": embedder.binding.id,
            "model": embedder.binding.model,
            "destination": embedder.destination,
            "dimensions": embedder.settings.dimensions,
            "revision": embedder.embedder_ref.binding_rev,
        }}))?;
    }
    for p in engine.registry().iter() {
        report::print(json!({
            "profile": p.def.id,
            "rev": p.def.rev,
            "hash": p.hash,
            "content": p.def.content,
            "chunker": p.chunker_ref,
        }))?;
    }
    Ok(ExitCode::SUCCESS)
}

fn login(config_path: &Path, args: LoginArgs) -> Result<ExitCode, Stop> {
    let options = login::LoginOptions {
        credentials: args.credentials,
        status: args.status,
        models: args.models,
        import: args.import,
        ask_again: args.consent,
        allow_background_use: args.allow_background_use,
        no_browser: args.no_browser,
    };
    let lines = login::login(
        config_path,
        &options,
        &mut io::stdin().lock(),
        &mut io::stderr().lock(),
    )?;
    for line in lines {
        report::print(line)?;
    }
    Ok(ExitCode::SUCCESS)
}

fn token(id: &str) -> Result<ExitCode, Stop> {
    if !core_memory_core::is_identifier(id) {
        // Not repeated: what is not an identifier could be anything.
        return Err("the token id is not a valid identifier".into());
    }
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("cannot generate a token: {e}"))?;
    let token = format!("cm_{}", core_memory_contract::hash::to_hex(&bytes));
    let hash = Sha256Digest::of(token.as_bytes());
    report::print(format_args!(
        "token: {token}\n\n\
         Give the token to the caller. Add this to core-memory.toml; it stores only the hash:\n\n\
         [[http.token]]\n\
         id = \"{id}\"\n\
         sha256 = \"{hash}\"\n\
         scopes = [\"<scope>\"]"
    ))?;
    Ok(ExitCode::SUCCESS)
}

fn schema(name: Option<&str>) -> Result<ExitCode, Stop> {
    match name {
        None => {
            for c in Contract::ALL {
                report::print(format_args!("{}\t{}", c.name(), c.digest()))?;
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

fn print_json(value: &serde_json::Value) -> Result<ExitCode, Stop> {
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    report::print(text)?;
    Ok(ExitCode::SUCCESS)
}

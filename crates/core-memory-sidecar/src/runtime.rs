//! Opening the engine, store and sinks a config describes, and delivering.

use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use core_memory_contract::Timestamp;
use core_memory_core::store::OperationalStore;
use core_memory_core::{
    ClassifyReport, DeliveryReport, Engine, ModelTransport, Sink, SystemClock, deliver_all,
};
use core_memory_model_anthropic::AnthropicTransport;
use core_memory_model_openai::{Dialect, OpenAiTransport};
use core_memory_sink_jsonl::JsonlSink;
use core_memory_store_sqlite::SqliteStore;
use serde_json::json;

use crate::config::{Loaded, SinkKind};

/// Outbox entries per sink write.
pub const DELIVERY_BATCH: usize = 512;

pub fn build_engine(loaded: &Loaded) -> Result<Engine, String> {
    Engine::new(
        loaded.engine_config(),
        loaded.required_sinks(),
        Arc::new(SystemClock),
    )
    .map_err(|e| format!("configuration: {e}"))
}

/// The transport for the configured classifier binding, or `None` when no
/// classifier is configured. A classifier that cannot be called (unknown
/// provider, missing credential, a setting the provider rejects) stops the
/// engine from starting; it is never silently turned off.
pub fn build_transport(loaded: &Loaded) -> Result<Option<Box<dyn ModelTransport>>, String> {
    let Some(classifier) = &loaded.config.classifier else {
        return Ok(None);
    };
    let binding = loaded
        .config
        .bindings
        .iter()
        .find(|b| b.id == classifier.binding)
        .ok_or_else(|| {
            format!(
                "classifier.binding `{}` is not a configured binding",
                classifier.binding
            )
        })?;
    let timeout = Duration::from_millis(binding.timeout_ms);
    let key = |default_env: &str| {
        let env = binding.api_key_env.as_deref().unwrap_or(default_env);
        std::env::var(env).ok().filter(|v| !v.trim().is_empty()).ok_or_else(|| {
            format!(
                "binding `{}` needs its credential in ${env}; the engine does not start with a classifier it cannot call",
                binding.id
            )
        })
    };
    match binding.provider.as_str() {
        core_memory_model_anthropic::PROVIDER => {
            AnthropicTransport::check_binding(binding)?;
            Ok(Some(Box::new(AnthropicTransport::new(
                key("ANTHROPIC_API_KEY")?,
                timeout,
            ))))
        }
        provider => match Dialect::for_provider(provider) {
            Some(dialect) => {
                OpenAiTransport::check_binding(binding)?;
                let api_key = match (dialect, &binding.api_key_env) {
                    (Dialect::OpenAi, _) => Some(key("OPENAI_API_KEY")?),
                    (Dialect::Compatible, Some(_)) => Some(key("")?),
                    (Dialect::Compatible, None) => None,
                };
                Ok(Some(Box::new(OpenAiTransport::new(
                    dialect, api_key, timeout,
                ))))
            }
            None => Err(format!(
                "binding `{}`: no adapter for provider `{provider}` (use anthropic, openai or openai-compatible)",
                binding.id
            )),
        },
    }
}

pub fn classify_line(r: &ClassifyReport) -> String {
    json!({
        "classify": {
            "calls": r.calls,
            "repaired": r.repaired,
            "decided": r.decided,
            "retried": r.retried,
            "released": r.released,
            "abandoned": r.abandoned,
            "split": r.split,
            "budget_exhausted": r.budget_exhausted,
        }
    })
    .to_string()
}

pub fn open_store(loaded: &Loaded) -> Result<SqliteStore, String> {
    let path = loaded.resolve(&loaded.config.store.path);
    SqliteStore::open(&path).map_err(|e| format!("{}: {e}", path.display()))
}

pub fn open_sinks(loaded: &Loaded) -> Result<Vec<JsonlSink>, String> {
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
    Ok(sinks)
}

/// Delivers to every sink until each is idle or asks to retry, then prunes
/// what the required sinks have taken. Returns one report per sink.
pub fn deliver_round(
    store: &mut SqliteStore,
    sinks: &mut [JsonlSink],
    required: &[String],
) -> Result<Vec<(String, DeliveryReport)>, String> {
    let mut reports = Vec::with_capacity(sinks.len());
    for sink in sinks.iter_mut() {
        let report = deliver_all(store, sink, DELIVERY_BATCH).map_err(|e| e.to_string())?;
        reports.push((sink.id().to_string(), report));
    }
    store.prune_outbox(required).map_err(|e| e.to_string())?;
    Ok(reports)
}

pub fn delivery_line(id: &str, r: &DeliveryReport) -> String {
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

/// Everything one command-line invocation needs.
#[derive(Debug)]
pub struct Runtime {
    pub loaded: Loaded,
    pub engine: Engine,
    pub store: SqliteStore,
    pub sinks: Vec<JsonlSink>,
    pub transport: Option<Box<dyn ModelTransport>>,
}

impl Runtime {
    pub fn open(config_path: &Path) -> Result<Self, String> {
        let loaded = Loaded::read(config_path)?;
        let engine = build_engine(&loaded)?;
        let mut store = open_store(&loaded)?;
        engine.pin_profiles(&mut store).map_err(|e| e.to_string())?;
        let sinks = open_sinks(&loaded)?;
        let transport = build_transport(&loaded)?;
        Ok(Self {
            loaded,
            engine,
            store,
            sinks,
            transport,
        })
    }

    /// Runs a classification pass over every due chunk, then, unless
    /// `keep_holds`, releases records still held as pending: a one-shot
    /// command has no later moment to wait for their deadline. Their chunks
    /// stay queued, so a later `classify` run delivers type decisions.
    pub fn classify(&mut self, keep_holds: bool) -> Result<(), String> {
        let Some(transport) = &self.transport else {
            return Ok(());
        };
        let mut report = self
            .engine
            .classify_due(&mut self.store, transport.as_ref(), usize::MAX)
            .map_err(|e| e.to_string())?;
        if !keep_holds {
            report.released += self
                .engine
                .release_holds(&mut self.store, Timestamp::max())
                .map_err(|e| e.to_string())?;
        }
        eprintln!("{}", classify_line(&report));
        Ok(())
    }

    /// Delivers and prints a report per sink to stderr. Returns false if a
    /// sink asked to retry later.
    pub fn deliver(&mut self) -> Result<bool, String> {
        let reports = deliver_round(
            &mut self.store,
            &mut self.sinks,
            self.engine.required_sinks(),
        )?;
        let mut complete = true;
        for (id, report) in &reports {
            complete &= report.retry_after_ms.is_none();
            eprintln!("{}", delivery_line(id, report));
        }
        Ok(complete)
    }
}

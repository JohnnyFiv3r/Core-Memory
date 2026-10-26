//! Opening the engine, store and sinks a config describes, and delivering.

use std::path::Path;
use std::sync::Arc;

use core_memory_core::store::OperationalStore;
use core_memory_core::{DeliveryReport, Engine, Sink, SystemClock, deliver_all};
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
}

impl Runtime {
    pub fn open(config_path: &Path) -> Result<Self, String> {
        let loaded = Loaded::read(config_path)?;
        let engine = build_engine(&loaded)?;
        let mut store = open_store(&loaded)?;
        engine.pin_profiles(&mut store).map_err(|e| e.to_string())?;
        let sinks = open_sinks(&loaded)?;
        Ok(Self {
            loaded,
            engine,
            store,
            sinks,
        })
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

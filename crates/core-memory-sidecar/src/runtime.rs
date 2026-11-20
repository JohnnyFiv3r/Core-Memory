//! Opening the engine and the store a config describes.

use std::sync::Arc;
use std::time::Duration;

use core_memory_core::{Engine, SystemClock};
use core_memory_store_postgres::{PostgresPool, PostgresStore};

use crate::config::Loaded;
use crate::report::Stop;

pub fn build_engine(loaded: &Loaded) -> Result<Engine, Stop> {
    Engine::new(loaded.engine_config(), Arc::new(SystemClock))
        .map_err(|e| Stop::Failed(format!("configuration: {e}")))
}

/// One store connection, not yet checked against the schema version.
pub fn open_store(loaded: &Loaded) -> Result<PostgresStore, Stop> {
    Ok(PostgresStore::new(&loaded.connect_config()?)?)
}

/// The pool `core-memory serve` admits through.
pub fn open_pool(loaded: &Loaded) -> Result<PostgresPool, Stop> {
    Ok(PostgresPool::new(
        &loaded.connect_config()?,
        loaded.config.store.pool_size,
    )?)
}

/// How long a request waits for a store connection before it is told to
/// retry.
pub const CHECKOUT_WAIT: Duration = Duration::from_secs(2);

/// Everything one command-line invocation needs: the engine and a store
/// that is reachable, at this build's schema version, with profiles pinned.
#[derive(Debug)]
pub struct Runtime {
    pub loaded: Loaded,
    pub engine: Engine,
    pub store: PostgresStore,
}

impl Runtime {
    pub fn open(config_path: &std::path::Path) -> Result<Self, Stop> {
        let loaded = Loaded::read(config_path)?;
        let engine = build_engine(&loaded)?;
        let mut store = open_store(&loaded)?;
        store.check()?;
        engine.pin_profiles(&mut store)?;
        Ok(Self {
            loaded,
            engine,
            store,
        })
    }
}

//! Opening the engine and the store a config describes.

use std::sync::Arc;
use std::time::Duration;

use core_memory_core::{BindingConfig, Engine, ModelTransport, SystemClock};
use core_memory_model_anthropic::AnthropicTransport;
use core_memory_model_openai::{Dialect, OpenAiTransport};
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

/// Checks that a binding names a provider this build can call and has the
/// settings that provider's API takes. Reads no key and sends nothing.
pub fn check_binding(binding: &BindingConfig) -> Result<(), Stop> {
    if binding.provider == core_memory_model_anthropic::PROVIDER {
        AnthropicTransport::check_binding(binding)?;
        if binding.api_key_env.is_none() {
            return Err(Stop::Failed(format!(
                "binding `{}`: set api_key_env to the environment variable that holds the \
                 Anthropic API key",
                binding.id
            )));
        }
        return Ok(());
    }
    if Dialect::for_provider(&binding.provider).is_none() {
        return Err(Stop::Failed(format!(
            "binding `{}`: provider must be `anthropic`, `openai` or `openai-compatible`",
            binding.id
        )));
    }
    Ok(OpenAiTransport::check_binding(binding)?)
}

/// The transport for a binding, with its key read from the environment.
/// Nothing is sent: a binding is first used when a chunk is read.
pub fn open_transport(binding: &BindingConfig) -> Result<Box<dyn ModelTransport>, Stop> {
    check_binding(binding)?;
    let timeout = Duration::from_millis(binding.timeout_ms);
    let key = match &binding.api_key_env {
        None => None,
        Some(name) => Some(
            std::env::var(name)
                .ok()
                .filter(|key| !key.trim().is_empty())
                .ok_or_else(|| {
                    format!(
                        "binding `{}`: ${name} must hold the provider's API key",
                        binding.id
                    )
                })?,
        ),
    };
    match (Dialect::for_provider(&binding.provider), key) {
        // A server on this machine may need no key.
        (Some(dialect), key) => Ok(Box::new(OpenAiTransport::new(dialect, key, timeout))),
        (None, Some(key)) => Ok(Box::new(AnthropicTransport::new(key, timeout))),
        (None, None) => Err(Stop::Failed(format!(
            "binding `{}` has no API key",
            binding.id
        ))),
    }
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

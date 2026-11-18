//! Opening the engine and the store a config describes.

use std::sync::Arc;
use std::time::Duration;

use core_memory_core::{BindingConfig, Engine, ModelTransport, SystemClock};
use core_memory_model_anthropic::AnthropicTransport;
use core_memory_model_http::{HttpClient, Roots};
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

/// The certificate authorities model calls trust: the public roots, and
/// those of `[models] ca_file` if it names a file. `None` for the public
/// roots alone.
pub fn model_roots(loaded: &Loaded) -> Result<Option<Roots>, Stop> {
    match &loaded.config.models.ca_file {
        None => Ok(None),
        // The path is not repeated. A setting's value is only as harmless
        // as what was put there.
        Some(path) => Roots::with_file(&loaded.resolve(path))
            .map(Some)
            .map_err(|e| Stop::Failed(format!("models.ca_file: {e}"))),
    }
}

/// The transport for a binding, with its key read from the environment.
/// Nothing is sent: a binding is first used when a chunk is read.
pub fn open_transport(
    binding: &BindingConfig,
    roots: Option<&Roots>,
) -> Result<Box<dyn ModelTransport>, Stop> {
    check_binding(binding)?;
    let client = HttpClient::trusting(Duration::from_millis(binding.timeout_ms), roots);
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
        (Some(dialect), key) => Ok(Box::new(OpenAiTransport::with_client(dialect, key, client))),
        (None, Some(key)) => Ok(Box::new(AnthropicTransport::with_client(key, client))),
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

#[cfg(test)]
mod tests {
    use super::*;

    /// A self-signed authority made for these tests and nothing else.
    const TEST_CA: &str = "-----BEGIN CERTIFICATE-----\n\
MIIBkzCCATmgAwIBAgIUKcFoolTQZa7nRPSS3yea08CYRI4wCgYIKoZIzj0EAwIw\n\
HjEcMBoGA1UEAwwTQ29yZSBNZW1vcnkgdGVzdCBDQTAgFw0yNjExMTgwMjI4NDZa\n\
GA8yMTI2MTAyNTAyMjg0NlowHjEcMBoGA1UEAwwTQ29yZSBNZW1vcnkgdGVzdCBD\n\
QTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABLoFXbY5bzMePwwxPcHxZzlWubd5\n\
7fc2ZCoFybv/ptDSPpQ7guWGuQVED4TAOh14angTnQk6cn1jbdLnQ4hqhUajUzBR\n\
MB0GA1UdDgQWBBTSMBhrWAIlfZmRdA+InApMcz00XTAfBgNVHSMEGDAWgBTSMBhr\n\
WAIlfZmRdA+InApMcz00XTAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0gA\n\
MEUCIG2/Fa/qx/j0xw0IwRQ0FuNnlIojyuDQ/NDOCqkSqUFDAiEA6YSf+7LbyOCq\n\
0iA2k66Cin8Y1D+1XBzZRQnrXQLskas=\n\
-----END CERTIFICATE-----
";

    fn loaded(dir: &std::path::Path, models: &str) -> Loaded {
        let path = dir.join("core-memory.toml");
        std::fs::write(&path, models).unwrap();
        Loaded::read(&path).unwrap()
    }

    #[test]
    fn model_calls_trust_a_private_authority_named_beside_the_config() {
        let dir = tempfile::tempdir().unwrap();
        assert!(model_roots(&loaded(dir.path(), "")).unwrap().is_none());
        std::fs::write(dir.path().join("proxy-ca.pem"), TEST_CA).unwrap();
        let roots = model_roots(&loaded(
            dir.path(),
            "[models]\nca_file = \"proxy-ca.pem\"\n",
        ))
        .unwrap()
        .unwrap();
        assert_eq!(roots.private(), 1);
    }

    #[test]
    fn a_missing_authority_file_stops_the_command_without_its_name() {
        let dir = tempfile::tempdir().unwrap();
        let err = model_roots(&loaded(
            dir.path(),
            "[models]\nca_file = \"secret-name.pem\"\n",
        ))
        .unwrap_err();
        let Stop::Failed(message) = err else {
            panic!("a missing file is a configuration error")
        };
        assert!(message.starts_with("models.ca_file: "), "{message}");
        assert!(!message.contains("secret-name"), "{message}");
    }
}

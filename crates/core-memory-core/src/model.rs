//! Model bindings and the injected model transport (MOD-01, MOD-02, MOD-07).
//!
//! The engine never talks to a provider directly. A binding names a pinned
//! model configuration; a [`ModelTransport`] adapter sends requests for it.
//! Provider SDK types never appear here.

use std::collections::VecDeque;
use std::fmt;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::error::ConfigError;
use crate::ident::is_identifier;

/// A pinned model configuration used for one role.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BindingConfig {
    pub id: String,
    /// Adapter name, for example `anthropic` or `openai-compatible`.
    pub provider: String,
    /// Provider endpoint. Part of the binding's identity.
    pub endpoint: String,
    /// The model to call. Name a pinned snapshot, not an alias: the name is
    /// part of the binding's revision, and an alias changes what it means
    /// without changing the revision.
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<String>,
    pub max_output_tokens: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seed: Option<u64>,
    /// Spend limits per workspace per UTC day (MOD-05). Without one, calls
    /// are limited only by the work there is.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub budget: Option<BudgetConfig>,
    /// Operational settings below are excluded from the binding revision,
    /// so tuning them never changes decisions' identity.
    #[serde(default = "default_timeout_ms")]
    pub timeout_ms: u64,
    /// Environment variable holding the provider credential.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key_env: Option<String>,
}

fn default_timeout_ms() -> u64 {
    60_000
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BudgetConfig {
    pub max_requests_per_day: u64,
    /// Counted as reserved tokens: what a call may use at most, whether or
    /// not it does.
    pub max_tokens_per_day: u64,
}

/// The decision-affecting part of a binding, hashed into its revision.
#[derive(Serialize)]
pub(crate) struct BindingIdentity<'a> {
    pub provider: &'a str,
    pub endpoint: &'a str,
    pub model: &'a str,
    pub temperature: Option<f64>,
    pub reasoning_effort: Option<&'a str>,
    pub max_output_tokens: u32,
    pub seed: Option<u64>,
}

impl BindingConfig {
    pub(crate) fn identity(&self) -> BindingIdentity<'_> {
        BindingIdentity {
            provider: &self.provider,
            endpoint: &self.endpoint,
            model: &self.model,
            temperature: self.temperature,
            reasoning_effort: self.reasoning_effort.as_deref(),
            max_output_tokens: self.max_output_tokens,
            seed: self.seed,
        }
    }

    pub fn validate(&self) -> Result<(), ConfigError> {
        if !is_identifier(&self.id) {
            return Err(ConfigError::new("a binding's id is not a valid identifier"));
        }
        let err = |m: &str| Err(ConfigError::new(format!("binding `{}`: {m}", self.id)));
        for (name, value) in [
            ("provider", &self.provider),
            ("endpoint", &self.endpoint),
            ("model", &self.model),
        ] {
            if value.trim().is_empty() {
                return err(&format!("{name} must not be empty"));
            }
        }
        if self.max_output_tokens == 0 {
            return err("max_output_tokens must be 1 or more");
        }
        if let Some(t) = self.temperature
            && !(0.0..=2.0).contains(&t)
        {
            return err("temperature must be between 0 and 2");
        }
        if self
            .budget
            .is_some_and(|b| b.max_requests_per_day == 0 || b.max_tokens_per_day == 0)
        {
            return err("budget limits must be 1 or more");
        }
        if self.timeout_ms == 0 {
            return err("timeout_ms must be 1 or more");
        }
        self.destination().map(|_| ())
    }

    /// Where a call through this binding sends text: the endpoint's host
    /// in lower case, with its port if it names one. This is what a scope
    /// consents to, so it is taken from the endpoint and never written by
    /// hand: a binding pointed somewhere else needs consent again.
    pub fn destination(&self) -> Result<String, ConfigError> {
        let err = |m: &str| {
            Err(ConfigError::new(format!(
                "binding `{}`: endpoint {m}",
                self.id
            )))
        };
        let Some((scheme, rest)) = self.endpoint.split_once("://") else {
            return err("must be a URL such as https://api.example.com/v1");
        };
        if !matches!(scheme, "http" | "https") {
            return err("must start with http:// or https://");
        }
        let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
        if authority.contains('@') {
            return err("must not carry a user name or password");
        }
        let host = authority.to_ascii_lowercase();
        let (name, port) = match host.rsplit_once(':') {
            // A bracketed IPv6 address has colons of its own.
            Some((name, port)) if !port.contains(']') => (name, Some(port)),
            _ => (host.as_str(), None),
        };
        let port_ok = port.is_none_or(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()));
        let name_ok = !name.is_empty()
            && name.len() <= 253
            && name.bytes().all(|b| {
                b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b':' | b'[' | b']')
            });
        if !port_ok || !name_ok {
            return err("must name a host, with a numeric port if any");
        }
        Ok(host)
    }
}

/// One model call: a system instruction, a user message and the JSON
/// Schema the reply must match.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ModelRequest {
    pub system: String,
    pub user: String,
    pub response_schema: serde_json::Value,
    pub max_output_tokens: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelResponse {
    pub text: String,
    pub usage: Usage,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: u64,
    pub output_tokens: u64,
}

/// Longest problem about a reply, in bytes, that is sent back to a model.
/// A problem quotes what the reply said, which may be long.
pub(crate) const MAX_PROBLEM_BYTES: usize = 300;

/// `problem`, cut at a character boundary to [`MAX_PROBLEM_BYTES`].
pub(crate) fn bounded(mut problem: String) -> String {
    if problem.len() > MAX_PROBLEM_BYTES {
        let mut end = MAX_PROBLEM_BYTES - '…'.len_utf8();
        while !problem.is_char_boundary(end) {
            end -= 1;
        }
        problem.truncate(end);
        problem.push('…');
    }
    problem
}

/// Reads a model's reply as JSON. A reply that gives a key twice could be
/// read two ways, and is refused as any such input is.
pub(crate) fn reply_json(reply: &str) -> Result<serde_json::Value, String> {
    let value =
        serde_json::from_str(reply).map_err(|e| bounded(format!("reply is not JSON: {e}")))?;
    core_memory_contract::json::check(reply, core_memory_contract::json::Integers::Any)
        .map_err(|e| bounded(format!("reply cannot be read one way: {e}")))?;
    Ok(value)
}

/// Why a call produced no response. The engine retries only what is safe
/// to retry, and records calls whose outcome is unknown as in doubt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransportError {
    /// The provider did not process the request, or said to retry.
    Transient {
        message: String,
        retry_after_ms: Option<u64>,
    },
    /// The request itself is unacceptable: credentials, model, settings.
    Permanent { message: String },
    /// The model declined this request's content, or the request or its
    /// reply did not fit. The same chunks in a smaller batch may succeed.
    Refused { message: String },
    /// The request may have been processed: a timeout after sending.
    InDoubt { message: String },
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TransportError::Transient { message, .. } => write!(f, "transient: {message}"),
            TransportError::Permanent { message } => write!(f, "permanent: {message}"),
            TransportError::Refused { message } => write!(f, "refused: {message}"),
            TransportError::InDoubt { message } => write!(f, "in doubt: {message}"),
        }
    }
}

/// Sends model requests for a binding. Implementations live in provider
/// adapter crates; tests use [`ScriptedTransport`] or [`FnTransport`].
/// An implementation makes at most one inference per call, with no hidden
/// retries or fallbacks.
pub trait ModelTransport: Send + Sync + fmt::Debug {
    fn send(
        &self,
        binding: &BindingConfig,
        request: &ModelRequest,
    ) -> Result<ModelResponse, TransportError>;
}

/// Answers from a queue of canned results, recording every request.
#[derive(Debug, Default)]
pub struct ScriptedTransport {
    script: Mutex<VecDeque<Result<ModelResponse, TransportError>>>,
    requests: Mutex<Vec<ModelRequest>>,
}

impl ScriptedTransport {
    pub fn new(script: Vec<Result<ModelResponse, TransportError>>) -> Self {
        Self {
            script: Mutex::new(script.into()),
            requests: Mutex::new(Vec::new()),
        }
    }

    pub fn requests(&self) -> Vec<ModelRequest> {
        self.requests.lock().map(|r| r.clone()).unwrap_or_default()
    }
}

impl ModelTransport for ScriptedTransport {
    fn send(
        &self,
        _: &BindingConfig,
        request: &ModelRequest,
    ) -> Result<ModelResponse, TransportError> {
        if let Ok(mut r) = self.requests.lock() {
            r.push(request.clone());
        }
        self.script
            .lock()
            .ok()
            .and_then(|mut s| s.pop_front())
            .unwrap_or_else(|| {
                Err(TransportError::Permanent {
                    message: "script exhausted".into(),
                })
            })
    }
}

/// Answers with a function of the request.
pub struct FnTransport<F>(pub F);

impl<F> fmt::Debug for FnTransport<F> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("FnTransport")
    }
}

impl<F> ModelTransport for FnTransport<F>
where
    F: Fn(&ModelRequest) -> Result<ModelResponse, TransportError> + Send + Sync,
{
    fn send(
        &self,
        _: &BindingConfig,
        request: &ModelRequest,
    ) -> Result<ModelResponse, TransportError> {
        (self.0)(request)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn binding(endpoint: &str) -> BindingConfig {
        BindingConfig {
            id: "small".into(),
            provider: "openai-compatible".into(),
            endpoint: endpoint.into(),
            model: "a-model".into(),
            temperature: None,
            reasoning_effort: None,
            max_output_tokens: 1024,
            seed: None,
            budget: None,
            timeout_ms: 60_000,
            api_key_env: None,
        }
    }

    #[test]
    fn the_destination_is_the_endpoints_host_and_port() {
        for (endpoint, destination) in [
            ("https://api.anthropic.com", "api.anthropic.com"),
            ("https://API.OpenAI.com/v1", "api.openai.com"),
            ("http://127.0.0.1:11434/v1", "127.0.0.1:11434"),
            ("http://localhost:8000", "localhost:8000"),
            ("http://[::1]:8000/v1", "[::1]:8000"),
            ("https://models.internal:443/v1?x=1", "models.internal:443"),
        ] {
            let b = binding(endpoint);
            assert_eq!(b.destination().unwrap(), destination, "{endpoint}");
            b.validate().unwrap();
        }
    }

    #[test]
    fn an_endpoint_that_names_no_host_is_refused() {
        for endpoint in [
            "api.anthropic.com",
            "ftp://example.com",
            "https://",
            "https://user:secret@example.com/v1",
            "https://example.com:port/v1",
            "https://exa mple.com",
        ] {
            let err = binding(endpoint).validate().unwrap_err().to_string();
            assert!(err.contains("endpoint"), "{endpoint}: {err}");
            assert!(!err.contains("secret"), "{err}");
        }
    }
}

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
    /// Pinned model snapshot ID. Adapters refuse aliases.
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<String>,
    pub max_output_tokens: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seed: Option<u64>,
    /// Scopes whose content may be sent through this binding (MOD-06).
    /// `["*"]` permits every scope.
    pub scopes: Vec<String>,
    /// Where the provider processes data, as the operator declares it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub region: Option<String>,
    /// The provider's data retention, as the operator declares it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data_retention: Option<String>,
    /// Spend limits per scope per UTC day (MOD-05).
    pub budget: BudgetConfig,
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
    /// Counted as reserved (maximum) tokens, never refunded.
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
        let err = |m: &str| Err(ConfigError::new(format!("binding `{}`: {m}", self.id)));
        if !is_identifier(&self.id) {
            return err("id is not a valid identifier");
        }
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
        if self.scopes.is_empty() || (self.scopes.iter().any(|s| s == "*") && self.scopes.len() > 1)
        {
            return err("scopes must list scopes, or be [\"*\"] alone");
        }
        if self.budget.max_requests_per_day == 0 || self.budget.max_tokens_per_day == 0 {
            return err("budget limits must be 1 or more");
        }
        Ok(())
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

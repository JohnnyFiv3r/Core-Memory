//! OpenAI-compatible chat completions transport: OpenAI, and servers that
//! speak its API (Ollama, vLLM, OpenRouter, LM Studio).
//!
//! One call is one `POST {endpoint}/chat/completions` with the request's
//! JSON Schema as `response_format`. Provider `openai` sends
//! `max_completion_tokens`; `openai-compatible` sends `max_tokens`, which
//! most compatible servers expect.

use std::fmt;
use std::time::Duration;

use core_memory_core::model::{
    BindingConfig, ModelRequest, ModelResponse, ModelTransport, TransportError, Usage,
};
use core_memory_model_http::{HttpClient, parse_body, status_error};
use serde_json::{Value, json};

/// Which flavour of the API a binding speaks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dialect {
    /// `provider = "openai"`.
    OpenAi,
    /// `provider = "openai-compatible"`.
    Compatible,
}

impl Dialect {
    pub fn for_provider(provider: &str) -> Option<Self> {
        match provider {
            "openai" => Some(Dialect::OpenAi),
            "openai-compatible" => Some(Dialect::Compatible),
            _ => None,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Dialect::OpenAi => "openai",
            Dialect::Compatible => "openai-compatible",
        }
    }
}

pub struct OpenAiTransport {
    client: HttpClient,
    dialect: Dialect,
    api_key: Option<String>,
}

impl fmt::Debug for OpenAiTransport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OpenAiTransport")
            .field("dialect", &self.dialect)
            .finish_non_exhaustive()
    }
}

impl OpenAiTransport {
    /// `api_key` may be absent for local servers that need none.
    pub fn new(dialect: Dialect, api_key: Option<String>, timeout: Duration) -> Self {
        Self {
            client: HttpClient::new(timeout),
            dialect,
            api_key,
        }
    }

    pub fn check_binding(binding: &BindingConfig) -> Result<(), String> {
        let id = &binding.id;
        if Dialect::for_provider(&binding.provider).is_none() {
            return Err(format!(
                "binding `{id}` is for provider `{}`, not `openai` or `openai-compatible`",
                binding.provider
            ));
        }
        if !(binding.endpoint.starts_with("https://") || binding.endpoint.starts_with("http://")) {
            return Err(format!("binding `{id}`: endpoint must be an http(s) URL"));
        }
        Ok(())
    }

    pub fn body(&self, binding: &BindingConfig, request: &ModelRequest) -> Value {
        let mut body = json!({
            "model": binding.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "core_memory_classification",
                    "schema": request.response_schema,
                    "strict": false,
                },
            },
        });
        let max_field = match self.dialect {
            Dialect::OpenAi => "max_completion_tokens",
            Dialect::Compatible => "max_tokens",
        };
        body[max_field] = json!(request.max_output_tokens);
        if let Some(t) = binding.temperature {
            body["temperature"] = json!(t);
        }
        if let Some(seed) = binding.seed {
            body["seed"] = json!(seed);
        }
        if let Some(effort) = &binding.reasoning_effort {
            body["reasoning_effort"] = json!(effort);
        }
        body
    }
}

impl ModelTransport for OpenAiTransport {
    fn send(
        &self,
        binding: &BindingConfig,
        request: &ModelRequest,
    ) -> Result<ModelResponse, TransportError> {
        Self::check_binding(binding).map_err(|message| TransportError::Permanent { message })?;
        let provider = self.dialect.name();
        let url = format!(
            "{}/chat/completions",
            binding.endpoint.trim_end_matches('/')
        );
        let auth = self.api_key.as_ref().map(|k| format!("Bearer {k}"));
        let headers: Vec<(&str, &str)> =
            auth.iter().map(|a| ("authorization", a.as_str())).collect();
        let reply = self
            .client
            .post_json(&url, &headers, &self.body(binding, request))?;
        if !(200..300).contains(&reply.status) {
            return Err(status_error(provider, &reply));
        }
        let body = parse_body(provider, &reply)?;
        let choice = &body["choices"][0];
        if !choice["message"]["refusal"].is_null() || choice["finish_reason"] == "content_filter" {
            return Err(TransportError::Refused {
                message: format!(
                    "{provider} declined the request: {}",
                    choice["message"]["refusal"]
                ),
            });
        }
        if choice["finish_reason"] == "length" {
            return Err(TransportError::Refused {
                message: "the reply was cut off (length); raise the binding's max_output_tokens"
                    .into(),
            });
        }
        let text = choice["message"]["content"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        let usage = &body["usage"];
        Ok(ModelResponse {
            text,
            usage: Usage {
                input_tokens: usage["prompt_tokens"].as_u64().unwrap_or(0),
                output_tokens: usage["completion_tokens"].as_u64().unwrap_or(0),
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core_memory_model_http::testing::serve_once;

    fn binding(provider: &str, endpoint: &str) -> BindingConfig {
        serde_json::from_value(json!({
            "id": "local",
            "provider": provider,
            "endpoint": endpoint,
            "model": "qwen3:8b",
            "temperature": 0.0,
            "seed": 7,
            "max_output_tokens": 1024,
            "scopes": ["*"],
            "budget": {"max_requests_per_day": 10, "max_tokens_per_day": 100000}
        }))
        .unwrap()
    }

    fn request() -> ModelRequest {
        ModelRequest {
            system: "classify".into(),
            user: "{\"chunks\": []}".into(),
            response_schema: json!({"type": "object"}),
            max_output_tokens: 1024,
        }
    }

    const OK: &str = r#"{"choices": [{"message": {"role": "assistant", "content": "{\"items\": []}", "refusal": null}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 50, "completion_tokens": 9}}"#;

    #[test]
    fn compatible_servers_get_max_tokens_and_no_auth_header() {
        let (url, server) = serve_once(200, &[], OK);
        let transport = OpenAiTransport::new(Dialect::Compatible, None, Duration::from_secs(5));
        let response = transport
            .send(
                &binding("openai-compatible", &format!("{url}/v1")),
                &request(),
            )
            .unwrap();
        assert_eq!(response.text, "{\"items\": []}");
        assert_eq!(
            (response.usage.input_tokens, response.usage.output_tokens),
            (50, 9)
        );
        let received = server.join().unwrap();
        assert_eq!(received.request_line, "POST /v1/chat/completions HTTP/1.1");
        assert_eq!(received.header("authorization"), None);
        let sent: Value = serde_json::from_str(&received.body).unwrap();
        assert_eq!(sent["max_tokens"], 1024);
        assert_eq!(sent["temperature"], 0.0);
        assert_eq!(sent["seed"], 7);
        assert_eq!(sent["messages"][0]["role"], "system");
        assert_eq!(sent["response_format"]["type"], "json_schema");
    }

    #[test]
    fn openai_gets_max_completion_tokens_and_a_bearer_token() {
        let (url, server) = serve_once(200, &[], OK);
        let transport =
            OpenAiTransport::new(Dialect::OpenAi, Some("sk-x".into()), Duration::from_secs(5));
        transport
            .send(&binding("openai", &url), &request())
            .unwrap();
        let received = server.join().unwrap();
        assert_eq!(received.header("authorization"), Some("Bearer sk-x"));
        let sent: Value = serde_json::from_str(&received.body).unwrap();
        assert_eq!(sent["max_completion_tokens"], 1024);
        assert!(sent.get("max_tokens").is_none());
    }

    #[test]
    fn refusals_rate_limits_and_truncation() {
        let transport =
            OpenAiTransport::new(Dialect::OpenAi, Some("k".into()), Duration::from_secs(5));
        let (url, _s) = serve_once(
            200,
            &[],
            r#"{"choices": [{"message": {"content": null, "refusal": "no"}, "finish_reason": "stop"}]}"#,
        );
        assert!(matches!(
            transport.send(&binding("openai", &url), &request()),
            Err(TransportError::Refused { .. })
        ));
        let (url, _s) = serve_once(429, &[("retry-after", "3")], "{}");
        assert!(matches!(
            transport.send(&binding("openai", &url), &request()),
            Err(TransportError::Transient {
                retry_after_ms: Some(3000),
                ..
            })
        ));
        let (url, _s) = serve_once(
            200,
            &[],
            r#"{"choices": [{"message": {"content": "{", "refusal": null}, "finish_reason": "length"}]}"#,
        );
        assert!(matches!(
            transport.send(&binding("openai", &url), &request()),
            Err(TransportError::Refused { .. })
        ));
    }
}

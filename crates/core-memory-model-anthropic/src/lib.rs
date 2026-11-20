//! Anthropic Messages API transport (raw HTTP; Rust has no official SDK).
//!
//! One call is one `POST /v1/messages` with API version `2026-10-21`. The
//! reply is constrained to the request's JSON Schema with
//! `output_config.format`, and the binding's `reasoning_effort` becomes
//! `output_config.effort`. Sampling parameters are not sent: that API
//! version rejects them. Server-side refusal fallbacks are not enabled,
//! because a decision must come from the model its binding pins (MOD-02).
//!
//! The API takes a subset of JSON Schema for a reply, and answers 400 to a
//! schema that bounds a number, the length of a string or the size of an
//! array. Those bounds are left out of what is sent, as the official SDKs
//! do. Nothing is lost by that: the engine checks every reply itself.

use std::fmt;
use std::time::Duration;

use core_memory_core::model::{
    BindingConfig, ModelRequest, ModelResponse, ModelTransport, TransportError, Usage,
};
use core_memory_model_http::{HttpClient, parse_body, status_error};
use serde_json::{Value, json};

/// The `anthropic-version` header this adapter speaks.
pub const API_VERSION: &str = "2026-10-21";
pub const DEFAULT_ENDPOINT: &str = "https://api.anthropic.com";
/// The provider name bindings use for this adapter.
pub const PROVIDER: &str = "anthropic";

const EFFORT_LEVELS: [&str; 5] = ["low", "medium", "high", "xhigh", "max"];

pub struct AnthropicTransport {
    client: HttpClient,
    api_key: String,
}

impl fmt::Debug for AnthropicTransport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AnthropicTransport").finish_non_exhaustive()
    }
}

impl AnthropicTransport {
    pub fn new(api_key: impl Into<String>, timeout: Duration) -> Self {
        Self::with_client(api_key, HttpClient::new(timeout))
    }

    /// A transport that sends through `client`, such as one that trusts a
    /// private certificate authority.
    pub fn with_client(api_key: impl Into<String>, client: HttpClient) -> Self {
        Self {
            client,
            api_key: api_key.into(),
        }
    }

    /// Checks that `binding` can be sent through this adapter. Call it at
    /// startup so a bad binding stops the engine instead of failing later.
    pub fn check_binding(binding: &BindingConfig) -> Result<(), String> {
        let id = &binding.id;
        if binding.provider != PROVIDER {
            return Err(format!(
                "binding `{id}` is for provider `{}`, not `{PROVIDER}`",
                binding.provider
            ));
        }
        if binding.temperature.is_some() {
            return Err(format!(
                "binding `{id}`: the Messages API ({API_VERSION}) rejects temperature; remove it"
            ));
        }
        if binding.seed.is_some() {
            return Err(format!(
                "binding `{id}`: the Messages API has no seed parameter; remove it"
            ));
        }
        if let Some(effort) = &binding.reasoning_effort
            && !EFFORT_LEVELS.contains(&effort.as_str())
        {
            return Err(format!(
                "binding `{id}`: reasoning_effort must be one of {}",
                EFFORT_LEVELS.join(", ")
            ));
        }
        if !(binding.endpoint.starts_with("https://") || binding.endpoint.starts_with("http://")) {
            return Err(format!("binding `{id}`: endpoint must be an http(s) URL"));
        }
        Ok(())
    }

    /// The request body for one call.
    pub fn body(binding: &BindingConfig, request: &ModelRequest) -> Value {
        let mut output_config = json!({
            "format": {"type": "json_schema", "schema": sendable(&request.response_schema)},
        });
        if let Some(effort) = &binding.reasoning_effort {
            output_config["effort"] = json!(effort);
        }
        json!({
            "model": binding.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": output_config,
        })
    }
}

/// Keywords the API's structured outputs do not take: bounds on a number,
/// on the length of a string and on the size of an array.
const UNSENDABLE: [&str; 10] = [
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
];

/// `schema` without the keywords the API refuses. Only keywords are
/// removed: a property that happens to be named `maximum` stays.
fn sendable(schema: &Value) -> Value {
    let Value::Object(fields) = schema else {
        return schema.clone();
    };
    let mut kept = serde_json::Map::new();
    for (keyword, value) in fields {
        if UNSENDABLE.contains(&keyword.as_str()) {
            continue;
        }
        let value = match (keyword.as_str(), value) {
            // Maps from a name to a schema. The names are not keywords.
            ("properties" | "$defs" | "definitions", Value::Object(named)) => Value::Object(
                named
                    .iter()
                    .map(|(name, schema)| (name.clone(), sendable(schema)))
                    .collect(),
            ),
            // One schema, or a list of schemas.
            ("items" | "not", schema) => sendable(schema),
            ("anyOf" | "allOf" | "oneOf" | "prefixItems", Value::Array(schemas)) => {
                Value::Array(schemas.iter().map(sendable).collect())
            }
            // Values, not schemas: an enum may list any JSON value.
            (_, value) => value.clone(),
        };
        kept.insert(keyword.clone(), value);
    }
    Value::Object(kept)
}

/// The error reply for a prompt longer than the model's context window: a
/// 400 `invalid_request_error` whose message starts "prompt is too long".
/// A smaller batch may fit. Requests over the size limit come back as 413,
/// which the shared client already treats the same way; every other error
/// keeps its status mapping.
fn too_long(status: u16, body: &Value) -> bool {
    let error = &body["error"];
    status == 400
        && error["type"] == "invalid_request_error"
        && error["message"]
            .as_str()
            .is_some_and(|m| m.starts_with("prompt is too long"))
}

impl ModelTransport for AnthropicTransport {
    fn send(
        &self,
        binding: &BindingConfig,
        request: &ModelRequest,
    ) -> Result<ModelResponse, TransportError> {
        Self::check_binding(binding).map_err(|message| TransportError::Permanent { message })?;
        let url = format!("{}/v1/messages", binding.endpoint.trim_end_matches('/'));
        let reply = self.client.post_json(
            &url,
            &[
                ("x-api-key", &self.api_key),
                ("anthropic-version", API_VERSION),
            ],
            &Self::body(binding, request),
        )?;
        if !(200..300).contains(&reply.status) {
            return Err(status_error(PROVIDER, &reply, too_long));
        }
        let body = parse_body(PROVIDER, &reply)?;
        match body["stop_reason"].as_str() {
            Some("refusal") => {
                return Err(TransportError::Refused {
                    message: format!(
                        "anthropic declined the request (refusal, category {})",
                        body["stop_details"]["category"]
                    ),
                });
            }
            Some("max_tokens" | "model_context_window_exceeded") => {
                return Err(TransportError::Refused {
                    message: format!(
                        "the reply was cut off ({}); raise the binding's max_output_tokens",
                        body["stop_reason"]
                    ),
                });
            }
            _ => {}
        }
        // Thinking blocks come first; the answer is in the text blocks.
        let text: String = body["content"]
            .as_array()
            .map(|blocks| {
                blocks
                    .iter()
                    .filter(|b| b["type"] == "text")
                    .filter_map(|b| b["text"].as_str())
                    .collect()
            })
            .unwrap_or_default();
        let usage = &body["usage"];
        let count = |field: &str| usage[field].as_u64().unwrap_or(0);
        Ok(ModelResponse {
            text,
            usage: Usage {
                input_tokens: count("input_tokens")
                    + count("cache_creation_input_tokens")
                    + count("cache_read_input_tokens"),
                output_tokens: count("output_tokens"),
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core_memory_model_http::testing::serve_once;

    fn binding(endpoint: &str) -> BindingConfig {
        serde_json::from_value(json!({
            "id": "classifier",
            "provider": "anthropic",
            "endpoint": endpoint,
            "model": "claude-opus-5-5",
            "reasoning_effort": "low",
            "max_output_tokens": 4096
        }))
        .unwrap()
    }

    fn request() -> ModelRequest {
        ModelRequest {
            system: "classify".into(),
            user: "{\"chunks\": []}".into(),
            response_schema: json!({"type": "object", "additionalProperties": false, "properties": {}}),
            max_output_tokens: 4096,
        }
    }

    #[test]
    fn sends_a_messages_request_and_reads_the_text_block() {
        let (url, server) = serve_once(
            200,
            &[("content-type", "application/json")],
            r#"{"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "{\"items\": []}"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 7}}"#,
        );
        let transport = AnthropicTransport::new("sk-test", Duration::from_secs(5));
        let response = transport.send(&binding(&url), &request()).unwrap();
        assert_eq!(response.text, "{\"items\": []}");
        assert_eq!(response.usage.input_tokens, 120);
        assert_eq!(response.usage.output_tokens, 7);

        let received = server.join().unwrap();
        assert_eq!(received.request_line, "POST /v1/messages HTTP/1.1");
        assert_eq!(received.header("x-api-key"), Some("sk-test"));
        assert_eq!(received.header("anthropic-version"), Some(API_VERSION));
        let sent: Value = serde_json::from_str(&received.body).unwrap();
        assert_eq!(sent["model"], "claude-opus-5-5");
        assert_eq!(sent["output_config"]["effort"], "low");
        assert_eq!(sent["output_config"]["format"]["type"], "json_schema");
        assert_eq!(sent["messages"][0]["role"], "user");
        assert!(sent.get("temperature").is_none());
        assert!(sent.get("fallbacks").is_none());
    }

    #[test]
    fn bounds_the_api_does_not_take_are_left_out_of_the_schema() {
        let mut request = request();
        request.response_schema = json!({
            "type": "object",
            "additionalProperties": false,
            "required": ["action", "items"],
            "properties": {
                "action": {"type": "string", "minLength": 1, "maxLength": 120},
                // A property with a keyword's name is a property.
                "maximum": {"type": "integer", "minimum": 1, "maximum": 9},
                "status": {"type": "string", "enum": ["minimum", "maxLength"]},
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "anyOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "object", "additionalProperties": false,
                             "properties": {"n": {"type": "number", "multipleOf": 2}}},
                        ],
                    },
                },
            },
        });
        let sent = AnthropicTransport::body(&binding("https://api.anthropic.com"), &request);
        assert_eq!(
            sent["output_config"]["format"]["schema"],
            json!({
                "type": "object",
                "additionalProperties": false,
                "required": ["action", "items"],
                "properties": {
                    "action": {"type": "string"},
                    "maximum": {"type": "integer"},
                    "status": {"type": "string", "enum": ["minimum", "maxLength"]},
                    "items": {
                        "type": "array",
                        "items": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "object", "additionalProperties": false,
                                 "properties": {"n": {"type": "number"}}},
                            ],
                        },
                    },
                },
            })
        );
        // The request itself is not changed: its schema is what the
        // binding's revision was computed from.
        assert_eq!(
            request.response_schema["properties"]["action"]["maxLength"],
            120
        );
    }

    #[test]
    fn overload_is_transient_with_retry_after() {
        let (url, _server) = serve_once(529, &[("retry-after", "7")], r#"{"type": "error"}"#);
        let transport = AnthropicTransport::new("sk-test", Duration::from_secs(5));
        let err = transport.send(&binding(&url), &request()).unwrap_err();
        assert!(
            matches!(
                err,
                TransportError::Transient {
                    retry_after_ms: Some(7000),
                    ..
                }
            ),
            "{err:?}"
        );
    }

    #[test]
    fn refusals_and_truncation_are_not_types() {
        let (url, _s) = serve_once(
            200,
            &[],
            r#"{"content": [], "stop_reason": "refusal", "stop_details": {"category": "cyber"}}"#,
        );
        let transport = AnthropicTransport::new("sk-test", Duration::from_secs(5));
        let err = transport.send(&binding(&url), &request()).unwrap_err();
        assert!(
            matches!(&err, TransportError::Refused { message } if message.contains("declined")),
            "{err:?}"
        );

        let (url, _s) = serve_once(
            200,
            &[],
            r#"{"content": [{"type": "text", "text": "{\"it"}], "stop_reason": "max_tokens"}"#,
        );
        let err = transport.send(&binding(&url), &request()).unwrap_err();
        assert!(
            matches!(&err, TransportError::Refused { message } if message.contains("max_output_tokens"))
        );
    }

    #[test]
    fn a_prompt_too_long_for_the_context_window_is_refused() {
        let transport = AnthropicTransport::new("sk-test", Duration::from_secs(5));
        let (url, _s) = serve_once(
            400,
            &[],
            r#"{"type": "error", "error": {"type": "invalid_request_error",
                "message": "prompt is too long: 215000 tokens > 200000 maximum"},
                "request_id": "req_1"}"#,
        );
        let err = transport.send(&binding(&url), &request()).unwrap_err();
        assert!(
            matches!(&err, TransportError::Refused { message } if message.contains("prompt is too long")),
            "{err:?}"
        );

        // Any other invalid request is not something a smaller batch fixes.
        let (url, _s) = serve_once(
            400,
            &[],
            r#"{"type": "error", "error": {"type": "invalid_request_error",
                "message": "max_tokens: Field required"}}"#,
        );
        let err = transport.send(&binding(&url), &request()).unwrap_err();
        assert!(matches!(err, TransportError::Permanent { .. }), "{err:?}");
    }

    #[test]
    fn bindings_with_sampling_parameters_are_refused() {
        let mut b = binding("https://api.anthropic.com");
        assert!(AnthropicTransport::check_binding(&b).is_ok());
        b.temperature = Some(0.0);
        assert!(
            AnthropicTransport::check_binding(&b)
                .unwrap_err()
                .contains("temperature")
        );
        let mut b = binding("https://api.anthropic.com");
        b.reasoning_effort = Some("extreme".into());
        assert!(AnthropicTransport::check_binding(&b).is_err());
    }

    #[test]
    fn debug_output_hides_the_key() {
        let transport = AnthropicTransport::new("sk-secret", Duration::from_secs(5));
        assert!(!format!("{transport:?}").contains("sk-secret"));
    }
}

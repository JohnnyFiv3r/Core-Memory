//! One Responses API call on the ChatGPT plan: the request body, the event
//! stream it is answered with, and what each failure means.
//!
//! The plan's route takes `store: false` and `stream: true` on every
//! request, the system prompt as `instructions`, and `input` as a list. It
//! refuses `max_output_tokens`, `temperature` and a dozen other fields, so
//! none is sent. A call succeeded only if its stream reached
//! `response.completed`.

use core_memory_core::model::{BindingConfig, ModelRequest, ModelResponse, TransportError, Usage};
use core_memory_model_http::HttpReply;
use serde_json::{Value, json};

use crate::{USAGE_URL, plain_code};

/// Most of a provider's own words kept in an error message.
const DETAIL_CHARS: usize = 300;

/// The request body for `request` through `binding`.
pub(crate) fn body(binding: &BindingConfig, request: &ModelRequest) -> Value {
    let mut body = json!({
        "model": binding.model,
        "instructions": request.system,
        "input": [{"role": "user", "content": request.user}],
        "store": false,
        "stream": true,
    });
    if request.enforce_schema {
        body["text"] = json!({
            "format": {
                "type": "json_schema",
                "name": "core_memory_classification",
                "schema": request.response_schema,
                "strict": false,
            }
        });
    }
    if let Some(effort) = &binding.reasoning_effort {
        body["reasoning"] = json!({"effort": effort});
    }
    body
}

/// What a reply's error says: its code, the field it names, and a little
/// of its own words. A reply turned away before the Responses API saw the
/// request may carry only `{"detail": "..."}`.
#[derive(Debug, Default)]
struct ErrorBody<'a> {
    code: Option<&'a str>,
    param: Option<&'a str>,
    detail: Option<String>,
}

fn error_body(error: &Value) -> ErrorBody<'_> {
    let words = error["message"]
        .as_str()
        .or_else(|| error["detail"].as_str())
        .map(|m| m.chars().take(DETAIL_CHARS).collect());
    ErrorBody {
        code: plain_code(&error["code"]),
        param: plain_code(&error["param"]),
        detail: words,
    }
}

fn describe(status: Option<u16>, error: &ErrorBody<'_>, request_id: Option<&str>) -> String {
    let mut message = String::from("ChatGPT plan");
    if let Some(status) = status {
        message.push_str(&format!(" returned {status}"));
    }
    if let Some(code) = error.code {
        message.push_str(&format!(", code {code}"));
    }
    if let Some(param) = error.param {
        message.push_str(&format!(", param {param}"));
    }
    if let Some(id) = request_id {
        message.push_str(&format!(", request {id}"));
    }
    if let Some(detail) = &error.detail {
        message.push_str(&format!(": {detail}"));
    }
    message
}

const LIMIT_HELP: &str = "the ChatGPT plan's usage limit for Core Memory is reached; new calls \
     wait. See and manage it at ";

/// The error for a reply whose status is not a success.
pub(crate) fn status_error(reply: &HttpReply) -> TransportError {
    let parsed: Value = serde_json::from_str(&reply.body).unwrap_or(Value::Null);
    let error = if parsed["error"].is_object() {
        error_body(&parsed["error"])
    } else {
        error_body(&parsed)
    };
    let message = describe(Some(reply.status), &error, reply.request_id.as_deref());
    match (reply.status, error.code) {
        // The signed identity or its permission was not accepted.
        (401, _) => TransportError::SignInRequired {
            message: format!(
                "{message}. Check the account with `core-memory login chatgpt --status`, \
                 and sign in again if it persists"
            ),
        },
        (429, Some("subscription_sharing_usage_limit_exceeded")) => TransportError::LimitReached {
            message: format!("{message}. {LIMIT_HELP}{USAGE_URL}"),
            started: false,
        },
        (408 | 409 | 429, _) | (500..=599, _) => TransportError::Transient {
            message,
            retry_after_ms: reply.retry_after_ms,
        },
        (400, Some("context_length_exceeded")) | (413, _) => TransportError::DidNotFit { message },
        // Eligibility, region, route and grant refusals, and fields the
        // plan's route does not take: the same request fails alike.
        _ => TransportError::Permanent { message },
    }
}

/// The error for a stream that stopped on `response.failed` or `error`,
/// after the request was taken.
fn failed(error: &Value, request_id: Option<&str>) -> TransportError {
    let error = error_body(error);
    let message = describe(None, &error, request_id);
    match error.code {
        Some("subscription_sharing_usage_limit_exceeded") => TransportError::LimitReached {
            message: format!("{message}. {LIMIT_HELP}{USAGE_URL}"),
            started: true,
        },
        Some("context_length_exceeded") => TransportError::DidNotFit { message },
        // Whatever else stopped it, the model may have read the request.
        _ => TransportError::InDoubt {
            message: format!("the reply stopped: {message}"),
        },
    }
}

/// The events of a `text/event-stream` body, as their JSON data.
fn events(body: &str) -> Result<Vec<Value>, TransportError> {
    let mut events = Vec::new();
    let mut data = String::new();
    let mut flush = |data: &mut String| -> Result<(), TransportError> {
        if !data.is_empty() {
            let event = serde_json::from_str(data).map_err(|_| TransportError::InDoubt {
                message: "ChatGPT plan streamed an event that is not JSON".into(),
            })?;
            events.push(event);
            data.clear();
        }
        Ok(())
    };
    for line in body.lines() {
        let line = line.strip_suffix('\r').unwrap_or(line);
        if line.is_empty() {
            flush(&mut data)?;
        } else if let Some(value) = line.strip_prefix("data:") {
            if !data.is_empty() {
                data.push('\n');
            }
            data.push_str(value.strip_prefix(' ').unwrap_or(value));
        }
        // `event:`, `id:`, `retry:` and comments add nothing the data's
        // own `type` does not say.
    }
    flush(&mut data)?;
    Ok(events)
}

/// The text and refusal of a completed response's output.
fn output(response: &Value) -> (String, Option<String>) {
    let mut text = String::new();
    let mut refusal = None;
    for item in response["output"].as_array().into_iter().flatten() {
        if item["type"] != "message" {
            continue;
        }
        for part in item["content"].as_array().into_iter().flatten() {
            match part["type"].as_str() {
                Some("output_text") => text.push_str(part["text"].as_str().unwrap_or_default()),
                Some("refusal") => {
                    refusal = Some(part["refusal"].as_str().unwrap_or_default().to_string());
                }
                _ => {}
            }
        }
    }
    (text, refusal)
}

/// The reply a successful status streamed: its text, if the stream reached
/// `response.completed`, or why not.
pub(crate) fn read_stream(reply: &HttpReply) -> Result<ModelResponse, TransportError> {
    let request_id = reply.request_id.as_deref();
    let mut streamed = String::new();
    let mut refused = false;
    for event in events(&reply.body)? {
        match event["type"].as_str().unwrap_or_default() {
            "response.output_text.delta" => {
                streamed.push_str(event["delta"].as_str().unwrap_or_default());
            }
            "response.refusal.delta" | "response.refusal.done" => refused = true,
            "response.completed" => {
                let response = &event["response"];
                let (mut text, refusal) = output(response);
                if refused || refusal.is_some() {
                    return Err(TransportError::Refused {
                        message: "the model declined the request".into(),
                    });
                }
                if text.is_empty() {
                    text = streamed;
                }
                let usage = &response["usage"];
                return Ok(ModelResponse {
                    text,
                    usage: Usage {
                        input_tokens: usage["input_tokens"].as_u64().unwrap_or(0),
                        output_tokens: usage["output_tokens"].as_u64().unwrap_or(0),
                        cache_read_tokens: usage["input_tokens_details"]["cached_tokens"]
                            .as_u64()
                            .unwrap_or(0),
                        cache_write_tokens: 0,
                    },
                });
            }
            "response.incomplete" => {
                let reason = plain_code(&event["response"]["incomplete_details"]["reason"]);
                return Err(match reason {
                    Some("content_filter") => TransportError::Refused {
                        message: "the model's reply was filtered (content_filter)".into(),
                    },
                    Some("max_output_tokens") => TransportError::DidNotFit {
                        message: "the reply was cut off at the model's output limit".into(),
                    },
                    other => TransportError::InDoubt {
                        message: format!(
                            "the reply was incomplete ({})",
                            other.unwrap_or("no reason given")
                        ),
                    },
                });
            }
            "response.failed" => return Err(failed(&event["response"]["error"], request_id)),
            "error" => {
                let error = if event["error"].is_object() {
                    &event["error"]
                } else {
                    &event
                };
                return Err(failed(error, request_id));
            }
            _ => {}
        }
    }
    Err(TransportError::InDoubt {
        message: "the reply stream ended before response.completed".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reply(status: u16, body: &str) -> HttpReply {
        HttpReply {
            status,
            retry_after_ms: Some(7_000),
            body: body.into(),
            request_id: Some("req_123".into()),
        }
    }

    fn stream(events: &[Value]) -> String {
        events
            .iter()
            .map(|e| format!("event: {}\ndata: {e}\n\n", e["type"].as_str().unwrap()))
            .collect()
    }

    fn completed(text: &str) -> Value {
        json!({"type": "response.completed", "response": {
            "status": "completed",
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 120, "output_tokens": 30,
                      "input_tokens_details": {"cached_tokens": 64}},
        }})
    }

    #[test]
    fn a_completed_stream_gives_its_text_and_usage() {
        let body = stream(&[
            json!({"type": "response.created", "response": {"status": "in_progress"}}),
            json!({"type": "response.output_text.delta", "delta": "{\"items\""}),
            json!({"type": "response.output_text.delta", "delta": ": []}"}),
            completed("{\"items\": []}"),
        ]);
        let response = read_stream(&reply(200, &body)).unwrap();
        assert_eq!(response.text, "{\"items\": []}");
        assert_eq!(
            (
                response.usage.input_tokens,
                response.usage.output_tokens,
                response.usage.cache_read_tokens
            ),
            (120, 30, 64)
        );
        // CRLF line ends and a data line split in two read the same.
        let split = "data: {\"type\": \"response.completed\",\r\ndata: \"response\": {\"output\": []}}\r\n\r\n";
        assert_eq!(read_stream(&reply(200, split)).unwrap().text, "");
        // Without the text in the completed response, the deltas are it.
        let only_deltas = stream(&[
            json!({"type": "response.output_text.delta", "delta": "ok"}),
            json!({"type": "response.completed", "response": {"output": []}}),
        ]);
        assert_eq!(read_stream(&reply(200, &only_deltas)).unwrap().text, "ok");
    }

    #[test]
    fn a_stream_that_never_completes_is_in_doubt() {
        let cut = stream(&[json!({"type": "response.output_text.delta", "delta": "{\"it"})]);
        assert!(matches!(
            read_stream(&reply(200, &cut)),
            Err(TransportError::InDoubt { .. })
        ));
        assert!(matches!(
            read_stream(&reply(200, "data: {not json\n\n")),
            Err(TransportError::InDoubt { .. })
        ));
    }

    #[test]
    fn a_stream_that_stops_says_why() {
        let failed_with = |code: &str| {
            read_stream(&reply(
                200,
                &stream(&[
                    json!({"type": "response.output_text.delta", "delta": "x"}),
                    json!({"type": "response.failed", "response": {"status": "failed",
                           "error": {"code": code, "message": "stopped"}}}),
                ]),
            ))
            .unwrap_err()
        };
        // The plan's limit, reached mid-reply: the request was taken.
        let limit = failed_with("subscription_sharing_usage_limit_exceeded");
        let TransportError::LimitReached { message, started } = &limit else {
            panic!("{limit:?}");
        };
        assert!(*started);
        assert!(message.contains(USAGE_URL), "{message}");
        assert!(message.contains("request req_123"), "{message}");
        assert!(matches!(
            failed_with("subscription_sharing_usage_unavailable"),
            TransportError::InDoubt { .. }
        ));
        assert!(matches!(
            failed_with("server_error"),
            TransportError::InDoubt { .. }
        ));
        let incomplete = |reason: &str| {
            read_stream(&reply(
                200,
                &stream(&[json!({"type": "response.incomplete", "response": {
                    "status": "incomplete", "incomplete_details": {"reason": reason}}})]),
            ))
            .unwrap_err()
        };
        assert!(matches!(
            incomplete("max_output_tokens"),
            TransportError::DidNotFit { .. }
        ));
        assert!(matches!(
            incomplete("content_filter"),
            TransportError::Refused { .. }
        ));
        let refused = stream(&[
            json!({"type": "response.refusal.delta", "delta": "I can't"}),
            completed(""),
        ]);
        assert!(matches!(
            read_stream(&reply(200, &refused)),
            Err(TransportError::Refused { .. })
        ));
        let error_event =
            stream(&[json!({"type": "error", "code": "server_error", "message": "x"})]);
        assert!(matches!(
            read_stream(&reply(200, &error_event)),
            Err(TransportError::InDoubt { .. })
        ));
    }

    #[test]
    fn every_documented_error_has_its_meaning() {
        let error = |status: u16, code: &str| {
            status_error(&reply(
                status,
                &json!({"error": {"code": code, "message": "m", "param": "temperature"}})
                    .to_string(),
            ))
        };
        let detail = |status: u16| status_error(&reply(status, r#"{"detail": "not admitted"}"#));
        use TransportError as E;
        // Admission before the Responses API: identity, policy, routing.
        assert!(matches!(detail(401), E::SignInRequired { .. }));
        assert!(matches!(detail(403), E::Permanent { .. }));
        assert!(matches!(
            detail(503),
            E::Transient {
                retry_after_ms: Some(7_000),
                ..
            }
        ));
        assert!(matches!(
            error(403, "subscription_sharing_user_not_eligible"),
            E::Permanent { .. }
        ));
        assert!(matches!(
            error(429, "subscription_sharing_usage_limit_exceeded"),
            E::LimitReached { started: false, .. }
        ));
        assert!(matches!(
            error(503, "subscription_sharing_usage_unavailable"),
            E::Transient { .. }
        ));
        let unsupported = error(400, "subscription_sharing_unsupported_capability");
        let E::Permanent { message } = &unsupported else {
            panic!("{unsupported:?}");
        };
        // The field to remove is named.
        assert!(message.contains("param temperature"), "{message}");
        assert!(matches!(
            error(403, "subscription_sharing_route_not_supported"),
            E::Permanent { .. }
        ));
        assert!(matches!(
            error(401, "subscription_sharing_invalid_user"),
            E::SignInRequired { .. }
        ));
        assert!(matches!(
            error(403, "chatpass_v2_scope_not_authorized"),
            E::Permanent { .. }
        ));
        assert!(matches!(
            error(403, "chatpass_v2_invalid_authorization_context"),
            E::Permanent { .. }
        ));
        assert!(matches!(
            error(503, "subscription_sharing_user_unavailable"),
            E::Transient { .. }
        ));
        // Any other rate limit is a moment's wait, not the plan's limit.
        assert!(matches!(
            error(429, "rate_limit_exceeded"),
            E::Transient { .. }
        ));
        assert!(matches!(
            error(400, "context_length_exceeded"),
            E::DidNotFit { .. }
        ));
        assert!(matches!(error(400, "invalid_request"), E::Permanent { .. }));
        // A body that is not JSON still gets its status's meaning.
        assert!(matches!(
            status_error(&reply(502, "<html>bad gateway</html>")),
            E::Transient { .. }
        ));
    }

    #[test]
    fn the_body_carries_only_what_the_plans_route_takes() {
        let binding: BindingConfig = serde_json::from_value(json!({
            "id": "plan", "provider": "openai-chatgpt", "endpoint": "https://api.openai.com/v1",
            "model": "gpt-test", "reasoning_effort": "low", "max_output_tokens": 2048,
        }))
        .unwrap();
        let request = ModelRequest {
            system: "classify".into(),
            user: "{\"chunks\": []}".into(),
            response_schema: json!({"type": "object"}),
            enforce_schema: true,
            max_output_tokens: 2048,
        };
        let sent = body(&binding, &request);
        assert_eq!(sent["store"], false);
        assert_eq!(sent["stream"], true);
        assert_eq!(sent["instructions"], "classify");
        assert_eq!(sent["input"][0]["role"], "user");
        assert_eq!(sent["input"].as_array().unwrap().len(), 1);
        assert_eq!(sent["reasoning"]["effort"], "low");
        assert_eq!(sent["text"]["format"]["type"], "json_schema");
        for field in [
            "background",
            "conversation",
            "max_output_tokens",
            "max_tool_calls",
            "metadata",
            "moderation",
            "multi_agent",
            "prompt",
            "prompt_cache_retention",
            "safety_identifier",
            "temperature",
            "top_logprobs",
            "top_p",
            "truncation",
            "user",
            "previous_response_id",
        ] {
            assert!(sent.get(field).is_none(), "{field} was sent");
        }
    }
}

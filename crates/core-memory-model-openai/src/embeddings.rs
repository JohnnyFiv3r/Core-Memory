//! OpenAI-compatible embeddings transport: OpenAI, and the many servers
//! that speak its `/embeddings` API, hosted (Voyage, Mistral, Together,
//! Gemini's compatible endpoint) or on this machine (Ollama, LM Studio,
//! Hugging Face text-embeddings-inference, vLLM, llama.cpp).
//!
//! One call is one `POST {endpoint}/embeddings` with the model and the
//! texts. The reply holds one vector for each text.

use std::fmt;

use core_memory_core::model::{
    BindingConfig, EmbedRequest, EmbedResponse, EmbedTransport, TransportError,
};
use core_memory_model_http::{ClientError, HttpClient, parse_body, status_error};
use serde_json::{Value, json};

use crate::{Dialect, OpenAiTransport};

pub struct OpenAiEmbedTransport {
    client: HttpClient,
    dialect: Dialect,
    api_key: Option<String>,
}

impl fmt::Debug for OpenAiEmbedTransport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OpenAiEmbedTransport")
            .field("dialect", &self.dialect)
            .finish_non_exhaustive()
    }
}

impl OpenAiEmbedTransport {
    /// A transport that sends through `client`. `api_key` may be absent
    /// for a server on this machine that needs none.
    pub fn with_client(dialect: Dialect, api_key: Option<String>, client: HttpClient) -> Self {
        Self {
            client,
            dialect,
            api_key,
        }
    }

    /// Checks that a binding names a provider this transport speaks to and
    /// has an endpoint it can call.
    pub fn check_binding(binding: &BindingConfig) -> Result<(), String> {
        OpenAiTransport::check_binding(binding)
    }

    pub fn body(binding: &BindingConfig, request: &EmbedRequest) -> Value {
        let mut body = json!({
            "model": binding.model,
            "input": request.inputs,
        });
        // Only a model that can be asked for a length is asked: a server
        // that does not know the field may turn the request down.
        if let Some(dimensions) = request.dimensions {
            body["dimensions"] = json!(dimensions);
        }
        body
    }
}

/// The error replies that mean a smaller batch, or a shorter text, may
/// succeed: a 400 that says the input is over the model's context. OpenAI
/// gives the code `context_length_exceeded`; compatible servers say so in
/// the message. Every other error keeps its status mapping.
fn too_long(status: u16, body: &Value) -> Option<ClientError> {
    if status != 400 {
        return None;
    }
    let error = &body["error"];
    let message = error["message"]
        .as_str()
        .or_else(|| error.as_str())
        .or_else(|| body["detail"].as_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    let over = error["code"] == "context_length_exceeded"
        || message.contains("maximum context length")
        || message.contains("context length")
        || message.contains("too many tokens")
        || message.contains("token limit");
    over.then_some(ClientError::DidNotFit)
}

impl EmbedTransport for OpenAiEmbedTransport {
    fn embed(
        &self,
        binding: &BindingConfig,
        request: &EmbedRequest,
    ) -> Result<EmbedResponse, TransportError> {
        Self::check_binding(binding).map_err(|message| TransportError::Permanent { message })?;
        let provider = match self.dialect {
            Dialect::OpenAi => "openai",
            Dialect::Compatible => "openai-compatible",
        };
        let url = format!("{}/embeddings", binding.endpoint.trim_end_matches('/'));
        let auth = self.api_key.as_ref().map(|k| format!("Bearer {k}"));
        let headers: Vec<(&str, &str)> =
            auth.iter().map(|a| ("authorization", a.as_str())).collect();
        let reply = self
            .client
            .post_json(&url, &headers, &Self::body(binding, request))?;
        if !(200..300).contains(&reply.status) {
            return Err(status_error(provider, &reply, too_long));
        }
        let body = parse_body(provider, &reply)?;
        // The provider answered, and may have billed. A reply that is not
        // a vector for each text, in a known order, is not guessed at.
        let unreadable = |what: &str| TransportError::InDoubt {
            message: format!("{provider} returned embeddings that cannot be read: {what}"),
        };
        let Some(data) = body["data"].as_array() else {
            return Err(unreadable("no `data` list"));
        };
        if data.len() != request.inputs.len() {
            return Err(unreadable("not one vector for each text"));
        }
        let mut vectors: Vec<Option<Vec<f32>>> = vec![None; data.len()];
        for (place, item) in data.iter().enumerate() {
            // Each vector says which text it is of. A server that does not
            // say gives them in order.
            let index = match &item["index"] {
                Value::Null => place,
                index => index
                    .as_u64()
                    .and_then(|i| usize::try_from(i).ok())
                    .filter(|i| *i < data.len())
                    .ok_or_else(|| unreadable("a vector's `index` names no text"))?,
            };
            let Some(numbers) = item["embedding"].as_array() else {
                return Err(unreadable("a vector is not a list of numbers"));
            };
            let vector = numbers
                .iter()
                .map(|n| n.as_f64().map(|x| x as f32))
                .collect::<Option<Vec<f32>>>()
                .ok_or_else(|| unreadable("a vector holds what is not a number"))?;
            if vectors[index].replace(vector).is_some() {
                return Err(unreadable("two vectors for one text"));
            }
        }
        let vectors = vectors
            .into_iter()
            .collect::<Option<Vec<Vec<f32>>>>()
            .ok_or_else(|| unreadable("a text has no vector"))?;
        let usage = &body["usage"];
        Ok(EmbedResponse {
            vectors,
            input_tokens: usage["prompt_tokens"]
                .as_u64()
                .or_else(|| usage["total_tokens"].as_u64())
                .unwrap_or(0),
        })
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use core_memory_model_http::testing::serve_once;

    fn binding(provider: &str, endpoint: &str) -> BindingConfig {
        serde_json::from_value(json!({
            "id": "embed",
            "provider": provider,
            "endpoint": endpoint,
            "model": "text-embedding-3-small"
        }))
        .unwrap()
    }

    fn request(dimensions: Option<u32>) -> EmbedRequest {
        EmbedRequest {
            inputs: vec!["first".into(), "second".into()],
            dimensions,
        }
    }

    fn transport(key: Option<&str>) -> OpenAiEmbedTransport {
        OpenAiEmbedTransport::with_client(
            Dialect::OpenAi,
            key.map(str::to_string),
            HttpClient::new(Duration::from_secs(5)),
        )
    }

    #[test]
    fn texts_go_out_and_vectors_come_back_in_the_order_of_the_texts() {
        // The provider may answer in any order; each vector says which
        // text it is of.
        let (url, server) = serve_once(
            200,
            &[],
            r#"{"object": "list", "data": [
                 {"object": "embedding", "index": 1, "embedding": [0.5, -0.25, 0]},
                 {"object": "embedding", "index": 0, "embedding": [1, 2.5, 3e-1]}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 7, "total_tokens": 7}}"#,
        );
        let response = transport(Some("sk-x"))
            .embed(&binding("openai", &format!("{url}/v1")), &request(Some(3)))
            .unwrap();
        assert_eq!(
            response.vectors,
            vec![vec![1.0, 2.5, 0.3], vec![0.5, -0.25, 0.0]]
        );
        assert_eq!(response.input_tokens, 7);
        let received = server.join().unwrap();
        assert_eq!(received.request_line, "POST /v1/embeddings HTTP/1.1");
        assert_eq!(received.header("authorization"), Some("Bearer sk-x"));
        let sent: Value = serde_json::from_str(&received.body).unwrap();
        assert_eq!(sent["model"], "text-embedding-3-small");
        assert_eq!(sent["input"], json!(["first", "second"]));
        assert_eq!(sent["dimensions"], 3);
    }

    #[test]
    fn a_local_server_needs_no_key_and_is_not_asked_for_a_length_it_was_not_given() {
        // Ollama and others give no index and count tokens their own way.
        let (url, server) = serve_once(
            200,
            &[],
            r#"{"data": [{"embedding": [1, 0]}, {"embedding": [0, 1]}],
                "usage": {"total_tokens": 4}}"#,
        );
        let response = transport(None)
            .embed(&binding("openai-compatible", &url), &request(None))
            .unwrap();
        assert_eq!(response.vectors, vec![vec![1.0, 0.0], vec![0.0, 1.0]]);
        assert_eq!(response.input_tokens, 4);
        let received = server.join().unwrap();
        assert_eq!(received.header("authorization"), None);
        let sent: Value = serde_json::from_str(&received.body).unwrap();
        assert!(sent.get("dimensions").is_none());
    }

    #[test]
    fn a_reply_that_is_not_a_vector_for_each_text_is_in_doubt() {
        for body in [
            r#"{"data": [{"index": 0, "embedding": [1, 0]}]}"#,
            r#"{"data": [{"index": 0, "embedding": [1]}, {"index": 0, "embedding": [1]}]}"#,
            r#"{"data": [{"index": 0, "embedding": [1]}, {"index": 5, "embedding": [1]}]}"#,
            r#"{"data": [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": ["x"]}]}"#,
            r#"{"data": [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": "AAAA"}]}"#,
            r#"{"embeddings": [[1], [2]]}"#,
        ] {
            let (url, _server) = serve_once(200, &[], body);
            let err = transport(Some("k"))
                .embed(&binding("openai", &url), &request(None))
                .unwrap_err();
            assert!(
                matches!(err, TransportError::InDoubt { .. }),
                "{body}: {err:?}"
            );
        }
    }

    #[test]
    fn a_rate_limit_passes_a_rejected_key_does_not_and_a_long_text_did_not_fit() {
        let send = |status: u16, headers: &[(&str, &str)], body: &str| {
            let (url, _server) = serve_once(status, headers, body);
            transport(Some("k"))
                .embed(&binding("openai", &url), &request(None))
                .unwrap_err()
        };
        assert!(matches!(
            send(429, &[("retry-after", "2")], "{}"),
            TransportError::Transient {
                retry_after_ms: Some(2000),
                ..
            }
        ));
        assert!(matches!(
            send(503, &[], "{}"),
            TransportError::Transient {
                retry_after_ms: None,
                ..
            }
        ));
        assert!(matches!(
            send(
                401,
                &[],
                r#"{"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}"#
            ),
            TransportError::Permanent { .. }
        ));
        assert!(matches!(
            send(
                400,
                &[],
                r#"{"error": {"message": "This model's maximum context length is 8192 tokens, however you requested 9000 tokens", "type": "invalid_request_error"}}"#
            ),
            TransportError::DidNotFit { .. }
        ));
        assert!(matches!(
            send(
                400,
                &[],
                r#"{"error": {"message": "unknown field dimensions"}}"#
            ),
            TransportError::Permanent { .. }
        ));
    }
}

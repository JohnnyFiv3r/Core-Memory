//! The OpenAPI 3.1 document for `core-memory serve`.
//!
//! Schemas come from the same schemars derives that publish the contracts,
//! so the API description cannot drift from the types the server uses.

use schemars::generate::SchemaSettings;
use serde_json::{Value, json};

use core_memory_contract::{AdmissionOutcome, RecallAnswer, RecallRequest, SourceEvent};

use crate::server::{
    BatchRequest, BatchResponse, CheckpointResponse, ErrorResponse, ReadyResponse,
};

pub fn document() -> Value {
    let mut generator = SchemaSettings::draft2020_12()
        .with(|s| s.definitions_path = "#/components/schemas/".into())
        .into_generator();
    let source_event = generator.subschema_for::<SourceEvent>();
    let outcome = generator.subschema_for::<AdmissionOutcome>();
    let batch_request = generator.subschema_for::<BatchRequest>();
    let batch_response = generator.subschema_for::<BatchResponse>();
    let checkpoint = generator.subschema_for::<CheckpointResponse>();
    let ready = generator.subschema_for::<ReadyResponse>();
    let error = generator.subschema_for::<ErrorResponse>();
    let recall_request = generator.subschema_for::<RecallRequest>();
    let recall_answer = generator.subschema_for::<RecallAnswer>();
    let schemas = generator.take_definitions(true);

    let json_body = |schema: &schemars::Schema| json!({"application/json": {"schema": schema}});
    let outcome_response =
        |description: &str| json!({"description": description, "content": json_body(&outcome)});
    let error_response =
        |description: &str| json!({"description": description, "content": json_body(&error)});
    // A status that carries an outcome in one case and an error in another.
    let outcome_or_error = |description: &str| {
        json!({
            "description": description,
            "content": {"application/json": {"schema": {"oneOf": [&outcome, &error]}}},
        })
    };

    json!({
        "openapi": "3.1.0",
        "info": {
            "title": "Core Memory engine",
            "version": env!("CARGO_PKG_VERSION"),
            "description": "Admit SourceEvents and receive typed admission outcomes. An accepted event is stored in PostgreSQL before the answer is sent. Recall what is stored: the text that holds some words, verbatim, with what a judge said of how it relates.",
        },
        "paths": {
            "/v1/events": {
                "post": {
                    "operationId": "admitEvent",
                    "summary": "Admit one SourceEvent",
                    "security": [{"bearer": []}],
                    "requestBody": {"required": true, "content": json_body(&source_event)},
                    "responses": {
                        "200": outcome_response("Accepted, or a replay of an event already admitted"),
                        "400": outcome_or_error("Refused as malformed. Or, as an error, the request body could not be read"),
                        "401": error_response("Missing or unknown bearer token. Answered before any of the body is read"),
                        "403": outcome_response("Refused: the token holds no grant for the scope, or the scope is not enrolled, paused or revoked"),
                        "408": error_response("The request body did not arrive in time"),
                        "409": outcome_response("Identity conflict: the key was admitted with different content. Or checkpoint conflict: the event's checkpoint does not follow the stored one"),
                        "410": outcome_response("Refused: the event was erased"),
                        "413": outcome_or_error("Refused as oversize: the content or the metadata is over its limit. Or, as an error, the request body is over the server's limit"),
                        "422": outcome_response("Refused: the profile does not exist or cannot admit the event"),
                        "503": {
                            "description": "Busy: the store cannot take the event right now, because it is unreachable, overloaded or being upgraded. Retry after the given delay. Not a terminal failure.",
                            "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Seconds"}},
                            "content": json_body(&outcome),
                        },
                    },
                },
            },
            "/v1/events/batch": {
                "post": {
                    "operationId": "admitEvents",
                    "summary": "Admit several SourceEvents in order; one outcome per event",
                    "security": [{"bearer": []}],
                    "requestBody": {"required": true, "content": json_body(&batch_request)},
                    "responses": {
                        "200": {"description": "One outcome per event, in request order. Read every outcome: some events may be busy or refused while others were accepted.", "content": json_body(&batch_response)},
                        "400": error_response("The body is not {\"events\": [...]}"),
                        "401": error_response("Missing or unknown bearer token. Answered before any of the body is read"),
                        "408": error_response("The request body did not arrive in time"),
                        "413": error_response("Too many events in one batch, or the request body is over the server's limit"),
                        "503": {
                            "description": "Every event is busy: the store took none of them. Retry the batch after the given delay.",
                            "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Seconds"}},
                            "content": json_body(&batch_response),
                        },
                    },
                },
            },
            "/v1/checkpoints": {
                "get": {
                    "operationId": "readCheckpoint",
                    "summary": "A reader's stored position, to resume from after a restart",
                    "security": [{"bearer": []}],
                    "parameters": [
                        {"name": "scope", "in": "query", "required": true, "schema": {"type": "string"}},
                        {"name": "key", "in": "query", "required": true, "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {"description": "The stored sequence and hash", "content": json_body(&checkpoint)},
                        "400": error_response("`scope` or `key` is missing or is not a name"),
                        "401": error_response("Missing or unknown bearer token"),
                        "403": error_response("The token holds no grant for the scope"),
                        "404": error_response("No checkpoint is stored under the key"),
                        "503": {
                            "description": "The store cannot answer right now. Retry after the given delay",
                            "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Seconds"}},
                            "content": json_body(&error),
                        },
                    },
                },
            },
            "/v1/recall": {
                "post": {
                    "operationId": "recall",
                    "summary": "The stored text that holds some words or answers a question, with what a judge said of how it relates",
                    "description": "Searches the scopes the token may recall from, or those of them the request names. A match that a later statement replaced or withdrew is answered by the statement that now stands in its place. Arrows are followed up to `follow` steps, never into a scope the request may not read. Text comes verbatim, chunk by chunk, up to 1 MiB in one answer. Pausing or revoking a scope does not stop it being recalled; erasing a message does. Where the engine has an embedder, the query is also searched for by meaning: it is sent to the embedder to be given a vector, unless `search` is `words`, and the answer's `meaning` says whether it was and each match's `by` says which search found it.",
                    "security": [{"bearer": []}],
                    "requestBody": {"required": true, "content": json_body(&recall_request)},
                    "responses": {
                        "200": {"description": "What was found: `matched`, `no_match`, `no_terms` when the query holds no word to look for (only common words, punctuation or words to leave out), or `not_searched` when a search by meaning alone was asked for and could not be made", "content": json_body(&recall_answer)},
                        "400": error_response("The body is not a recall request, or a value is out of range: empty_query, query_too_long, query_control_character, bad_limit, bad_follow or bad_scopes"),
                        "401": error_response("Missing or unknown bearer token. Answered before any of the body is read"),
                        "403": error_response("The token may not recall from a scope the request names (scope_not_granted), or from any scope (recall_not_granted)"),
                        "408": error_response("The request body did not arrive in time"),
                        "413": error_response("The request body is over the server's limit"),
                        "503": {
                            "description": "The store cannot answer right now. Retry after the given delay",
                            "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Seconds"}},
                            "content": json_body(&error),
                        },
                    },
                },
            },
            "/v1/ready": {
                "get": {
                    "operationId": "readiness",
                    "summary": "Whether new events would be admitted now",
                    "responses": {
                        "200": {"description": "Ready", "content": json_body(&ready)},
                        "503": {"description": "Not ready: the store is unreachable, takes no writes, refuses the engine's connection, or is at another schema version", "content": json_body(&ready)},
                    },
                },
            },
            "/v1/health": {
                "get": {
                    "operationId": "health",
                    "summary": "Liveness",
                    "responses": {"200": {"description": "The process is up"}},
                },
            },
            "/v1/schemas/{name}": {
                "get": {
                    "operationId": "contractSchema",
                    "summary": "A published contract's JSON Schema",
                    "parameters": [{
                        "name": "name", "in": "path", "required": true,
                        "schema": {"type": "string", "enum": ["source_event", "admission_outcome", "record"]},
                    }],
                    "responses": {
                        "200": {"description": "The JSON Schema"},
                        "404": error_response("No such contract"),
                    },
                },
            },
        },
        "components": {
            "schemas": schemas,
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn refs(value: &Value, out: &mut Vec<String>) {
        match value {
            Value::Object(map) => {
                for (k, v) in map {
                    if k == "$ref" {
                        if let Some(s) = v.as_str() {
                            out.push(s.to_string());
                        }
                    } else {
                        refs(v, out);
                    }
                }
            }
            Value::Array(items) => items.iter().for_each(|v| refs(v, out)),
            _ => {}
        }
    }

    #[test]
    fn every_ref_resolves_to_a_component() {
        let doc = document();
        let mut found = Vec::new();
        refs(&doc, &mut found);
        assert!(!found.is_empty());
        let schemas = doc["components"]["schemas"].as_object().unwrap();
        for r in found {
            let name = r
                .strip_prefix("#/components/schemas/")
                .unwrap_or_else(|| panic!("unexpected ref {r}"));
            assert!(schemas.contains_key(name), "unresolved {r}");
        }
        assert!(schemas.contains_key("SourceEvent"));
        assert!(schemas.contains_key("AdmissionOutcome"));
        assert!(schemas.contains_key("Checkpoint"));
        assert!(schemas.contains_key("RecallRequest"));
        assert!(schemas.contains_key("RecallAnswer"));
        assert!(doc["paths"]["/v1/recall"]["post"].is_object());
    }
}

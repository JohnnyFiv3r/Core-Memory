//! The OpenAPI 3.1 document for `core-memory serve`.
//!
//! Schemas come from the same schemars derives that publish the contracts,
//! so the API description cannot drift from the types the server uses.

use schemars::generate::SchemaSettings;
use serde_json::{Value, json};

use core_memory_contract::{AdmissionOutcome, SourceEvent};

use crate::server::{BatchRequest, BatchResponse, ErrorResponse, ReadyResponse};

pub fn document() -> Value {
    let mut generator = SchemaSettings::draft2020_12()
        .with(|s| s.definitions_path = "#/components/schemas/".into())
        .into_generator();
    let source_event = generator.subschema_for::<SourceEvent>();
    let outcome = generator.subschema_for::<AdmissionOutcome>();
    let batch_request = generator.subschema_for::<BatchRequest>();
    let batch_response = generator.subschema_for::<BatchResponse>();
    let ready = generator.subschema_for::<ReadyResponse>();
    let error = generator.subschema_for::<ErrorResponse>();
    let schemas = generator.take_definitions(true);

    let json_body = |schema: &schemars::Schema| json!({"application/json": {"schema": schema}});
    let outcome_response =
        |description: &str| json!({"description": description, "content": json_body(&outcome)});
    let error_response =
        |description: &str| json!({"description": description, "content": json_body(&error)});

    json!({
        "openapi": "3.1.0",
        "info": {
            "title": "Core Memory engine",
            "version": env!("CARGO_PKG_VERSION"),
            "description": "Admit SourceEvents and receive typed admission outcomes. Records are delivered to the configured sinks, not returned here.",
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
                        "400": outcome_response("Refused as malformed"),
                        "401": error_response("Missing or unknown bearer token"),
                        "403": outcome_response("Refused: the token holds no grant for the event's scope"),
                        "409": outcome_response("Identity conflict: the key was admitted with different content"),
                        "410": outcome_response("Refused: the event key was erased"),
                        "413": outcome_response("Refused as oversize"),
                        "422": outcome_response("Refused: the profile does not exist or cannot admit the event"),
                        "503": {
                            "description": "Busy: retry after the given delay. Not a terminal failure.",
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
                        "200": {"description": "One outcome per event, in request order", "content": json_body(&batch_response)},
                        "400": error_response("The body is not {\"events\": [...]}"),
                        "401": error_response("Missing or unknown bearer token"),
                        "413": error_response("Too many events in one batch"),
                    },
                },
            },
            "/v1/ready": {
                "get": {
                    "operationId": "readiness",
                    "summary": "Whether new events would be admitted now",
                    "responses": {
                        "200": {"description": "Ready", "content": json_body(&ready)},
                        "503": {"description": "Not ready: the slowest required sink is too far behind", "content": json_body(&ready)},
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
    }
}

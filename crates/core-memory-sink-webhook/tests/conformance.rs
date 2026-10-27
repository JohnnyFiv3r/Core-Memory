//! The webhook sink against the sink conformance suite, delivering to a
//! local receiver that checks every signature.

#![allow(clippy::unwrap_used)]

mod common;

use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use common::{Request, Response, SECRET, start};
use core_memory_conformance::Harness;
use core_memory_contract::RECORD_CONTRACT_V1;
use core_memory_core::SystemClock;
use core_memory_core::sink::Sink;
use core_memory_sink_webhook::{
    DEFAULT_MAX_REQUEST_BYTES, SIGNATURE_HEADER, WebhookConfig, WebhookSink, verify,
};
use serde_json::{Value, json};

/// Answers like a host callback that appends what it gets (`204`), or
/// like a system of record that keeps the first record per key and
/// answers per record.
fn receive(request: &Request, held: &Mutex<Vec<Value>>, system_of_record: bool) -> Response {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let signature = request.header(SIGNATURE_HEADER).unwrap_or_default();
    if let Err(e) = verify(SECRET, signature, request.body.as_bytes(), now, 300) {
        return Response::json(401, json!({"error": e.to_string()}));
    }
    let body: Value = serde_json::from_str(&request.body).unwrap();
    assert_eq!(body["contract"], RECORD_CONTRACT_V1);
    let mut held = held.lock().unwrap();
    let records = body["records"].as_array().unwrap();
    if !system_of_record {
        held.extend(records.iter().cloned());
        return Response::empty(204);
    }
    let mut results = Vec::new();
    for record in records {
        let stored = held
            .iter()
            .find(|r| r["record_key"] == record["record_key"])
            .cloned();
        results.push(match stored {
            Some(stored) if stored == *record => json!({"outcome": "already_accepted"}),
            Some(stored) => json!({"outcome": "conflict", "stored": stored}),
            None => {
                held.push(record.clone());
                json!({"outcome": "accepted"})
            }
        });
    }
    Response::json(200, json!({ "results": results }))
}

struct WebhookHarness {
    url: String,
    held: Arc<Mutex<Vec<Value>>>,
    system_of_record: bool,
}

impl WebhookHarness {
    fn new(system_of_record: bool) -> Self {
        let held = Arc::new(Mutex::new(Vec::new()));
        let store = held.clone();
        let url = start(move |r| receive(r, &store, system_of_record));
        Self {
            url,
            held,
            system_of_record,
        }
    }
}

impl Harness for WebhookHarness {
    fn open(&mut self, max_record_bytes: u64) -> Box<dyn Sink> {
        let config = WebhookConfig {
            url: self.url.clone(),
            secret: SECRET.to_vec(),
            timeout: Duration::from_secs(10),
            max_record_bytes,
            max_request_bytes: DEFAULT_MAX_REQUEST_BYTES,
            system_of_record: self.system_of_record,
        };
        Box::new(WebhookSink::new("host", config, Arc::new(SystemClock)).unwrap())
    }

    fn read(&mut self) -> Vec<Value> {
        self.held.lock().unwrap().clone()
    }
}

mod host_callback {
    use super::*;
    core_memory_conformance::sink_conformance!(WebhookHarness::new(false));
}

mod system_of_record {
    use super::*;
    core_memory_conformance::sink_conformance!(WebhookHarness::new(true));
}

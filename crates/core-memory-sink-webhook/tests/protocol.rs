//! How the webhook sink reads its receiver's answers.

#![allow(clippy::unwrap_used)]

mod common;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use common::{Request, Response, SECRET, start};
use core_memory_core::sink::{Sink, WriteOutcome};
use core_memory_core::store::OutboxEntry;
use core_memory_core::{Clock, ManualClock};
use core_memory_sink_webhook::{SIGNATURE_HEADER, WebhookConfig, WebhookSink, verify};
use serde_json::json;

const T0: i64 = 1_793_072_000_000;

fn sink(url: &str) -> WebhookSink {
    sink_at(url, Arc::new(ManualClock::at_unix_millis(T0)))
}

fn sink_at(url: &str, clock: Arc<dyn Clock>) -> WebhookSink {
    let config = WebhookConfig {
        url: url.into(),
        secret: SECRET.to_vec(),
        timeout: Duration::from_secs(5),
        max_record_bytes: 1 << 20,
        system_of_record: false,
    };
    WebhookSink::new("host", config, clock).unwrap()
}

fn batch(n: u64) -> Vec<OutboxEntry> {
    (1..=n)
        .map(|seq| OutboxEntry {
            seq,
            record_key: format!("chunk:{seq}"),
            record_json:
                json!({"contract": "core_memory.record.v1", "record_key": format!("chunk:{seq}")})
                    .to_string(),
        })
        .collect()
}

#[test]
fn requests_are_signed_at_the_injected_time() {
    let seen: Arc<Mutex<Vec<Request>>> = Arc::default();
    let log = seen.clone();
    let url = start(move |r| {
        log.lock().unwrap().push(r.clone());
        Response::empty(204)
    });
    let outcomes = sink(&url).write(&batch(2)).unwrap();
    assert_eq!(outcomes, vec![WriteOutcome::Accepted; 2]);

    let request = seen.lock().unwrap().remove(0);
    let signature = request.header(SIGNATURE_HEADER).unwrap();
    assert!(signature.starts_with("t=1793072000,v1="), "{signature}");
    assert_eq!(
        verify(SECRET, signature, request.body.as_bytes(), T0 / 1000, 0),
        Ok(())
    );
    assert_eq!(request.header("content-type"), Some("application/json"));
}

#[test]
fn per_record_answers_map_to_outcomes() {
    let url = start(|_| {
        Response::json(
            200,
            json!({"results": [
                {"outcome": "already_accepted"},
                {"outcome": "rejected", "reason": "unknown scope"},
                {"outcome": "conflict", "stored": {"record_key": "chunk:3"}},
                {"outcome": "retryable", "retry_after_ms": 1500}
            ]}),
        )
    });
    assert_eq!(
        sink(&url).write(&batch(4)).unwrap(),
        vec![
            WriteOutcome::AlreadyAccepted,
            WriteOutcome::Rejected {
                reason: "unknown scope".into()
            },
            WriteOutcome::Conflict {
                stored: r#"{"record_key":"chunk:3"}"#.into()
            },
            WriteOutcome::Retryable {
                retry_after_ms: 1500
            },
        ]
    );
}

#[test]
fn any_other_success_accepts_the_batch() {
    for body in ["", "ok", "{\"status\": \"ok\"}"] {
        let url = start(move |_| Response {
            status: 200,
            headers: Vec::new(),
            body: body.into(),
        });
        assert_eq!(
            sink(&url).write(&batch(2)).unwrap(),
            vec![WriteOutcome::Accepted; 2],
            "{body}"
        );
    }
}

#[test]
fn a_failed_response_writes_nothing() {
    let url = start(|_| {
        let mut r = Response::json(503, json!({"error": "busy"}));
        r.headers.push(("retry-after".into(), "7".into()));
        r
    });
    let err = sink(&url).write(&batch(1)).unwrap_err();
    assert_eq!(err.retry_after_ms, 7000);
    assert!(err.message.contains("503"), "{}", err.message);

    let url = start(|_| Response::json(401, json!({"error": "bad signature"})));
    let err = sink(&url).write(&batch(1)).unwrap_err();
    assert!(err.message.contains("401"), "{}", err.message);
}

#[test]
fn answers_that_do_not_match_the_batch_write_nothing() {
    let url = start(|_| Response::json(200, json!({"results": [{"outcome": "accepted"}]})));
    let err = sink(&url).write(&batch(2)).unwrap_err();
    assert!(err.message.contains("1 results for 2"), "{}", err.message);

    let url = start(|_| Response::json(200, json!({"results": [{"outcome": "maybe"}]})));
    assert!(sink(&url).write(&batch(1)).is_err());
}

#[test]
fn an_unreachable_receiver_is_tried_later() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/hook", listener.local_addr().unwrap());
    drop(listener);
    let err = sink(&url).write(&batch(1)).unwrap_err();
    assert!(err.retry_after_ms > 0);
}

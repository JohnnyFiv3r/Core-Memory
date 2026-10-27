//! How the webhook sink reads its receiver's answers.

#![allow(clippy::unwrap_used)]

mod common;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use common::{Request, Response, SECRET, start};
use core_memory_core::sink::{Sink, WriteOutcome};
use core_memory_core::store::OutboxEntry;
use core_memory_core::{Clock, ManualClock};
use core_memory_sink_webhook::{
    DEFAULT_MAX_REQUEST_BYTES, SIGNATURE_HEADER, WebhookConfig, WebhookSink, verify,
};
use serde_json::json;

const T0: i64 = 1_793_072_000_000;

fn sink(url: &str) -> WebhookSink {
    sink_at(url, Arc::new(ManualClock::at_unix_millis(T0)))
}

fn sink_at(url: &str, clock: Arc<dyn Clock>) -> WebhookSink {
    sink_with(url, clock, DEFAULT_MAX_REQUEST_BYTES)
}

fn sink_with(url: &str, clock: Arc<dyn Clock>, max_request_bytes: u64) -> WebhookSink {
    let config = WebhookConfig {
        url: url.into(),
        secret: SECRET.to_vec(),
        timeout: Duration::from_secs(5),
        max_record_bytes: 1 << 20,
        max_request_bytes,
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
                {"outcome": "retryable", "retry_after_ms": 1500, "reason": "scope gamma is unknown"}
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
                stored: Some(r#"{"record_key":"chunk:3"}"#.into())
            },
            WriteOutcome::Retryable {
                retry_after_ms: 1500,
                reason: Some("scope gamma is unknown".into())
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

#[test]
fn answers_may_leave_out_their_details() {
    let url = start(|_| {
        Response::json(
            200,
            json!({"results": [
                {"outcome": "conflict"},
                {"outcome": "rejected"},
                {"outcome": "retryable"}
            ]}),
        )
    });
    assert_eq!(
        sink(&url).write(&batch(3)).unwrap(),
        vec![
            WriteOutcome::Conflict { stored: None },
            WriteOutcome::Rejected {
                reason: "the receiver gave no reason".into()
            },
            WriteOutcome::Retryable {
                retry_after_ms: 5000,
                reason: None
            },
        ]
    );
}

#[test]
fn a_redirect_is_not_followed() {
    let seen: Arc<Mutex<Vec<Request>>> = Arc::default();
    let log = seen.clone();
    let url = start(move |r| {
        log.lock().unwrap().push(r.clone());
        let mut response = Response::empty(301);
        response
            .headers
            .push(("location".into(), "https://example.com/hook/".into()));
        response
    });
    let err = sink(&url).write(&batch(2)).unwrap_err();
    assert!(
        err.message
            .contains("redirect to https://example.com/hook/"),
        "{}",
        err.message
    );
    assert_eq!(seen.lock().unwrap().len(), 1);
}

#[test]
fn a_large_batch_goes_as_requests_within_the_limit() {
    let seen: Arc<Mutex<Vec<Request>>> = Arc::default();
    let log = seen.clone();
    let url = start(move |r| {
        log.lock().unwrap().push(r.clone());
        Response::empty(204)
    });
    let records = batch(5);
    let envelope = core_memory_sink_webhook::body("host", &[]).len();
    let limit = envelope + 2 * records[0].record_json.len() + 1;
    let mut sink = sink_with(
        &url,
        Arc::new(ManualClock::at_unix_millis(T0)),
        limit as u64,
    );
    assert_eq!(
        sink.write(&records).unwrap(),
        vec![WriteOutcome::Accepted; 5]
    );

    let requests = seen.lock().unwrap().clone();
    assert_eq!(requests.len(), 3);
    let mut keys = Vec::new();
    for request in &requests {
        assert!(
            request.body.len() <= limit,
            "{} > {limit}",
            request.body.len()
        );
        let body: serde_json::Value = serde_json::from_str(&request.body).unwrap();
        for record in body["records"].as_array().unwrap() {
            keys.push(record["record_key"].as_str().unwrap().to_string());
        }
    }
    assert_eq!(
        keys,
        ["chunk:1", "chunk:2", "chunk:3", "chunk:4", "chunk:5"]
    );
    // A record that could not fit in one request is refused, not retried.
    assert_eq!(sink.caps().max_record_bytes, (limit - envelope) as u64);
}

#[test]
fn a_failed_later_request_keeps_the_earlier_answers() {
    let calls = Arc::new(Mutex::new(0));
    let count = calls.clone();
    let url = start(move |_| {
        let mut n = count.lock().unwrap();
        *n += 1;
        if *n == 1 {
            Response::empty(204)
        } else {
            let mut r = Response::json(503, json!({"error": "busy"}));
            r.headers.push(("retry-after".into(), "2".into()));
            r
        }
    });
    let records = batch(4);
    let envelope = core_memory_sink_webhook::body("host", &[]).len();
    let limit = envelope + 2 * records[0].record_json.len() + 1;
    let mut sink = sink_with(
        &url,
        Arc::new(ManualClock::at_unix_millis(T0)),
        limit as u64,
    );
    let outcomes = sink.write(&records).unwrap();
    assert_eq!(
        outcomes[..2],
        [WriteOutcome::Accepted, WriteOutcome::Accepted]
    );
    for outcome in &outcomes[2..] {
        match outcome {
            WriteOutcome::Retryable {
                retry_after_ms: 2000,
                reason: Some(reason),
            } => assert!(reason.contains("503"), "{reason}"),
            other => panic!("{other:?}"),
        }
    }
}

#[test]
fn a_reply_larger_than_ten_megabytes_is_read() {
    let stored = "x".repeat(11 << 20);
    let answer = json!({"results": [{"outcome": "conflict", "stored": {"text": stored}}]});
    let url = start(move |_| Response::json(200, answer.clone()));
    match sink(&url).write(&batch(1)).unwrap().as_slice() {
        [WriteOutcome::Conflict { stored: Some(s) }] => assert!(s.len() > 11 << 20),
        other => panic!("{other:?}"),
    }
}

//! The HTTP API, driven through the router without a network socket.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::time::{Duration, Instant};

use axum::Router;
use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use core_memory_contract::Sha256Digest;
use core_memory_sidecar::config::Loaded;
use core_memory_sidecar::server::{Prepared, prepare};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use tower::ServiceExt;

const WRITER: &str = "cm_test_writer";
const OUTSIDER: &str = "cm_test_outsider";

fn write_config(dir: &Path, extra: &str) -> Loaded {
    let config = format!(
        r#"
[store]
path = "store.sqlite"

[grants]
scopes = ["acme"]

[[sink]]
id = "records"
kind = "jsonl"
path = "records.jsonl"

[http]
max_batch_events = 3
delivery_interval_ms = 50

[[http.token]]
id = "writer"
sha256 = "{writer}"
scopes = ["acme"]

[[http.token]]
id = "outsider"
sha256 = "{outsider}"
scopes = ["elsewhere"]
{extra}
"#,
        writer = Sha256Digest::of(WRITER.as_bytes()),
        outsider = Sha256Digest::of(OUTSIDER.as_bytes()),
    );
    let path = dir.join("core-memory.toml");
    std::fs::write(&path, config).unwrap();
    Loaded::read(&path).unwrap()
}

fn event(id: &str, text: &str) -> Value {
    json!({
        "scope": "acme",
        "source_ns": "slack/T01",
        "external_id": id,
        "profile_id": "core.chat_transcript",
        "media_type": "text/plain",
        "content": {"text": text},
        "parser_contract_version": "slack-export/1"
    })
}

async fn call(
    router: &Router,
    method: &str,
    uri: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> (StatusCode, Value, axum::http::HeaderMap) {
    let mut request = Request::builder().method(method).uri(uri);
    if let Some(token) = token {
        request = request.header(header::AUTHORIZATION, format!("Bearer {token}"));
    }
    let body = match body {
        Some(v) => Body::from(v.to_string()),
        None => Body::empty(),
    };
    let response = router
        .clone()
        .oneshot(request.body(body).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let value = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes)
            .unwrap_or_else(|_| Value::String(String::from_utf8_lossy(&bytes).into()))
    };
    (status, value, headers)
}

fn server(dir: &Path, extra: &str, delivery: bool) -> Prepared {
    prepare(&write_config(dir, extra), delivery).unwrap()
}

#[tokio::test]
async fn requests_need_a_valid_token() {
    let dir = tempfile::tempdir().unwrap();
    let s = server(dir.path(), "", false);
    let (status, body, headers) = call(
        &s.router,
        "POST",
        "/v1/events",
        None,
        Some(event("1", "hi")),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"]["code"], "unauthorized");
    assert_eq!(headers[header::WWW_AUTHENTICATE], "Bearer");
    let (status, ..) = call(
        &s.router,
        "POST",
        "/v1/events",
        Some("cm_wrong"),
        Some(event("1", "hi")),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    // The scheme name is case-insensitive.
    let request = Request::builder()
        .method("POST")
        .uri("/v1/events")
        .header(header::AUTHORIZATION, format!("bearer {WRITER}"))
        .body(Body::from(event("1", "hi").to_string()))
        .unwrap();
    let response = s.router.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn admission_outcomes_map_to_statuses() {
    let dir = tempfile::tempdir().unwrap();
    let s = server(dir.path(), "", false);
    let r = &s.router;

    let (status, body, _) = call(
        r,
        "POST",
        "/v1/events",
        Some(WRITER),
        Some(event("1", "We ship Friday.")),
    )
    .await;
    assert_eq!(
        (status, body["outcome"].as_str()),
        (StatusCode::OK, Some("accepted"))
    );

    let (status, body, _) = call(
        r,
        "POST",
        "/v1/events",
        Some(WRITER),
        Some(event("1", "We ship Friday.")),
    )
    .await;
    assert_eq!(
        (status, body["outcome"].as_str()),
        (StatusCode::OK, Some("replay"))
    );

    let (status, body, _) = call(
        r,
        "POST",
        "/v1/events",
        Some(WRITER),
        Some(event("1", "Changed.")),
    )
    .await;
    assert_eq!(
        (status, body["outcome"].as_str()),
        (StatusCode::CONFLICT, Some("identity_conflict"))
    );

    let (status, body, _) = call(
        r,
        "POST",
        "/v1/events",
        Some(OUTSIDER),
        Some(event("2", "hi")),
    )
    .await;
    assert_eq!(
        (status, body["reason"].as_str()),
        (StatusCode::FORBIDDEN, Some("scope_denied"))
    );

    let mut unknown = event("3", "hi");
    unknown["profile_id"] = json!("nope");
    let (status, body, _) = call(r, "POST", "/v1/events", Some(WRITER), Some(unknown)).await;
    assert_eq!(
        (status, body["reason"].as_str()),
        (StatusCode::UNPROCESSABLE_ENTITY, Some("invalid_profile"))
    );

    let (status, body, _) = call(
        r,
        "POST",
        "/v1/events",
        Some(WRITER),
        Some(json!({"scope": "acme"})),
    )
    .await;
    assert_eq!(
        (status, body["reason"].as_str()),
        (StatusCode::BAD_REQUEST, Some("malformed"))
    );
}

#[tokio::test]
async fn batches_return_one_outcome_per_event() {
    let dir = tempfile::tempdir().unwrap();
    let s = server(dir.path(), "", false);
    let body = json!({"events": [event("b1", "one"), {"not": "an event"}, event("b2", "two")]});
    let (status, body, _) = call(
        &s.router,
        "POST",
        "/v1/events/batch",
        Some(WRITER),
        Some(body),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let outcomes: Vec<_> = body["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["outcome"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(outcomes, vec!["accepted", "refused", "accepted"]);

    let too_many =
        json!({"events": [event("x1", "a"), event("x2", "b"), event("x3", "c"), event("x4", "d")]});
    let (status, body, _) = call(
        &s.router,
        "POST",
        "/v1/events/batch",
        Some(WRITER),
        Some(too_many),
    )
    .await;
    assert_eq!(
        (status, body["error"]["code"].as_str()),
        (StatusCode::PAYLOAD_TOO_LARGE, Some("batch_too_large"))
    );
}

#[tokio::test]
async fn backlog_makes_the_server_busy_and_not_ready() {
    let dir = tempfile::tempdir().unwrap();
    let s = server(
        dir.path(),
        "[limits]\nmax_outbox_backlog = 1\nbusy_retry_after_ms = 1500",
        false,
    );
    let (status, body, _) = call(&s.router, "GET", "/v1/ready", None, None).await;
    assert_eq!(
        (status, body["ready"].as_bool()),
        (StatusCode::OK, Some(true))
    );

    call(
        &s.router,
        "POST",
        "/v1/events",
        Some(WRITER),
        Some(event("1", "first")),
    )
    .await;
    let (status, body, headers) = call(
        &s.router,
        "POST",
        "/v1/events",
        Some(WRITER),
        Some(event("2", "second")),
    )
    .await;
    assert_eq!(
        (status, body["outcome"].as_str()),
        (StatusCode::SERVICE_UNAVAILABLE, Some("busy"))
    );
    assert_eq!(headers[header::RETRY_AFTER], "2");

    let (status, body, _) = call(&s.router, "GET", "/v1/ready", None, None).await;
    assert_eq!(
        (status, body["backlog"].as_u64()),
        (StatusCode::SERVICE_UNAVAILABLE, Some(1))
    );
}

#[tokio::test]
async fn admitted_records_are_delivered_in_the_background() {
    let dir = tempfile::tempdir().unwrap();
    let s = server(dir.path(), "", true);
    let (status, ..) = call(
        &s.router,
        "POST",
        "/v1/events",
        Some(WRITER),
        Some(event("d1", "deliver me")),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let records = dir.path().join("records.jsonl");
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let lines = std::fs::read_to_string(&records)
            .unwrap_or_default()
            .lines()
            .count();
        if lines == 1 {
            break;
        }
        assert!(Instant::now() < deadline, "record was not delivered");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    s.worker.unwrap().stop();
}

#[tokio::test]
async fn describes_itself() {
    let dir = tempfile::tempdir().unwrap();
    let s = server(dir.path(), "", false);
    let (status, doc, _) = call(&s.router, "GET", "/v1/openapi.json", None, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(doc["openapi"], "3.1.0");
    assert!(doc["paths"]["/v1/events"]["post"].is_object());
    let (status, ..) = call(&s.router, "GET", "/v1/schemas/record", None, None).await;
    assert_eq!(status, StatusCode::OK);
    let (status, ..) = call(&s.router, "GET", "/v1/schemas/nope", None, None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, body, _) = call(&s.router, "GET", "/v1/health", None, None).await;
    assert_eq!(
        (status, body["status"].as_str()),
        (StatusCode::OK, Some("ok"))
    );
}

#[test]
fn serving_requires_tokens() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("core-memory.toml");
    std::fs::write(
        &path,
        "[store]\npath = \"s.sqlite\"\n[grants]\nscopes = [\"acme\"]\n[[sink]]\nid = \"r\"\nkind = \"jsonl\"\npath = \"r.jsonl\"\n",
    )
    .unwrap();
    let err = prepare(&Loaded::read(&path).unwrap(), false).unwrap_err();
    assert!(err.contains("[http]"), "{err}");
}

//! Delivers records to an HTTP endpoint the host runs: a webhook, or the
//! host callback of a sidecar deployment (REC-09).
//!
//! # Protocol
//!
//! Each sink write is one `POST` to the configured URL:
//!
//! ```text
//! POST /core-memory HTTP/1.1
//! Content-Type: application/json
//! Core-Memory-Signature: t=1793072000,v1=5f2b…
//!
//! {"contract":"core_memory.record.v1","sink":"host","records":[{…},{…}]}
//! ```
//!
//! - `records` holds the engine's records byte for byte, in delivery order.
//! - The signature is HMAC-SHA256 keyed by the shared secret, over
//!   `<t>.<body>`, where `t` is the send time in Unix seconds. Receivers
//!   check it with [`verify`], or the same computation in their own
//!   language, and refuse old timestamps so a captured request cannot be
//!   replayed later. `v1` may appear more than once while a secret is
//!   being rotated; one match is enough.
//! - Delivery is at least once: after a failure, the same records come
//!   again. Receivers deduplicate by `record_key`.
//!
//! The receiver answers with one of:
//!
//! | Response | Meaning |
//! |---|---|
//! | `2xx` with `{"results": [...]}` | One result per record, in order. |
//! | Any other `2xx`, such as `204` | Every record is accepted. |
//! | Anything else | Nothing in the batch counts as written. It is sent again later, after `Retry-After` if the response has one. |
//!
//! A result is one of `{"outcome": "accepted"}`,
//! `{"outcome": "already_accepted"}`,
//! `{"outcome": "conflict", "stored": <the record it holds>}`,
//! `{"outcome": "rejected", "reason": "..."}` or
//! `{"outcome": "retryable", "retry_after_ms": 1000}`. `conflict` and
//! `rejected` are final for that record.

use std::fmt;
use std::sync::Arc;
use std::time::Duration;

use core_memory_contract::hash::to_hex;
use core_memory_contract::{RECORD_CONTRACT_V1, Sha256Digest};
use core_memory_core::Clock;
use core_memory_core::sink::{Sink, SinkCaps, SinkError, WriteOutcome};
use core_memory_core::store::OutboxEntry;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use sha2::Sha256;
use ureq::Agent;

/// The request header that carries the signature.
pub const SIGNATURE_HEADER: &str = "Core-Memory-Signature";

/// The shortest secret the sink accepts.
pub const MIN_SECRET_BYTES: usize = 16;

/// Retry delay when a failed response gives none.
const DEFAULT_RETRY_MS: u64 = 5_000;

/// Most of an error response kept in a delivery error.
const ERROR_BODY_CHARS: usize = 300;

#[derive(Clone)]
pub struct WebhookConfig {
    pub url: String,
    /// The shared signing secret.
    pub secret: Vec<u8>,
    /// Limit for one whole request, from connecting to reading the reply.
    pub timeout: Duration,
    pub max_record_bytes: u64,
    /// The receiver keeps the first record written under each key and
    /// answers a different one with `conflict` (REC-07).
    pub system_of_record: bool,
}

impl fmt::Debug for WebhookConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WebhookConfig")
            .field("url", &self.url)
            .field("secret", &"<redacted>")
            .field("timeout", &self.timeout)
            .field("max_record_bytes", &self.max_record_bytes)
            .field("system_of_record", &self.system_of_record)
            .finish()
    }
}

#[derive(Debug)]
pub struct WebhookSink {
    id: String,
    config: WebhookConfig,
    agent: Agent,
    clock: Arc<dyn Clock>,
}

impl WebhookSink {
    /// `clock` supplies the signature timestamps.
    pub fn new(
        id: impl Into<String>,
        config: WebhookConfig,
        clock: Arc<dyn Clock>,
    ) -> Result<Self, String> {
        let id = id.into();
        if !(config.url.starts_with("https://") || config.url.starts_with("http://")) {
            return Err(format!(
                "sink {id}: url must start with https:// or http://"
            ));
        }
        if config.secret.len() < MIN_SECRET_BYTES {
            return Err(format!(
                "sink {id}: the signing secret must be at least {MIN_SECRET_BYTES} bytes"
            ));
        }
        let agent = Agent::new_with_config(
            Agent::config_builder()
                .timeout_global(Some(config.timeout))
                .http_status_as_error(false)
                .build(),
        );
        Ok(Self {
            id,
            config,
            agent,
            clock,
        })
    }

    fn error(&self, message: String, retry_after_ms: u64) -> SinkError {
        SinkError {
            sink: self.id.clone(),
            message,
            retry_after_ms,
        }
    }
}

/// The request body for `batch`: the records verbatim inside the envelope.
pub fn body(sink_id: &str, batch: &[OutboxEntry]) -> String {
    let id = serde_json::Value::String(sink_id.to_string());
    let mut body = format!("{{\"contract\":\"{RECORD_CONTRACT_V1}\",\"sink\":{id},\"records\":[");
    for (i, entry) in batch.iter().enumerate() {
        if i > 0 {
            body.push(',');
        }
        body.push_str(&entry.record_json);
    }
    body.push_str("]}");
    body
}

fn mac(secret: &[u8], unix_seconds: i64, body: &[u8]) -> Hmac<Sha256> {
    let mut mac = match <Hmac<Sha256> as KeyInit>::new_from_slice(secret) {
        Ok(mac) => mac,
        Err(_) => unreachable!("HMAC takes keys of any length"),
    };
    mac.update(unix_seconds.to_string().as_bytes());
    mac.update(b".");
    mac.update(body);
    mac
}

/// The signature header value for `body` sent at `unix_seconds`.
pub fn sign(secret: &[u8], unix_seconds: i64, body: &[u8]) -> String {
    let digest = mac(secret, unix_seconds, body).finalize().into_bytes();
    format!("t={unix_seconds},v1={}", to_hex(&digest))
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum SignatureError {
    #[error("the signature header is malformed")]
    Malformed,
    #[error("the signature is {0} s from now, outside the tolerance")]
    Stale(u64),
    #[error("the signature does not match the body")]
    Mismatch,
}

/// Checks a request's signature header against its body, in constant time.
/// Refuses a timestamp more than `tolerance_s` seconds from `now_unix_s`.
pub fn verify(
    secret: &[u8],
    header: &str,
    body: &[u8],
    now_unix_s: i64,
    tolerance_s: u64,
) -> Result<(), SignatureError> {
    let mut t = None;
    let mut signatures = Vec::new();
    for part in header.split(',') {
        match part.trim().split_once('=') {
            Some(("t", value)) => t = value.parse::<i64>().ok(),
            Some(("v1", value)) => signatures.push(
                value
                    .parse::<Sha256Digest>()
                    .map_err(|_| SignatureError::Malformed)?,
            ),
            _ => {}
        }
    }
    let Some(t) = t else {
        return Err(SignatureError::Malformed);
    };
    if signatures.is_empty() {
        return Err(SignatureError::Malformed);
    }
    let skew = now_unix_s.abs_diff(t);
    if skew > tolerance_s {
        return Err(SignatureError::Stale(skew));
    }
    let expected = mac(secret, t, body);
    if signatures
        .iter()
        .any(|s| expected.clone().verify_slice(s.as_bytes()).is_ok())
    {
        Ok(())
    } else {
        Err(SignatureError::Mismatch)
    }
}

#[derive(Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
enum Answer {
    Accepted,
    AlreadyAccepted,
    Conflict { stored: serde_json::Value },
    Rejected { reason: String },
    Retryable { retry_after_ms: u64 },
}

impl From<Answer> for WriteOutcome {
    fn from(r: Answer) -> Self {
        match r {
            Answer::Accepted => WriteOutcome::Accepted,
            Answer::AlreadyAccepted => WriteOutcome::AlreadyAccepted,
            Answer::Conflict { stored } => WriteOutcome::Conflict {
                stored: stored.to_string(),
            },
            Answer::Rejected { reason } => WriteOutcome::Rejected { reason },
            Answer::Retryable { retry_after_ms } => WriteOutcome::Retryable { retry_after_ms },
        }
    }
}

impl Sink for WebhookSink {
    fn id(&self) -> &str {
        &self.id
    }

    fn caps(&self) -> SinkCaps {
        SinkCaps {
            system_of_record: self.config.system_of_record,
            ..SinkCaps::accepts_all_v1(self.config.max_record_bytes)
        }
    }

    fn write(&mut self, batch: &[OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError> {
        let body = body(&self.id, batch);
        let t = self.clock.now().unix_millis().div_euclid(1000);
        let signature = sign(&self.config.secret, t, body.as_bytes());
        let mut response = self
            .agent
            .post(&self.config.url)
            .header("content-type", "application/json")
            .header(SIGNATURE_HEADER, &signature)
            .send(&body)
            .map_err(|e| self.error(format!("{}: {e}", self.config.url), DEFAULT_RETRY_MS))?;
        let status = response.status().as_u16();
        let retry_after_ms = response
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.trim().parse::<u64>().ok())
            .map(|seconds| seconds.saturating_mul(1000));
        let text = response
            .body_mut()
            .read_to_string()
            .map_err(|e| self.error(format!("reading the reply: {e}"), DEFAULT_RETRY_MS))?;
        if !(200..300).contains(&status) {
            let snippet: String = text.chars().take(ERROR_BODY_CHARS).collect();
            return Err(self.error(
                format!("the receiver answered {status}: {snippet}"),
                retry_after_ms.unwrap_or(DEFAULT_RETRY_MS),
            ));
        }
        let reply: Option<serde_json::Value> = serde_json::from_str(&text).ok();
        let Some(results) = reply.as_ref().and_then(|r| r.get("results")) else {
            return Ok(vec![WriteOutcome::Accepted; batch.len()]);
        };
        // The receiver answered per record, so a malformed answer cannot be
        // read as acceptance.
        let results: Vec<Answer> = serde_json::from_value(results.clone())
            .map_err(|e| self.error(format!("unreadable results: {e}"), DEFAULT_RETRY_MS))?;
        if results.len() != batch.len() {
            return Err(self.error(
                format!(
                    "the receiver answered {} results for {} records",
                    results.len(),
                    batch.len()
                ),
                DEFAULT_RETRY_MS,
            ));
        }
        Ok(results.into_iter().map(Into::into).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SECRET: &[u8] = b"0123456789abcdef0123456789abcdef";

    #[test]
    fn signatures_verify_and_tampering_does_not() {
        let header = sign(SECRET, 1_793_072_000, b"{\"records\":[]}");
        assert_eq!(
            verify(SECRET, &header, b"{\"records\":[]}", 1_793_072_100, 300),
            Ok(())
        );
        assert_eq!(
            verify(SECRET, &header, b"{\"records\":[1]}", 1_793_072_000, 300),
            Err(SignatureError::Mismatch)
        );
        assert_eq!(
            verify(
                b"another secret, 32 bytes long..",
                &header,
                b"{\"records\":[]}",
                1_793_072_000,
                300
            ),
            Err(SignatureError::Mismatch)
        );
        assert_eq!(
            verify(SECRET, &header, b"{\"records\":[]}", 1_793_073_000, 300),
            Err(SignatureError::Stale(1000))
        );
        for bad in ["", "t=1", "v1=00", "t=x,v1=00", "t=1,v1=zz"] {
            assert_eq!(
                verify(SECRET, bad, b"", 1, 300),
                Err(SignatureError::Malformed),
                "{bad}"
            );
        }
    }

    #[test]
    fn any_listed_signature_may_match() {
        let old = sign(b"the old secret, rotated out soon", 100, b"x");
        let new = sign(SECRET, 100, b"x");
        let both = format!("{new},{}", old.split_once(',').unwrap().1);
        assert_eq!(
            verify(b"the old secret, rotated out soon", &both, b"x", 100, 0),
            Ok(())
        );
        assert_eq!(verify(SECRET, &both, b"x", 100, 0), Ok(()));
    }

    #[test]
    fn the_body_carries_records_verbatim() {
        let batch: Vec<_> = (1..=2)
            .map(|seq| OutboxEntry {
                seq,
                record_key: format!("k{seq}"),
                record_json: format!("{{\"record_key\":\"k{seq}\",\"n\": {seq}}}"),
            })
            .collect();
        assert_eq!(
            body("host \"a\"", &batch),
            "{\"contract\":\"core_memory.record.v1\",\"sink\":\"host \\\"a\\\"\",\"records\":[{\"record_key\":\"k1\",\"n\": 1},{\"record_key\":\"k2\",\"n\": 2}]}"
        );
        assert_eq!(
            body("host", &[]),
            "{\"contract\":\"core_memory.record.v1\",\"sink\":\"host\",\"records\":[]}"
        );
    }

    #[test]
    fn the_secret_never_prints() {
        let config = WebhookConfig {
            url: "https://example.com/hook".into(),
            secret: SECRET.to_vec(),
            timeout: Duration::from_secs(5),
            max_record_bytes: 1 << 20,
            system_of_record: false,
        };
        assert!(!format!("{config:?}").contains("0123456789abcdef"));
        let short = WebhookConfig {
            secret: b"short".to_vec(),
            ..config.clone()
        };
        let clock: Arc<dyn Clock> = Arc::new(core_memory_core::SystemClock);
        assert!(WebhookSink::new("host", short, clock.clone()).is_err());
        let ftp = WebhookConfig {
            url: "ftp://example.com".into(),
            ..config
        };
        assert!(WebhookSink::new("host", ftp, clock).is_err());
    }
}

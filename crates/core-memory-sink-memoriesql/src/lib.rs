//! Delivers records to memoriesQL, the system of record, through
//! `memoriesql.accept_engine_records_v1` (memoriesQL schema 55).
//!
//! The sink connects to memoriesQL's PostgreSQL database and authenticates
//! the way every memoriesQL client does: inside each transaction it takes
//! the `memoriesql_application` role and opens an authorization context with
//! its paired-client credential. memoriesQL, not the engine, decides which
//! engine scopes it may deliver to. Each engine scope must be enrolled there
//! as an exact source (`source_system = "core-memory"`,
//! `object_kind = "engine_scope"`) and granted to this credential.
//!
//! memoriesQL keeps the first record written under a key and answers a
//! different one with `conflict` and the stored record, so the sink declares
//! itself a system of record (REC-07).
//!
//! Delivery is strictly in order. A record for a scope memoriesQL has not
//! enrolled, or not granted to this credential, comes back `retryable` with
//! memoriesQL's reason, which names the scope, and holds back every later
//! record until an operator enrolls or grants it.

use std::fmt;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use core_memory_contract::Sha256Digest;
use core_memory_core::sink::{Sink, SinkCaps, SinkError, WriteOutcome};
use core_memory_core::store::OutboxEntry;
use postgres::Client;
use rustls::pki_types::CertificateDer;
use rustls::pki_types::pem::PemObject;
use serde::Deserialize;
use tokio_postgres_rustls::MakeRustlsConnect;

/// Records per call, memoriesQL's batch limit.
pub const MAX_BATCH_RECORDS: usize = 256;

/// Request bytes per call, under memoriesQL's 16 MiB limit.
const MAX_REQUEST_BYTES: usize = 15 << 20;

/// The largest record the sink can send at all.
pub const MAX_RECORD_BYTES: u64 = (MAX_REQUEST_BYTES - 1024) as u64;

/// How long an idle connection is probed before it is dropped as dead.
const KEEPALIVE_INTERVAL: Duration = Duration::from_secs(1);
const KEEPALIVE_RETRIES: u32 = 3;

const RETRY_SOON_MS: u64 = 1_000;
const RETRY_MS: u64 = 5_000;
const RETRY_CONFIG_MS: u64 = 60_000;

#[derive(Clone)]
pub struct MemoriesqlConfig {
    /// A libpq-style connection string or URL for memoriesQL's database.
    pub url: String,
    /// The paired-client secret memoriesQL issued to this engine. Only its
    /// SHA-256 is ever sent.
    pub credential: String,
    pub workspace_id: String,
    pub max_record_bytes: u64,
    /// Limit for one call, from connecting to the reply. A connection that
    /// goes silent is dropped a few seconds after it.
    pub timeout: Duration,
    /// PEM certificates to trust as well as the public web roots, for a
    /// server whose certificate comes from a private or self-signed CA.
    pub ca_file: Option<PathBuf>,
}

impl fmt::Debug for MemoriesqlConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MemoriesqlConfig")
            .field("url", &"<redacted>")
            .field("credential", &"<redacted>")
            .field("workspace_id", &self.workspace_id)
            .field("max_record_bytes", &self.max_record_bytes)
            .field("timeout", &self.timeout)
            .field("ca_file", &self.ca_file)
            .finish()
    }
}

pub struct MemoriesqlSink {
    id: String,
    config: postgres::Config,
    credential_sha256: String,
    workspace_id: String,
    max_record_bytes: u64,
    timeout: Duration,
    tls: MakeRustlsConnect,
    client: Option<Client>,
}

impl fmt::Debug for MemoriesqlSink {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MemoriesqlSink")
            .field("id", &self.id)
            .field("workspace_id", &self.workspace_id)
            .field("max_record_bytes", &self.max_record_bytes)
            .field("connected", &self.client.is_some())
            .finish()
    }
}

#[derive(Deserialize)]
struct Reply {
    results: Vec<Answer>,
}

#[derive(Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
enum Answer {
    Accepted,
    AlreadyAccepted,
    /// memoriesQL shows the stored record only to a caller that may read
    /// the scope.
    Conflict {
        #[serde(default)]
        stored: Option<serde_json::Value>,
    },
    Rejected {
        reason: String,
    },
    Retryable {
        retry_after_ms: u64,
        /// memoriesQL names the engine scope it cannot take records for.
        #[serde(default)]
        reason: Option<String>,
    },
}

impl From<Answer> for WriteOutcome {
    fn from(a: Answer) -> Self {
        match a {
            Answer::Accepted => WriteOutcome::Accepted,
            Answer::AlreadyAccepted => WriteOutcome::AlreadyAccepted,
            Answer::Conflict { stored } => WriteOutcome::Conflict {
                stored: stored.map(|record| record.to_string()),
            },
            Answer::Rejected { reason } => WriteOutcome::Rejected { reason },
            Answer::Retryable {
                retry_after_ms,
                reason,
            } => WriteOutcome::Retryable {
                retry_after_ms,
                reason,
            },
        }
    }
}

impl MemoriesqlSink {
    /// Validates the settings without connecting; the first write connects.
    pub fn new(id: impl Into<String>, config: MemoriesqlConfig) -> Result<Self, String> {
        let id = id.into();
        let mut pg: postgres::Config = config
            .url
            .parse()
            .map_err(|e| format!("sink {id}: the memoriesQL url does not parse: {e}"))?;
        // statement_timeout bounds the server; the client's own reads need
        // keepalives, or a silent network would block delivery for good.
        pg.connect_timeout(config.timeout)
            .keepalives(true)
            .keepalives_idle(config.timeout)
            .keepalives_interval(KEEPALIVE_INTERVAL)
            .keepalives_retries(KEEPALIVE_RETRIES)
            .tcp_user_timeout(config.timeout + KEEPALIVE_INTERVAL * KEEPALIVE_RETRIES);
        if config.credential.trim().is_empty() {
            return Err(format!("sink {id}: the memoriesQL credential is empty"));
        }
        let workspace_id = uuid::Uuid::try_parse(&config.workspace_id)
            .map_err(|_| format!("sink {id}: workspace_id must be a UUID"))?
            .hyphenated()
            .to_string();
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let mut roots = rustls::RootCertStore {
            roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
        };
        if let Some(path) = &config.ca_file {
            let pem = std::fs::read(path)
                .map_err(|e| format!("sink {id}: ca_file {}: {e}", path.display()))?;
            let certs = CertificateDer::pem_slice_iter(&pem)
                .collect::<Result<Vec<_>, _>>()
                .map_err(|e| format!("sink {id}: ca_file {}: {e}", path.display()))?;
            if certs.is_empty() {
                return Err(format!(
                    "sink {id}: ca_file {} holds no certificate",
                    path.display()
                ));
            }
            for cert in certs {
                roots
                    .add(cert)
                    .map_err(|e| format!("sink {id}: ca_file {}: {e}", path.display()))?;
            }
        }
        let tls = rustls::ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(|e| format!("sink {id}: TLS setup: {e}"))?
            .with_root_certificates(roots)
            .with_no_client_auth();
        Ok(Self {
            id,
            config: pg,
            credential_sha256: Sha256Digest::of(config.credential.as_bytes()).to_hex(),
            workspace_id,
            max_record_bytes: config.max_record_bytes.min(MAX_RECORD_BYTES),
            timeout: config.timeout,
            tls: MakeRustlsConnect::new(tls),
            client: None,
        })
    }

    fn error(&self, message: String, retry_after_ms: u64) -> SinkError {
        SinkError {
            sink: self.id.clone(),
            message,
            retry_after_ms,
        }
    }

    fn client(&mut self) -> Result<&mut Client, SinkError> {
        if self.client.as_ref().is_some_and(Client::is_closed) {
            self.client = None;
        }
        let client = match self.client.take() {
            Some(client) => client,
            None => self
                .config
                .connect(self.tls.clone())
                .map_err(|e| self.error(format!("connecting to memoriesQL: {e}"), RETRY_MS))?,
        };
        Ok(self.client.insert(client))
    }

    /// Runs `query` with one text parameter in an authorized transaction and
    /// returns its single text result.
    fn call(&mut self, query: &str, param: Option<&str>) -> Result<String, SinkError> {
        let timeout_ms = self.timeout.as_millis().clamp(1, u128::from(u32::MAX));
        let credential = self.credential_sha256.clone();
        let workspace = self.workspace_id.clone();
        let client = self.client()?;
        let result = (|| {
            let mut tx = client.transaction()?;
            tx.batch_execute(&format!(
                "SET LOCAL statement_timeout = {timeout_ms}; \
                 SET LOCAL lock_timeout = 500; \
                 SET LOCAL ROLE memoriesql_application"
            ))?;
            tx.execute(
                "SELECT 1 FROM memoriesql.begin_authorization_context($1, $2::text::uuid)",
                &[&credential, &workspace],
            )?;
            let row = match param {
                Some(p) => tx.query_one(query, &[&p])?,
                None => tx.query_one(query, &[])?,
            };
            let text: String = row.try_get(0)?;
            tx.commit()?;
            Ok::<_, postgres::Error>(text)
        })();
        result.map_err(|e| {
            let (message, retry) = describe(&e);
            if e.as_db_error().is_none() {
                self.client = None;
            }
            self.error(message, retry)
        })
    }

    fn send(&mut self, group: &[&OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError> {
        let mut body =
            String::from("{\"contract_version\":1,\"expected_schema_version\":55,\"records\":[");
        for (i, entry) in group.iter().enumerate() {
            if i > 0 {
                body.push(',');
            }
            body.push_str(&entry.record_json);
        }
        body.push_str("]}");
        let text = self.call(
            "SELECT memoriesql.accept_engine_records_v1($1::text::jsonb)::text",
            Some(&body),
        )?;
        let reply: Reply = serde_json::from_str(&text)
            .map_err(|e| self.error(format!("unreadable memoriesQL reply: {e}"), RETRY_MS))?;
        if reply.results.len() != group.len() {
            return Err(self.error(
                format!(
                    "memoriesQL answered {} results for {} records",
                    reply.results.len(),
                    group.len()
                ),
                RETRY_MS,
            ));
        }
        Ok(reply.results.into_iter().map(Into::into).collect())
    }

    /// The governed bead types as an engine vocabulary, as JSON
    /// (`{"id": "memoriesql.bead_types", "types": [...]}`).
    pub fn vocabulary(&mut self) -> Result<String, SinkError> {
        self.call("SELECT memoriesql.engine_vocabulary_v1()::text", None)
    }
}

/// PostgreSQL cannot store U+0000 in text or jsonb. JSON writes it as the
/// escape `\u0000`. When an odd number of backslashes precede it, its `\`
/// is itself escaped and the text is a literal backslash and `u0000`.
fn unstorable(entry: &OutboxEntry) -> bool {
    let json = entry.record_json.as_bytes();
    json.windows(6).enumerate().any(|(i, w)| {
        w == b"\\u0000" && json[..i].iter().rev().take_while(|&&b| b == b'\\').count() % 2 == 0
    })
}

fn describe(e: &postgres::Error) -> (String, u64) {
    let Some(db) = e.as_db_error() else {
        return (format!("memoriesQL: {e}"), RETRY_MS);
    };
    let retry = match db.code().code() {
        // Lock timeout, cancelled statement, deadlock, serialization.
        "55P03" | "57014" | "40P01" | "40001" => RETRY_SOON_MS,
        // The credential, workspace, schema or request needs an operator.
        "42501" | "22023" | "42883" | "3F000" => RETRY_CONFIG_MS,
        _ => RETRY_MS,
    };
    (
        format!("memoriesQL {}: {}", db.code().code(), db.message()),
        retry,
    )
}

impl Sink for MemoriesqlSink {
    fn id(&self) -> &str {
        &self.id
    }

    fn caps(&self) -> SinkCaps {
        SinkCaps {
            system_of_record: true,
            ..SinkCaps::accepts_all_v1(self.max_record_bytes)
        }
    }

    fn write(&mut self, batch: &[OutboxEntry]) -> Result<Vec<WriteOutcome>, SinkError> {
        let mut outcomes: Vec<Option<WriteOutcome>> = vec![None; batch.len()];
        let mut next = 0;
        while next < batch.len() {
            let mut group = Vec::new();
            let mut bytes = 0;
            while next < batch.len() && group.len() < MAX_BATCH_RECORDS {
                let entry = &batch[next];
                if unstorable(entry) {
                    outcomes[next] = Some(WriteOutcome::Rejected {
                        reason: "the record contains U+0000, which memoriesQL cannot store".into(),
                    });
                    next += 1;
                    continue;
                }
                if !group.is_empty() && bytes + entry.record_json.len() > MAX_REQUEST_BYTES {
                    break;
                }
                bytes += entry.record_json.len() + 1;
                group.push(next);
                next += 1;
            }
            if group.is_empty() {
                continue;
            }
            let entries: Vec<&OutboxEntry> = group.iter().map(|&i| &batch[i]).collect();
            let answers = self.send(&entries)?;
            let mut retry = None;
            for (&i, answer) in group.iter().zip(answers) {
                if retry.is_none() && matches!(answer, WriteOutcome::Retryable { .. }) {
                    retry = Some(answer.clone());
                }
                outcomes[i] = Some(answer);
            }
            // The engine stops at the first retryable record, so nothing
            // after it needs sending now.
            if let Some(retry) = retry {
                for o in outcomes.iter_mut().filter(|o| o.is_none()) {
                    *o = Some(retry.clone());
                }
                break;
            }
        }
        Ok(outcomes
            .into_iter()
            .map(|o| {
                o.unwrap_or(WriteOutcome::Retryable {
                    retry_after_ms: RETRY_MS,
                    reason: None,
                })
            })
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> MemoriesqlConfig {
        MemoriesqlConfig {
            url: "postgresql://engine:hunter2@db.example.com/memoriesql".into(),
            credential: "a paired engine credential".into(),
            workspace_id: "01a17995-de2f-770f-97ac-c63d1b24fc20".into(),
            max_record_bytes: 1 << 40,
            timeout: Duration::from_secs(10),
            ca_file: None,
        }
    }

    fn entry(record_json: &str) -> OutboxEntry {
        OutboxEntry {
            seq: 1,
            record_key: "chunk:1".into(),
            record_json: record_json.into(),
        }
    }

    #[test]
    fn only_a_real_nul_is_unstorable() {
        // A NUL character, an escaped backslash then a NUL, and text that
        // only reads like the escape.
        let nul = serde_json::json!({"text": "a\u{0}b"}).to_string();
        let both = serde_json::json!({"text": "a\\\u{0}b"}).to_string();
        let literal = serde_json::json!({"text": r#"printf("\u0000")"#}).to_string();
        let doubled = serde_json::json!({"text": r"\\u0000"}).to_string();
        assert!(unstorable(&entry(&nul)), "{nul}");
        assert!(unstorable(&entry(&both)), "{both}");
        assert!(!unstorable(&entry(&literal)), "{literal}");
        assert!(!unstorable(&entry(&doubled)), "{doubled}");
    }

    #[test]
    fn a_silent_connection_is_dropped_after_the_timeout() {
        let sink = MemoriesqlSink::new("memoriesql", config()).unwrap();
        assert!(sink.config.get_keepalives());
        assert_eq!(sink.config.get_keepalives_idle(), Duration::from_secs(10));
        assert_eq!(
            sink.config.get_tcp_user_timeout(),
            Some(&Duration::from_secs(13))
        );
    }

    #[test]
    fn the_workspace_id_is_sent_in_canonical_form() {
        let sink = MemoriesqlSink::new(
            "memoriesql",
            MemoriesqlConfig {
                workspace_id: "01A17995DE2F770F97ACC63D1B24FC20".into(),
                ..config()
            },
        )
        .unwrap();
        assert_eq!(sink.workspace_id, "01a17995-de2f-770f-97ac-c63d1b24fc20");
    }

    #[test]
    fn a_private_ca_is_trusted_from_a_pem_file() {
        let fixtures = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
        let with_ca = MemoriesqlConfig {
            ca_file: Some(fixtures.join("fictional-ca.pem")),
            ..config()
        };
        assert!(MemoriesqlSink::new("memoriesql", with_ca).is_ok());
        for bad in [
            fixtures.join("missing.pem"),
            fixtures.join("..").join("conformance.rs"),
        ] {
            let err = MemoriesqlSink::new(
                "memoriesql",
                MemoriesqlConfig {
                    ca_file: Some(bad),
                    ..config()
                },
            )
            .unwrap_err();
            assert!(err.contains("ca_file"), "{err}");
        }
    }

    #[test]
    fn settings_are_checked_without_connecting() {
        let sink = MemoriesqlSink::new("memoriesql", config()).unwrap();
        assert!(sink.caps().system_of_record);
        assert_eq!(sink.caps().max_record_bytes, MAX_RECORD_BYTES);
        for bad in [
            MemoriesqlConfig {
                url: "postgresql://[".into(),
                ..config()
            },
            MemoriesqlConfig {
                credential: " ".into(),
                ..config()
            },
            MemoriesqlConfig {
                workspace_id: "acme".into(),
                ..config()
            },
        ] {
            assert!(MemoriesqlSink::new("memoriesql", bad).is_err());
        }
    }

    #[test]
    fn secrets_never_print() {
        let printed = format!(
            "{:?} {:?}",
            config(),
            MemoriesqlSink::new("m", config()).unwrap()
        );
        assert!(!printed.contains("hunter2") && !printed.contains("paired engine credential"));
    }

    #[test]
    fn a_conflict_without_the_stored_record_is_still_a_conflict() {
        let reply: Reply = serde_json::from_str(
            r#"{"results":[{"outcome":"conflict"},{"outcome":"conflict","stored":{"a":1}}]}"#,
        )
        .unwrap();
        let outcomes: Vec<WriteOutcome> = reply.results.into_iter().map(Into::into).collect();
        assert_eq!(
            outcomes,
            vec![
                WriteOutcome::Conflict { stored: None },
                WriteOutcome::Conflict {
                    stored: Some(r#"{"a":1}"#.into())
                },
            ]
        );
    }

    #[test]
    fn records_postgres_cannot_hold_are_refused_before_sending() {
        let mut sink = MemoriesqlSink::new("memoriesql", config()).unwrap();
        let entry = OutboxEntry {
            seq: 1,
            record_key: "chunk:x".into(),
            record_json: r#"{"text":"a\u0000b"}"#.into(),
        };
        // No connection is attempted: the only record never needs sending.
        assert_eq!(
            sink.write(&[entry]).unwrap(),
            vec![WriteOutcome::Rejected {
                reason: "the record contains U+0000, which memoriesQL cannot store".into()
            }]
        );
    }
}

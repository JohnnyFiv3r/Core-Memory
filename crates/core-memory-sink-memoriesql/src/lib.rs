//! Delivers records to memoriesQL, the system of record, through
//! `memoriesql.accept_engine_records_v1` (memoriesQL schema 54).
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

use std::fmt;
use std::sync::Arc;
use std::time::Duration;

use core_memory_contract::Sha256Digest;
use core_memory_core::sink::{Sink, SinkCaps, SinkError, WriteOutcome};
use core_memory_core::store::OutboxEntry;
use postgres::Client;
use serde::Deserialize;
use tokio_postgres_rustls::MakeRustlsConnect;

/// Records per call, memoriesQL's batch limit.
pub const MAX_BATCH_RECORDS: usize = 256;

/// Request bytes per call, under memoriesQL's 16 MiB limit.
const MAX_REQUEST_BYTES: usize = 15 << 20;

/// The largest record the sink can send at all.
pub const MAX_RECORD_BYTES: u64 = (MAX_REQUEST_BYTES - 1024) as u64;

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
    /// Limit for one call, from connecting to the reply.
    pub timeout: Duration,
}

impl fmt::Debug for MemoriesqlConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("MemoriesqlConfig")
            .field("url", &"<redacted>")
            .field("credential", &"<redacted>")
            .field("workspace_id", &self.workspace_id)
            .field("max_record_bytes", &self.max_record_bytes)
            .field("timeout", &self.timeout)
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
    Conflict { stored: serde_json::Value },
    Rejected { reason: String },
    Retryable { retry_after_ms: u64 },
}

impl From<Answer> for WriteOutcome {
    fn from(a: Answer) -> Self {
        match a {
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

impl MemoriesqlSink {
    /// Validates the settings without connecting; the first write connects.
    pub fn new(id: impl Into<String>, config: MemoriesqlConfig) -> Result<Self, String> {
        let id = id.into();
        let mut pg: postgres::Config = config
            .url
            .parse()
            .map_err(|e| format!("sink {id}: the memoriesQL url does not parse: {e}"))?;
        pg.connect_timeout(config.timeout);
        if config.credential.trim().is_empty() {
            return Err(format!("sink {id}: the memoriesQL credential is empty"));
        }
        if !is_uuid(&config.workspace_id) {
            return Err(format!("sink {id}: workspace_id must be a UUID"));
        }
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let roots = rustls::RootCertStore {
            roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
        };
        let tls = rustls::ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(|e| format!("sink {id}: TLS setup: {e}"))?
            .with_root_certificates(roots)
            .with_no_client_auth();
        Ok(Self {
            id,
            config: pg,
            credential_sha256: Sha256Digest::of(config.credential.as_bytes()).to_hex(),
            workspace_id: config.workspace_id,
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
            String::from("{\"contract_version\":1,\"expected_schema_version\":54,\"records\":[");
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

/// PostgreSQL cannot store U+0000 in text or jsonb.
fn unstorable(entry: &OutboxEntry) -> bool {
    entry.record_json.contains("\\u0000")
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

fn is_uuid(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 36
        && b.iter().enumerate().all(|(i, c)| match i {
            8 | 13 | 18 | 23 => *c == b'-',
            _ => c.is_ascii_hexdigit(),
        })
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

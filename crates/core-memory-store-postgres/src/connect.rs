//! Connecting to PostgreSQL.
//!
//! A connection to another machine always uses TLS, unless the URL says
//! `sslmode=disable`. The server's certificate and host name are checked
//! against the public web roots plus an optional private CA, and a failed
//! handshake is never retried without TLS. Only a connection to this
//! machine (a loopback address or a Unix socket) may fall back to plain
//! text when the server offers no TLS.

use std::fmt;
use std::net::IpAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU8, AtomicUsize, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::time::Duration;

use core_memory_core::StoreError;
use postgres::Client;

use crate::error::{describe, is_transient, source_chain};
use postgres::config::{Host, SslMode};
use rustls::pki_types::CertificateDer;
use rustls::pki_types::pem::PemObject;
use tokio_postgres_rustls::MakeRustlsConnect;

/// How long a connection may be silent before the client starts probing
/// it, how often it probes, and how many unanswered probes mean it is dead.
const KEEPALIVE_IDLE: Duration = Duration::from_secs(30);
const KEEPALIVE_INTERVAL: Duration = Duration::from_secs(1);
const KEEPALIVE_RETRIES: u32 = 3;
/// The longest statement timeout accepted. A statement that needs longer
/// than this is a migration, which sets its own limits.
pub const MAX_STATEMENT_TIMEOUT: Duration = Duration::from_secs(3_600);
/// The longest wait accepted for opening a connection.
pub const MAX_CONNECT_TIMEOUT: Duration = Duration::from_secs(300);
/// How long a statement may wait for a row or table lock.
const LOCK_TIMEOUT_MS: u64 = 5_000;
/// How long a transaction may sit idle before the server ends it. A stalled
/// engine would otherwise keep its workspace's writers waiting.
const IDLE_IN_TRANSACTION_TIMEOUT_MS: u64 = 15_000;
/// How many times `connect_timeout` one whole attempt to connect gets for
/// each host named. The driver spends up to one `connect_timeout` on each
/// address a name resolves to, and a name with a dead address ahead of a
/// good one must still be reached.
const ATTEMPT_ALLOWANCE: u32 = 3;
/// How many attempts to connect may have been given up on and still be
/// running. See [`Connector::connect`].
const MAX_ABANDONED_ATTEMPTS: usize = 8;

/// The states of one attempt to connect.
const RUNNING: u8 = 0;
const ABANDONED: u8 = 1;
const ENDED: u8 = 2;

/// Where the store is and how to reach it.
#[derive(Clone)]
pub struct ConnectConfig {
    /// A libpq-style connection string or URL. It may carry a password.
    pub url: String,
    /// PEM certificates to trust as well as the public web roots, for a
    /// server whose certificate comes from a private or self-signed CA.
    pub ca_file: Option<PathBuf>,
    /// Limit for opening a connection.
    pub connect_timeout: Duration,
    /// Limit for one statement. A connection that goes silent is dropped a
    /// few seconds after it.
    pub statement_timeout: Duration,
}

impl ConnectConfig {
    pub fn new(url: impl Into<String>) -> Self {
        Self {
            url: url.into(),
            ca_file: None,
            connect_timeout: Duration::from_secs(10),
            statement_timeout: Duration::from_secs(30),
        }
    }
}

impl fmt::Debug for ConnectConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ConnectConfig")
            .field("url", &"<redacted>")
            .field("ca_file", &self.ca_file)
            .field("connect_timeout", &self.connect_timeout)
            .field("statement_timeout", &self.statement_timeout)
            .finish()
    }
}

/// A validated [`ConnectConfig`], ready to open connections.
#[derive(Clone)]
pub struct Connector {
    config: postgres::Config,
    tls: MakeRustlsConnect,
    statement_timeout_ms: u128,
    /// The limit for one whole attempt, from resolving the server's name
    /// to a session with its limits set.
    attempt_limit: Duration,
    /// How many attempts were given up on and are still running, across
    /// every copy of this connector.
    abandoned: Arc<AtomicUsize>,
}

impl fmt::Debug for Connector {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Nothing parsed from the URL is printed. A password with an
        // unescaped `@` in it ends up in the host name.
        f.debug_struct("Connector").finish_non_exhaustive()
    }
}

fn config_error(message: impl fmt::Display) -> StoreError {
    StoreError::Backend(format!("store configuration: {message}"))
}

/// Whether every server the config names is on this machine.
fn is_local(config: &postgres::Config) -> bool {
    let hosts_local = config.get_hosts().iter().all(|host| match host {
        Host::Tcp(name) => {
            name.eq_ignore_ascii_case("localhost")
                || name.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback())
        }
        #[cfg(unix)]
        Host::Unix(_) => true,
    });
    hosts_local && config.get_hostaddrs().iter().all(IpAddr::is_loopback)
}

impl Connector {
    /// Validates the settings without connecting.
    pub fn new(config: &ConnectConfig) -> Result<Self, StoreError> {
        for (name, value, max) in [
            (
                "connect_timeout",
                config.connect_timeout,
                MAX_CONNECT_TIMEOUT,
            ),
            (
                "statement_timeout",
                config.statement_timeout,
                MAX_STATEMENT_TIMEOUT,
            ),
        ] {
            if value < Duration::from_millis(1) || value > max {
                return Err(config_error(format!(
                    "{name} must be between 1 ms and {} s",
                    max.as_secs()
                )));
            }
        }
        let mut pg: postgres::Config = config.url.parse().map_err(|e: postgres::Error| {
            // The URL may hold a password, and nothing from it is repeated
            // here. The driver's own message can quote a character or a
            // word of a mistyped string, so only the one cause that names
            // a fixed option is passed on.
            let cause = source_chain(&e);
            let detail = match cause.find("invalid value for option `") {
                Some(at) => {
                    let option = cause[at..].split('`').nth(1).unwrap_or_default();
                    let hint = if option == "sslmode" {
                        ". Use sslmode=require: it checks the server's certificate and host name"
                    } else {
                        ""
                    };
                    format!(": invalid value for option `{option}`{hint}")
                }
                None => String::new(),
            };
            config_error(format!("the database URL does not parse{detail}"))
        })?;
        // On the way to another machine, anyone on the path could refuse TLS
        // on the server's behalf, so `prefer` (the default) is not enough.
        if pg.get_ssl_mode() == SslMode::Prefer && !is_local(&pg) {
            pg.ssl_mode(SslMode::Require);
        }
        // statement_timeout bounds the server; the client's own reads need
        // keepalives, or a silent network would block a caller for good.
        pg.connect_timeout(config.connect_timeout)
            .keepalives(true)
            .keepalives_idle(KEEPALIVE_IDLE)
            .keepalives_interval(KEEPALIVE_INTERVAL)
            .keepalives_retries(KEEPALIVE_RETRIES)
            .tcp_user_timeout(config.statement_timeout + KEEPALIVE_INTERVAL * KEEPALIVE_RETRIES);
        if pg.get_application_name().is_none() {
            pg.application_name("core-memory");
        }

        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let mut roots = rustls::RootCertStore {
            roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
        };
        if let Some(path) = &config.ca_file {
            // The path is not repeated. A setting's value is only as
            // harmless as what was put there, which may have been the URL.
            let problem = |e: &dyn fmt::Display| config_error(format!("ca_file: {e}"));
            let pem = std::fs::read(path).map_err(|e| problem(&e))?;
            let certs = CertificateDer::pem_slice_iter(&pem)
                .collect::<Result<Vec<_>, _>>()
                .map_err(|e| problem(&e))?;
            if certs.is_empty() {
                return Err(problem(&"it holds no certificate"));
            }
            for cert in certs {
                roots.add(cert).map_err(|e| problem(&e))?;
            }
        }
        let tls = rustls::ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(|e| config_error(format!("TLS setup: {e}")))?
            .with_root_certificates(roots)
            .with_no_client_auth();
        let hosts = pg.get_hosts().len().max(pg.get_hostaddrs().len()).max(1);
        let attempt_limit = config
            .connect_timeout
            .saturating_mul(ATTEMPT_ALLOWANCE)
            .saturating_mul(u32::try_from(hosts).unwrap_or(u32::MAX));
        Ok(Self {
            config: pg,
            tls: MakeRustlsConnect::new(tls),
            statement_timeout_ms: config
                .statement_timeout
                .as_millis()
                .clamp(1, u128::from(u32::MAX)),
            attempt_limit,
            abandoned: Arc::new(AtomicUsize::new(0)),
        })
    }

    /// Opens one connection with the session limits set.
    ///
    /// A server that cannot be reached, or cannot take another connection
    /// now, is `Unavailable`. A refusal that would repeat is
    /// `AccessRefused`: a wrong password, a role or a database that does
    /// not exist, a certificate that does not verify, a server without TLS.
    ///
    /// The driver's timeout covers reaching the server, not what the
    /// server says next: one that accepts the connection and then says
    /// nothing would hold the caller for good. So the attempt runs on a
    /// thread of its own, and the caller waits for it no longer than the
    /// attempt's limit. The driver offers no way to end an attempt, so one
    /// that is given up on is left to end by itself, which it does when
    /// the server answers or the connection dies, and its connection is
    /// then closed. No more than a few such attempts are left running:
    /// past that, connecting fails at once until one of them ends.
    pub fn connect(&self) -> Result<Client, StoreError> {
        let left_running = self.abandoned.load(Ordering::SeqCst);
        if left_running >= MAX_ABANDONED_ATTEMPTS {
            return Err(StoreError::Unavailable(format!(
                "cannot connect: {left_running} earlier attempts are still waiting for the server to answer"
            )));
        }
        let state = Arc::new(AtomicU8::new(RUNNING));
        let (ended, result) = mpsc::sync_channel(1);
        let attempt = self.clone();
        let attempt_state = Arc::clone(&state);
        std::thread::Builder::new()
            .name("core-memory-connect".into())
            .spawn(move || {
                let outcome = attempt.attempt();
                if attempt_state.swap(ENDED, Ordering::SeqCst) == ABANDONED {
                    attempt.abandoned.fetch_sub(1, Ordering::SeqCst);
                }
                // With no one waiting, this drops the connection.
                let _ = ended.send(outcome);
            })
            .map_err(|e| {
                StoreError::Unavailable(format!("cannot connect: no thread to connect on: {e}"))
            })?;
        match result.recv_timeout(self.attempt_limit) {
            Ok(outcome) => outcome,
            Err(RecvTimeoutError::Disconnected) => Err(StoreError::Backend(
                "cannot connect: the attempt ended without a result".into(),
            )),
            Err(RecvTimeoutError::Timeout) => {
                // Counted before it is marked, so the attempt's thread
                // never takes away what was not yet added.
                self.abandoned.fetch_add(1, Ordering::SeqCst);
                if state
                    .compare_exchange(RUNNING, ABANDONED, Ordering::SeqCst, Ordering::SeqCst)
                    .is_err()
                {
                    // It ended just now. Its result is on its way.
                    self.abandoned.fetch_sub(1, Ordering::SeqCst);
                    if let Ok(outcome) = result.recv() {
                        return outcome;
                    }
                }
                Err(StoreError::Unavailable(format!(
                    "cannot connect: the server did not finish opening a connection within {} ms",
                    self.attempt_limit.as_millis()
                )))
            }
        }
    }

    /// One attempt, however long the server takes.
    fn attempt(&self) -> Result<Client, StoreError> {
        let mut client = self.config.connect(self.tls.clone()).map_err(|e| {
            let cause = describe(&e);
            if is_transient(&e) {
                return StoreError::Unavailable(format!("cannot connect: {cause}"));
            }
            let hint = if cause.contains("server does not support TLS") {
                ". Add sslmode=disable to the database URL only if the network between the engine and the database is trusted"
            } else if cause.contains("CaUsedAsEndEntity") {
                // What `openssl req -x509` makes unless it is told otherwise.
                ". The server's own certificate is marked as a certificate authority, which is not accepted even when ca_file names it. Give the server a certificate with basicConstraints=CA:FALSE, or one signed by the authority"
            } else {
                ""
            };
            StoreError::AccessRefused(format!("{cause}{hint}"))
        })?;
        client
            // The search path makes every unqualified name mean the
            // built-in one, whatever another role has created in `public`.
            // The isolation level is what the store's write lock and
            // schema check are written for.
            .batch_execute(&format!(
                "SET search_path = pg_catalog, pg_temp;
                 SET default_transaction_isolation = 'read committed';
                 SET statement_timeout = {};
                 SET lock_timeout = {LOCK_TIMEOUT_MS};
                 SET idle_in_transaction_session_timeout = {IDLE_IN_TRANSACTION_TIMEOUT_MS}",
                self.statement_timeout_ms
            ))
            .map_err(|e| {
                let message = format!("cannot set session limits: {}", describe(&e));
                if is_transient(&e) {
                    StoreError::Unavailable(message)
                } else {
                    StoreError::Backend(message)
                }
            })?;
        Ok(client)
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::time::Instant;

    use super::*;

    /// Why `connector` could not connect.
    fn refusal(connector: &Connector) -> StoreError {
        match connector.connect() {
            Ok(_) => panic!("connected"),
            Err(e) => e,
        }
    }

    /// Why a connector for `url` could not connect.
    fn refusal_at(url: String) -> StoreError {
        refusal(&Connector::new(&ConnectConfig::new(url)).unwrap())
    }

    /// A server on this machine that answers the driver's first question,
    /// whether it offers TLS, with `answer`, then sends `then`, and keeps
    /// the connection until the client closes it. Returns its port.
    fn server_that_says(answer: &'static [u8], then: &'static [u8]) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let (mut peer, _) = listener.accept().unwrap();
            let mut asked = [0u8; 8];
            peer.read_exact(&mut asked).unwrap();
            peer.write_all(answer).unwrap();
            peer.write_all(then).unwrap();
            let _ = peer.read_to_end(&mut Vec::new());
        });
        port
    }

    #[test]
    fn a_server_that_accepts_and_says_nothing_does_not_hold_the_caller() {
        // Nothing accepts here: the operating system completes each
        // connection and what the driver sends is never read.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let mut config = ConnectConfig::new(format!("postgresql://engine@127.0.0.1:{port}/memory"));
        config.connect_timeout = Duration::from_millis(100);
        let connector = Connector::new(&config).unwrap();

        let started = Instant::now();
        let err = refusal(&connector);
        let waited = started.elapsed();
        assert!(
            matches!(&err, StoreError::Unavailable(why)
                if why.contains("did not finish opening a connection within 300 ms")),
            "{err:?}"
        );
        assert!(
            waited >= Duration::from_millis(300) && waited < Duration::from_secs(5),
            "{waited:?}"
        );
        assert_eq!(connector.abandoned.load(Ordering::SeqCst), 1);

        // Only so many attempts are left running. Past that, a copy of the
        // connector, as a pool holds, is refused at once.
        for _ in 1..MAX_ABANDONED_ATTEMPTS {
            refusal(&connector.clone());
        }
        let started = Instant::now();
        let err = refusal(&connector.clone());
        assert!(
            matches!(&err, StoreError::Unavailable(why)
                if why.contains("8 earlier attempts are still waiting")),
            "{err:?}"
        );
        assert!(started.elapsed() < Duration::from_millis(250));

        // When the server goes away the attempts end, and connecting is
        // tried again.
        drop(listener);
        let deadline = Instant::now() + Duration::from_secs(10);
        while connector.abandoned.load(Ordering::SeqCst) > 0 {
            assert!(Instant::now() < deadline, "the attempts never ended");
            std::thread::sleep(Duration::from_millis(10));
        }
        let err = refusal(&connector);
        assert!(
            matches!(&err, StoreError::Unavailable(why) if why.contains("Connection refused")),
            "{err:?}"
        );
    }

    #[test]
    fn a_refusal_of_tls_is_not_worth_trying_again() {
        // A server that offers no TLS, asked for it.
        let port = server_that_says(b"N", b"");
        let err = refusal_at(format!(
            "postgresql://engine@127.0.0.1:{port}/memory?sslmode=require"
        ));
        assert!(
            matches!(&err, StoreError::AccessRefused(why)
                if why.contains("server does not support TLS") && why.contains("sslmode=disable")),
            "{err:?}"
        );

        // A server that offers TLS and then fails the handshake, as one
        // does whose certificate does not verify.
        let port = server_that_says(b"S", b"this is not a TLS record\n");
        let err = refusal_at(format!(
            "postgresql://engine@127.0.0.1:{port}/memory?sslmode=require"
        ));
        assert!(
            matches!(&err, StoreError::AccessRefused(why) if why.contains("TLS handshake")),
            "{err:?}"
        );

        // A server that is not there may be there later.
        let port = {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.local_addr().unwrap().port()
        };
        let err = refusal_at(format!("postgresql://engine@127.0.0.1:{port}/memory"));
        assert!(matches!(err, StoreError::Unavailable(_)), "{err:?}");
    }

    #[test]
    fn the_url_never_prints() {
        let config = ConnectConfig::new("postgresql://engine:hunter2@db.internal/memory");
        assert!(!format!("{config:?}").contains("hunter2"));
        let connector = Connector::new(&config).unwrap();
        let printed = format!("{connector:?}");
        assert!(!printed.contains("hunter2"), "{printed}");
        // An unescaped `@` in a password moves the rest of it into the host.
        let split = Connector::new(&ConnectConfig::new(
            "postgresql://engine:hun@ter2@db.internal/memory",
        ))
        .unwrap();
        assert!(!format!("{split:?}").contains("ter2"));
    }

    #[test]
    fn only_a_server_on_this_machine_may_be_reached_without_tls() {
        let mode = |url: &str| {
            Connector::new(&ConnectConfig::new(url))
                .unwrap()
                .config
                .get_ssl_mode()
        };
        for local in [
            "postgresql://localhost/memory",
            "postgresql://127.0.0.1:5432/memory",
            "postgresql://[::1]/memory",
            "host=/var/run/postgresql dbname=memory",
        ] {
            assert_eq!(mode(local), SslMode::Prefer, "{local}");
        }
        for remote in [
            "postgresql://db.internal/memory",
            "postgresql://10.0.0.5/memory",
            "postgresql://localhost,db.internal/memory",
            "host=localhost hostaddr=10.0.0.5 dbname=memory",
        ] {
            assert_eq!(mode(remote), SslMode::Require, "{remote}");
        }
        // Turning TLS off is always an explicit choice.
        assert_eq!(
            mode("postgresql://db.internal/memory?sslmode=disable"),
            SslMode::Disable
        );
        let err = Connector::new(&ConnectConfig::new(
            "postgresql://db.internal/memory?sslmode=verify-full",
        ))
        .unwrap_err();
        assert!(err.to_string().contains("sslmode=require"), "{err}");
    }

    #[test]
    fn a_bad_url_or_ca_file_is_a_configuration_error() {
        let err = Connector::new(&ConnectConfig::new("not a url at all=")).unwrap_err();
        assert!(err.to_string().contains("does not parse"), "{err}");
        // A password split by a stray space reads as an option name, or
        // leaves a stray word. Neither is quoted back.
        for mistyped in [
            "host=db password=hunter2 swordfish=1",
            "host=db password=my secret word",
        ] {
            let err = Connector::new(&ConnectConfig::new(mistyped))
                .unwrap_err()
                .to_string();
            assert!(err.ends_with("the database URL does not parse"), "{err}");
        }
        // Timeouts the operating system would refuse are caught here, not
        // at the first connection.
        for (connect_ms, statement_ms) in
            [(0, 1_000), (1_000, 0), (1_000, 3_600_001), (300_001, 1_000)]
        {
            let mut config = ConnectConfig::new("postgresql://localhost/memory");
            config.connect_timeout = Duration::from_millis(connect_ms);
            config.statement_timeout = Duration::from_millis(statement_ms);
            let err = Connector::new(&config).unwrap_err().to_string();
            assert!(err.contains("must be between"), "{err}");
        }
        for statement_ms in [1, 999, 3_600_000] {
            let mut config = ConnectConfig::new("postgresql://localhost/memory");
            config.statement_timeout = Duration::from_millis(statement_ms);
            assert!(Connector::new(&config).is_ok(), "{statement_ms}");
        }
        let mut config = ConnectConfig::new("postgresql://localhost/memory");
        config.ca_file = Some(PathBuf::from("/nonexistent/ca.pem"));
        let err = Connector::new(&config).unwrap_err();
        assert!(err.to_string().contains("ca_file"), "{err}");
    }
}

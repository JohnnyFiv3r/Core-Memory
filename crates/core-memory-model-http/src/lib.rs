//! Shared HTTP plumbing for model provider adapters: one POST per call, no
//! retries, and errors sorted by whether the provider could have processed
//! the request.

use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use core_memory_core::model::TransportError;
use ureq::Agent;
use ureq::tls::{Certificate, PemItem, RootCerts, TlsConfig};

/// Most of a provider's error body kept in an error message.
const ERROR_BODY_CHARS: usize = 500;

/// A blocking HTTP client with one overall timeout per request. It goes
/// through the proxy that `HTTPS_PROXY` names, except to the hosts
/// `NO_PROXY` lists.
#[derive(Debug, Clone)]
pub struct HttpClient {
    agent: Agent,
}

/// The certificate authorities a client trusts: the public roots, and the
/// certificates of a private one, such as a proxy's that re-signs what it
/// passes on.
#[derive(Clone)]
pub struct Roots {
    certs: Arc<Vec<Certificate<'static>>>,
    private: usize,
}

impl std::fmt::Debug for Roots {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Roots")
            .field("private", &self.private)
            .finish_non_exhaustive()
    }
}

impl Roots {
    /// The public roots and every certificate in the PEM file at `path`.
    /// The error never repeats the path: a setting's value is only as
    /// harmless as what was put there.
    pub fn with_file(path: &Path) -> Result<Self, String> {
        let pem = std::fs::read(path).map_err(|e| e.to_string())?;
        Self::with_pem(&pem)
    }

    fn with_pem(pem: &[u8]) -> Result<Self, String> {
        let mut private = Vec::new();
        for item in ureq::tls::parse_pem(pem) {
            match item.map_err(|e| e.to_string())? {
                PemItem::Certificate(cert) => private.push(cert),
                _ => return Err("it holds something other than certificates".into()),
            }
        }
        if private.is_empty() {
            return Err("it holds no certificate".into());
        }
        let count = private.len();
        let mut certs: Vec<Certificate<'static>> = webpki_root_certs::TLS_SERVER_ROOT_CERTS
            .iter()
            .map(|der| Certificate::from_der(der))
            .collect();
        certs.extend(private);
        Ok(Self {
            certs: Arc::new(certs),
            private: count,
        })
    }

    /// How many certificates the file gave.
    pub fn private(&self) -> usize {
        self.private
    }
}

/// A response of any status.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpReply {
    pub status: u16,
    pub retry_after_ms: Option<u64>,
    pub body: String,
}

impl HttpClient {
    /// A client that trusts the public roots.
    pub fn new(timeout: Duration) -> Self {
        Self::trusting(timeout, None)
    }

    /// A client that trusts `roots`, or the public roots alone if `None`.
    pub fn trusting(timeout: Duration, roots: Option<&Roots>) -> Self {
        let mut config = Agent::config_builder()
            .timeout_global(Some(timeout))
            .http_status_as_error(false);
        if let Some(roots) = roots {
            config = config.tls_config(
                TlsConfig::builder()
                    .root_certs(RootCerts::Specific(roots.certs.clone()))
                    .build(),
            );
        }
        Self {
            agent: Agent::new_with_config(config.build()),
        }
    }

    /// Sends one JSON POST. The adapter never retries; the engine decides.
    pub fn post_json(
        &self,
        url: &str,
        headers: &[(&str, &str)],
        body: &serde_json::Value,
    ) -> Result<HttpReply, TransportError> {
        let mut request = self
            .agent
            .post(url)
            .header("content-type", "application/json");
        for (name, value) in headers {
            request = request.header(*name, *value);
        }
        let mut response = request.send(body.to_string()).map_err(classify_error)?;
        let status = response.status().as_u16();
        let retry_after_ms = response
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.trim().parse::<u64>().ok())
            .map(|seconds| seconds.saturating_mul(1000));
        // The provider has the request now; failing to read the reply means
        // the outcome is unknown.
        let body = response
            .body_mut()
            .read_to_string()
            .map_err(|e| TransportError::InDoubt {
                message: format!("reading the reply: {e}"),
            })?;
        Ok(HttpReply {
            status,
            retry_after_ms,
            body,
        })
    }
}

/// Errors before a response. Failures to resolve or connect mean nothing
/// was sent; anything later may have reached the provider.
fn classify_error(e: ureq::Error) -> TransportError {
    match e {
        ureq::Error::HostNotFound | ureq::Error::ConnectionFailed => TransportError::Transient {
            message: e.to_string(),
            retry_after_ms: None,
        },
        ureq::Error::Timeout(ureq::Timeout::Resolve | ureq::Timeout::Connect) => {
            TransportError::Transient {
                message: e.to_string(),
                retry_after_ms: None,
            }
        }
        ureq::Error::Io(ref io) if never_sent(io.kind()) => TransportError::Transient {
            message: e.to_string(),
            retry_after_ms: None,
        },
        ureq::Error::BadUri(_) | ureq::Error::InvalidProxyUrl => TransportError::Permanent {
            message: e.to_string(),
        },
        other => TransportError::InDoubt {
            message: other.to_string(),
        },
    }
}

/// I/O failures that happen before any byte reaches the provider.
fn never_sent(kind: std::io::ErrorKind) -> bool {
    use std::io::ErrorKind::*;
    matches!(
        kind,
        ConnectionRefused | AddrNotAvailable | NetworkUnreachable | HostUnreachable | NetworkDown
    )
}

/// Maps a non-success status to a transport error. 408, 409, 429 and 5xx
/// (including 529 overloaded) are retryable; 413 means the request was too
/// large, so a smaller batch may succeed; other 4xx are not retryable,
/// unless the provider's own error body says otherwise.
///
/// `refused` is the adapter's reading of its provider's error bodies. It
/// sees each other 4xx status with its body parsed as JSON, and returns
/// true only for the documented shapes that mean the model declined the
/// content or the request did not fit; those become
/// [`TransportError::Refused`]. Everything else stays permanent.
pub fn status_error(
    provider: &str,
    reply: &HttpReply,
    refused: fn(u16, &serde_json::Value) -> bool,
) -> TransportError {
    let snippet: String = reply.body.chars().take(ERROR_BODY_CHARS).collect();
    let message = format!("{provider} returned {}: {snippet}", reply.status);
    match reply.status {
        408 | 409 | 429 | 500..=599 => TransportError::Transient {
            message,
            retry_after_ms: reply.retry_after_ms,
        },
        413 => TransportError::Refused { message },
        status @ 400..=499
            if serde_json::from_str(&reply.body).is_ok_and(|body| refused(status, &body)) =>
        {
            TransportError::Refused { message }
        }
        _ => TransportError::Permanent { message },
    }
}

/// Parses a success body, treating an unreadable one as in doubt: the
/// provider processed (and may have billed) the call.
pub fn parse_body(provider: &str, reply: &HttpReply) -> Result<serde_json::Value, TransportError> {
    serde_json::from_str(&reply.body).map_err(|e| TransportError::InDoubt {
        message: format!("{provider} returned an unreadable body: {e}"),
    })
}

#[cfg(feature = "testing")]
pub mod testing {
    //! A one-shot HTTP server for adapter tests.

    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::thread::JoinHandle;

    /// A request the mock server received.
    #[derive(Debug, Clone)]
    pub struct Received {
        pub request_line: String,
        pub headers: Vec<(String, String)>,
        pub body: String,
    }

    impl Received {
        pub fn header(&self, name: &str) -> Option<&str> {
            self.headers
                .iter()
                .find(|(k, _)| k.eq_ignore_ascii_case(name))
                .map(|(_, v)| v.as_str())
        }
    }

    /// A canned response.
    #[derive(Debug, Clone)]
    pub struct Reply {
        pub status: u16,
        pub headers: Vec<(String, String)>,
        pub body: String,
    }

    impl Reply {
        pub fn json(status: u16, body: impl Into<String>) -> Self {
            Self {
                status,
                headers: vec![("content-type".into(), "application/json".into())],
                body: body.into(),
            }
        }
    }

    /// Serves one request with `status`, `headers` and `body`. Returns the
    /// base URL and a handle yielding the request it received.
    pub fn serve_once(
        status: u16,
        headers: &[(&str, &str)],
        body: &str,
    ) -> (String, JoinHandle<Received>) {
        let reply = Reply {
            status,
            headers: headers
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            body: body.to_string(),
        };
        let (url, handle) = serve(1, move |_| reply.clone());
        let handle = std::thread::spawn(move || {
            #[allow(clippy::unwrap_used)]
            handle.join().unwrap().remove(0)
        });
        (url, handle)
    }

    /// Serves `count` requests, answering each with `handler`. Returns the
    /// base URL and a handle yielding the requests received, in order.
    #[allow(clippy::unwrap_used)]
    pub fn serve<F>(count: usize, handler: F) -> (String, JoinHandle<Vec<Received>>)
    where
        F: Fn(&Received) -> Reply + Send + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let handle = std::thread::spawn(move || {
            let mut all = Vec::with_capacity(count);
            for _ in 0..count {
                let (mut stream, _) = listener.accept().unwrap();
                let received = read_request(&mut BufReader::new(stream.try_clone().unwrap()));
                let reply = handler(&received);
                let mut response = format!(
                    "HTTP/1.1 {} Mock\r\ncontent-length: {}\r\nconnection: close\r\n",
                    reply.status,
                    reply.body.len()
                );
                for (k, v) in &reply.headers {
                    response.push_str(&format!("{k}: {v}\r\n"));
                }
                response.push_str("\r\n");
                response.push_str(&reply.body);
                stream.write_all(response.as_bytes()).unwrap();
                all.push(received);
            }
            all
        });
        (url, handle)
    }

    #[allow(clippy::unwrap_used)]
    fn read_request(reader: &mut impl BufRead) -> Received {
        let mut request_line = String::new();
        reader.read_line(&mut request_line).unwrap();
        let mut headers = Vec::new();
        let mut length = 0usize;
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            let line = line.trim_end();
            if line.is_empty() {
                break;
            }
            if let Some((k, v)) = line.split_once(':') {
                if k.eq_ignore_ascii_case("content-length") {
                    length = v.trim().parse().unwrap();
                }
                headers.push((k.trim().to_string(), v.trim().to_string()));
            }
        }
        let mut buf = vec![0u8; length];
        reader.read_exact(&mut buf).unwrap();
        Received {
            request_line: request_line.trim_end().to_string(),
            headers,
            body: String::from_utf8(buf).unwrap(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reply(status: u16, retry: Option<u64>) -> HttpReply {
        HttpReply {
            status,
            retry_after_ms: retry,
            body: "{\"error\": \"x\"}".into(),
        }
    }

    fn never(_: u16, _: &serde_json::Value) -> bool {
        false
    }

    fn always(_: u16, _: &serde_json::Value) -> bool {
        true
    }

    #[test]
    fn retryable_statuses_are_transient() {
        for status in [408, 409, 429, 500, 503, 529] {
            assert!(matches!(
                status_error("p", &reply(status, Some(2000)), always),
                TransportError::Transient {
                    retry_after_ms: Some(2000),
                    ..
                }
            ));
        }
        for status in [400, 401, 403, 404] {
            assert!(matches!(
                status_error("p", &reply(status, None), never),
                TransportError::Permanent { .. }
            ));
        }
        assert!(matches!(
            status_error("p", &reply(413, None), never),
            TransportError::Refused { .. }
        ));
    }

    #[test]
    fn the_adapter_reads_other_client_errors() {
        assert!(matches!(
            status_error("p", &reply(400, None), always),
            TransportError::Refused { .. }
        ));
        let unreadable = HttpReply {
            body: "<html>bad request</html>".into(),
            ..reply(400, None)
        };
        assert!(matches!(
            status_error("p", &unreadable, always),
            TransportError::Permanent { .. }
        ));
    }

    /// A self-signed authority made for these tests and nothing else.
    const TEST_CA: &str = "-----BEGIN CERTIFICATE-----\n\
MIIBkzCCATmgAwIBAgIUKcFoolTQZa7nRPSS3yea08CYRI4wCgYIKoZIzj0EAwIw\n\
HjEcMBoGA1UEAwwTQ29yZSBNZW1vcnkgdGVzdCBDQTAgFw0yNjExMTgwMjI4NDZa\n\
GA8yMTI2MTAyNTAyMjg0NlowHjEcMBoGA1UEAwwTQ29yZSBNZW1vcnkgdGVzdCBD\n\
QTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABLoFXbY5bzMePwwxPcHxZzlWubd5\n\
7fc2ZCoFybv/ptDSPpQ7guWGuQVED4TAOh14angTnQk6cn1jbdLnQ4hqhUajUzBR\n\
MB0GA1UdDgQWBBTSMBhrWAIlfZmRdA+InApMcz00XTAfBgNVHSMEGDAWgBTSMBhr\n\
WAIlfZmRdA+InApMcz00XTAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0gA\n\
MEUCIG2/Fa/qx/j0xw0IwRQ0FuNnlIojyuDQ/NDOCqkSqUFDAiEA6YSf+7LbyOCq\n\
0iA2k66Cin8Y1D+1XBzZRQnrXQLskas=\n\
-----END CERTIFICATE-----
";

    #[test]
    fn a_private_authority_is_trusted_as_well_as_the_public_roots() {
        let roots = Roots::with_pem(TEST_CA.as_bytes()).unwrap();
        assert_eq!(roots.private(), 1);
        assert_eq!(
            roots.certs.len(),
            webpki_root_certs::TLS_SERVER_ROOT_CERTS.len() + 1
        );
        let twice = format!("{TEST_CA}{TEST_CA}");
        assert_eq!(Roots::with_pem(twice.as_bytes()).unwrap().private(), 2);
        // A client trusting them is built without a request being sent.
        HttpClient::trusting(Duration::from_secs(1), Some(&roots));
    }

    #[test]
    fn a_file_without_certificates_or_with_a_key_is_refused() {
        assert_eq!(
            Roots::with_pem(b"not a certificate").unwrap_err(),
            "it holds no certificate"
        );
        let key =
            "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIA==\n-----END PRIVATE KEY-----\n";
        let mixed = format!("{TEST_CA}{key}");
        assert_eq!(
            Roots::with_pem(mixed.as_bytes()).unwrap_err(),
            "it holds something other than certificates"
        );
    }

    #[test]
    fn a_missing_file_is_refused_without_its_path() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("secret-name.pem");
        let err = Roots::with_file(&path).unwrap_err();
        assert!(!err.contains("secret-name"), "{err}");
        std::fs::write(&path, TEST_CA).unwrap();
        assert_eq!(Roots::with_file(&path).unwrap().private(), 1);
    }

    #[test]
    fn unreachable_hosts_are_transient() {
        // Port 9 on localhost has nothing listening.
        let client = HttpClient::new(Duration::from_secs(2));
        let err = client
            .post_json("http://127.0.0.1:9/x", &[], &serde_json::json!({}))
            .unwrap_err();
        assert!(matches!(err, TransportError::Transient { .. }), "{err:?}");
    }
}

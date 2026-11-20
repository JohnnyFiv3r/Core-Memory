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
/// was sent, and so does a server certificate that is not trusted; anything
/// later may have reached the provider.
fn classify_error(e: ureq::Error) -> TransportError {
    if let Some(refused) = rustls_error(&e).filter(|e| certificate_refused(e)) {
        return TransportError::Untrusted {
            message: format!(
                "the server's TLS certificate was not trusted ({refused}), so nothing was \
                 sent. If a proxy or gateway re-signs TLS on the way, trust its certificate \
                 authority with [models] ca_file"
            ),
        };
    }
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

/// The rustls error behind `e`, if the TLS connection failed in rustls.
/// The handshake reports one as an I/O error wrapping it.
fn rustls_error(e: &ureq::Error) -> Option<&rustls::Error> {
    match e {
        ureq::Error::Rustls(inner) => Some(inner),
        ureq::Error::Io(io) => io
            .get_ref()
            .and_then(|inner| inner.downcast_ref::<rustls::Error>()),
        _ => None,
    }
}

/// Whether rustls refused the server's certificate: it was not valid or not
/// trusted, or there was none. A client sees the server's certificate only
/// in the handshake, before it writes a byte of the request: rustls does not
/// renegotiate, and TLS 1.3 has no later server certificate. So a call that
/// failed this way sent nothing. Any other TLS error is not placed by its
/// kind alone before or after the request, and stays in doubt.
fn certificate_refused(error: &rustls::Error) -> bool {
    matches!(
        error,
        rustls::Error::InvalidCertificate(_) | rustls::Error::NoCertificatesPresented
    )
}

/// I/O failures that happen before any byte reaches the provider.
fn never_sent(kind: std::io::ErrorKind) -> bool {
    use std::io::ErrorKind::*;
    matches!(
        kind,
        ConnectionRefused | AddrNotAvailable | NetworkUnreachable | HostUnreachable | NetworkDown
    )
}

/// What a provider's error body says, where it says more than its status.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClientError {
    /// The model declined the content.
    Declined,
    /// The request did not fit the model's context.
    DidNotFit,
}

/// Maps a non-success status to a transport error. 408, 409, 429 and 5xx
/// (including 529 overloaded) are retryable; 413 means the request was too
/// large, so a smaller batch may succeed; other 4xx are not retryable,
/// unless the provider's own error body says otherwise.
///
/// `read` is the adapter's reading of its provider's error bodies. It sees
/// each other 4xx status with its body parsed as JSON, and names only the
/// documented shapes that mean the model declined the content
/// ([`TransportError::Refused`]) or the request did not fit
/// ([`TransportError::DidNotFit`]). Everything else stays permanent.
pub fn status_error(
    provider: &str,
    reply: &HttpReply,
    read: fn(u16, &serde_json::Value) -> Option<ClientError>,
) -> TransportError {
    let snippet: String = reply.body.chars().take(ERROR_BODY_CHARS).collect();
    let message = format!("{provider} returned {}: {snippet}", reply.status);
    match reply.status {
        408 | 409 | 429 | 500..=599 => TransportError::Transient {
            message,
            retry_after_ms: reply.retry_after_ms,
        },
        413 => TransportError::DidNotFit { message },
        status @ 400..=499 => match serde_json::from_str(&reply.body)
            .ok()
            .and_then(|body| read(status, &body))
        {
            Some(ClientError::Declined) => TransportError::Refused { message },
            Some(ClientError::DidNotFit) => TransportError::DidNotFit { message },
            None => TransportError::Permanent { message },
        },
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

    fn never(_: u16, _: &serde_json::Value) -> Option<ClientError> {
        None
    }

    fn declined(_: u16, _: &serde_json::Value) -> Option<ClientError> {
        Some(ClientError::Declined)
    }

    fn too_long(_: u16, _: &serde_json::Value) -> Option<ClientError> {
        Some(ClientError::DidNotFit)
    }

    #[test]
    fn retryable_statuses_are_transient() {
        for status in [408, 409, 429, 500, 503, 529] {
            assert!(matches!(
                status_error("p", &reply(status, Some(2000)), declined),
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
            TransportError::DidNotFit { .. }
        ));
    }

    #[test]
    fn the_adapter_reads_other_client_errors() {
        assert!(matches!(
            status_error("p", &reply(400, None), declined),
            TransportError::Refused { .. }
        ));
        assert!(matches!(
            status_error("p", &reply(400, None), too_long),
            TransportError::DidNotFit { .. }
        ));
        let unreadable = HttpReply {
            body: "<html>bad request</html>".into(),
            ..reply(400, None)
        };
        assert!(matches!(
            status_error("p", &unreadable, declined),
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

    /// An authority a TLS gateway might sign with, made for these tests.
    const GATEWAY_CA: &str = "-----BEGIN CERTIFICATE-----\n\
MIIBtDCCAVmgAwIBAgIURympWYMX8VATelpnvS8hzPrXa10wCgYIKoZIzj0EAwIw\n\
JjEkMCIGA1UEAwwbQ29yZSBNZW1vcnkgdGVzdCBnYXRld2F5IENBMCAXDTI2MTEy\n\
MDE1NTc1MFoYDzIxMjYxMDI3MTU1NzUwWjAmMSQwIgYDVQQDDBtDb3JlIE1lbW9y\n\
eSB0ZXN0IGdhdGV3YXkgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAARq7Quw\n\
yYExShZC2A6t5+R90a3ipk+mNa3Dew6TpRCV818IZit4QOn4UZWmrYGptCp7ELfW\n\
cm+ZPvZ9gjqsXsYQo2MwYTAdBgNVHQ4EFgQUgBo4WTypF9DbispKqK5EClhpMEsw\n\
HwYDVR0jBBgwFoAUgBo4WTypF9DbispKqK5EClhpMEswDwYDVR0TAQH/BAUwAwEB\n\
/zAOBgNVHQ8BAf8EBAMCAQYwCgYIKoZIzj0EAwIDSQAwRgIhAM6TCFRR+iD/J1ak\n\
lm4nliK3bqGUbs0Q+t/ppApmKUpOAiEAuM31UAbGF6YLBLSnzuXREgwj51ZMfHN5\n\
FP2e5Ae2bsY=\n\
-----END CERTIFICATE-----
";
    /// The gateway's certificate for 127.0.0.1, signed by [`GATEWAY_CA`].
    const GATEWAY_LEAF: &str = "-----BEGIN CERTIFICATE-----\n\
MIIBxjCCAWygAwIBAgIUCE9G0xM+ZY/O0Zfm5NhN7nJaYFYwCgYIKoZIzj0EAwIw\n\
JjEkMCIGA1UEAwwbQ29yZSBNZW1vcnkgdGVzdCBnYXRld2F5IENBMCAXDTI2MTEy\n\
MDE1NTc1MFoYDzIxMjYxMDI3MTU1NzUwWjAUMRIwEAYDVQQDDAkxMjcuMC4wLjEw\n\
WTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAARzlZbLNl/oNGHS/0uNGxWACyqI0R0V\n\
VE3NNKg/a24FtW/3ctKv91mXYRyGT1W63Z0vvs0m2iRt5QrQFcP/ZCPao4GHMIGE\n\
MAwGA1UdEwEB/wQCMAAwDgYDVR0PAQH/BAQDAgeAMBMGA1UdJQQMMAoGCCsGAQUF\n\
BwMBMA8GA1UdEQQIMAaHBH8AAAEwHQYDVR0OBBYEFOnRDpR8XQnH3YfFLOrzrhL3\n\
JsZsMB8GA1UdIwQYMBaAFIAaOFk8qRfQ24rKSqiuRApYaTBLMAoGCCqGSM49BAMC\n\
A0gAMEUCIQDrkjhDVkGUH1WPgbsYFzXGgjvhkh43YcWMCnjnCwrBagIgFqGgZjjy\n\
sOmDGV+j8AjWCbWbF37pknlpceXt1BGe3QQ=\n\
-----END CERTIFICATE-----
";
    /// Its key. A test key, for this certificate and nothing else.
    const GATEWAY_KEY: &str = "-----BEGIN PRIVATE KEY-----\n\
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg/I71+PusVfjC31Ww\n\
KikaNueW9n2Pp/+5Fhv/KvuSH9WhRANCAARzlZbLNl/oNGHS/0uNGxWACyqI0R0V\n\
VE3NNKg/a24FtW/3ctKv91mXYRyGT1W63Z0vvs0m2iRt5QrQFcP/ZCPa\n\
-----END PRIVATE KEY-----
";

    /// A TLS server on 127.0.0.1 with the gateway's certificate. It answers
    /// one connection with 200 if the handshake succeeds, and yields the
    /// request line it read, or `None` if the handshake failed.
    fn serve_tls() -> (u16, std::thread::JoinHandle<Option<String>>) {
        use std::io::{BufRead, BufReader, Write};

        let certs: Vec<rustls::pki_types::CertificateDer<'static>> =
            ureq::tls::parse_pem(GATEWAY_LEAF.as_bytes())
                .map(|item| match item.unwrap() {
                    PemItem::Certificate(cert) => cert.der().to_vec().into(),
                    _ => panic!("not a certificate"),
                })
                .collect();
        let key = match ureq::tls::parse_pem(GATEWAY_KEY.as_bytes()).next() {
            Some(Ok(PemItem::PrivateKey(key))) => {
                rustls::pki_types::PrivateKeyDer::Pkcs8(key.der().to_vec().into())
            }
            _ => panic!("not a key"),
        };
        let config = rustls::ServerConfig::builder_with_provider(Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .unwrap();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = std::thread::spawn(move || {
            let (sock, _) = listener.accept().unwrap();
            let conn = rustls::ServerConnection::new(Arc::new(config)).unwrap();
            let mut tls = rustls::StreamOwned::new(conn, sock);
            // A client that refuses the certificate ends the handshake, and
            // this read fails.
            let mut reader = BufReader::new(&mut tls);
            let mut request_line = String::new();
            reader.read_line(&mut request_line).ok()?;
            // The rest of the request, so the reply does not cut it off.
            let mut length = 0usize;
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).ok()?;
                let line = line.trim_end();
                if line.is_empty() {
                    break;
                }
                if let Some((name, value)) = line.split_once(':')
                    && name.eq_ignore_ascii_case("content-length")
                {
                    length = value.trim().parse().ok()?;
                }
            }
            std::io::Read::read_exact(&mut reader, &mut vec![0u8; length]).ok()?;
            let body = "{}";
            write!(
                tls,
                "HTTP/1.1 200 OK\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .ok()?;
            tls.conn.send_close_notify();
            tls.flush().ok()?;
            Some(request_line.trim_end().to_string())
        });
        (port, handle)
    }

    #[test]
    fn a_server_whose_certificate_is_not_trusted_was_sent_nothing() {
        let (port, server) = serve_tls();
        // The public roots alone: the gateway's authority is not among them.
        let client = HttpClient::new(Duration::from_secs(5));
        let err = client
            .post_json(
                &format!("https://127.0.0.1:{port}/v1/messages"),
                &[],
                &serde_json::json!({}),
            )
            .unwrap_err();
        let TransportError::Untrusted { message } = &err else {
            panic!("{err:?}");
        };
        assert!(message.contains("[models] ca_file"), "{message}");
        assert!(message.contains("nothing was sent"), "{message}");
        // The server never got a request.
        assert_eq!(server.join().unwrap(), None);
    }

    #[test]
    fn the_same_server_is_reached_once_its_authority_is_trusted() {
        let (port, server) = serve_tls();
        let roots = Roots::with_pem(GATEWAY_CA.as_bytes()).unwrap();
        let client = HttpClient::trusting(Duration::from_secs(5), Some(&roots));
        let reply = client
            .post_json(
                &format!("https://127.0.0.1:{port}/v1/messages"),
                &[],
                &serde_json::json!({}),
            )
            .unwrap();
        assert_eq!((reply.status, reply.body.as_str()), (200, "{}"));
        assert_eq!(
            server.join().unwrap().as_deref(),
            Some("POST /v1/messages HTTP/1.1")
        );
    }

    #[test]
    fn a_tls_error_that_is_not_about_the_certificate_stays_in_doubt() {
        use std::io::Write;
        // A server that answers in plain HTTP to a client speaking TLS. The
        // handshake fails, but on a malformed message, not a certificate,
        // and an error of that kind can come after a request was written
        // too: its kind alone does not say nothing was sent.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().unwrap();
            let _ = sock.write_all(b"HTTP/1.1 400 Bad Request\r\ncontent-length: 0\r\n\r\n");
        });
        let client = HttpClient::new(Duration::from_secs(5));
        let err = client
            .post_json(
                &format!("https://127.0.0.1:{port}/x"),
                &[],
                &serde_json::json!({}),
            )
            .unwrap_err();
        assert!(matches!(err, TransportError::InDoubt { .. }), "{err:?}");
        server.join().unwrap();
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

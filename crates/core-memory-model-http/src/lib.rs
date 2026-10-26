//! Shared HTTP plumbing for model provider adapters: one POST per call, no
//! retries, and errors sorted by whether the provider could have processed
//! the request.

use std::time::Duration;

use core_memory_core::model::TransportError;
use ureq::Agent;

/// Most of a provider's error body kept in an error message.
const ERROR_BODY_CHARS: usize = 500;

/// A blocking HTTP client with one overall timeout per request.
#[derive(Debug, Clone)]
pub struct HttpClient {
    agent: Agent,
}

/// A response of any status.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpReply {
    pub status: u16,
    pub retry_after_ms: Option<u64>,
    pub body: String,
}

impl HttpClient {
    pub fn new(timeout: Duration) -> Self {
        let config = Agent::config_builder()
            .timeout_global(Some(timeout))
            .http_status_as_error(false)
            .build();
        Self {
            agent: Agent::new_with_config(config),
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
/// (including 529 overloaded) are retryable; other 4xx are not.
pub fn status_error(provider: &str, reply: &HttpReply) -> TransportError {
    let snippet: String = reply.body.chars().take(ERROR_BODY_CHARS).collect();
    let message = format!("{provider} returned {}: {snippet}", reply.status);
    match reply.status {
        408 | 409 | 429 | 500..=599 => TransportError::Transient {
            message,
            retry_after_ms: reply.retry_after_ms,
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

    #[test]
    fn retryable_statuses_are_transient() {
        for status in [408, 409, 429, 500, 503, 529] {
            assert!(matches!(
                status_error("p", &reply(status, Some(2000))),
                TransportError::Transient {
                    retry_after_ms: Some(2000),
                    ..
                }
            ));
        }
        for status in [400, 401, 403, 404, 413] {
            assert!(matches!(
                status_error("p", &reply(status, None)),
                TransportError::Permanent { .. }
            ));
        }
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

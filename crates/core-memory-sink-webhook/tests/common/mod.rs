//! A local HTTP receiver for the webhook sink's tests.

#![allow(clippy::unwrap_used, dead_code)]

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;

pub const SECRET: &[u8] = b"a shared secret of 32 bytes, ok.";

#[derive(Debug, Clone)]
pub struct Request {
    pub headers: Vec<(String, String)>,
    pub body: String,
}

impl Request {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }
}

#[derive(Debug, Clone)]
pub struct Response {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: String,
}

impl Response {
    pub fn json(status: u16, body: serde_json::Value) -> Self {
        Self {
            status,
            headers: vec![("content-type".into(), "application/json".into())],
            body: body.to_string(),
        }
    }

    pub fn empty(status: u16) -> Self {
        Self {
            status,
            headers: Vec::new(),
            body: String::new(),
        }
    }
}

/// Starts a receiver that answers every request with `handler`, one at a
/// time, until the test process exits. Returns its URL.
pub fn start<F>(handler: F) -> String
where
    F: Fn(&Request) -> Response + Send + 'static,
{
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/hook", listener.local_addr().unwrap());
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let Some(request) = read_request(&mut BufReader::new(stream.try_clone().unwrap()))
            else {
                continue;
            };
            let response = handler(&request);
            let mut out = format!(
                "HTTP/1.1 {} Test\r\ncontent-length: {}\r\nconnection: close\r\n",
                response.status,
                response.body.len()
            );
            for (k, v) in &response.headers {
                out.push_str(&format!("{k}: {v}\r\n"));
            }
            out.push_str("\r\n");
            out.push_str(&response.body);
            let _ = stream.write_all(out.as_bytes());
        }
    });
    url
}

fn read_request(reader: &mut impl BufRead) -> Option<Request> {
    let mut request_line = String::new();
    reader.read_line(&mut request_line).ok()?;
    let mut headers = Vec::new();
    let mut length = 0usize;
    loop {
        let mut line = String::new();
        reader.read_line(&mut line).ok()?;
        let line = line.trim_end();
        if line.is_empty() {
            break;
        }
        let (k, v) = line.split_once(':')?;
        if k.eq_ignore_ascii_case("content-length") {
            length = v.trim().parse().ok()?;
        }
        headers.push((k.trim().to_string(), v.trim().to_string()));
    }
    let mut body = vec![0u8; length];
    reader.read_exact(&mut body).ok()?;
    Some(Request {
        headers,
        body: String::from_utf8(body).ok()?,
    })
}

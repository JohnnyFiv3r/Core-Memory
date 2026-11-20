//! Signing in, renewing and signing out, as OpenAI's guide for open-source
//! apps lays out: authorization code with PKCE in the system browser, a
//! callback on `127.0.0.1`, a client registered on first sign-in and
//! reused after, ID-token validation, rotating refresh tokens, and
//! revocation on sign-out.
//!
//! The authorization URL carries no token: a returning account is hinted by
//! its email (`login_hint`), not by its ID token, because the URL is printed
//! and handed to the browser on a command line other users can see.

use std::fmt;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use core_memory_core::model::TransportError;
use core_memory_model_http::HttpClient;
use serde_json::Value;
use time::OffsetDateTime;

use crate::credentials::{
    self, BackgroundUse, CONSENT_REVISION, Credentials, FileError, Secret, now_rfc3339,
};
use crate::{APP_NAME, ENDPOINT, Endpoints, idtoken, plain, plain_code};

/// The client ID that starts a first sign-in. Never saved: the sign-in
/// returns the client issued for the account.
const REGISTRATION_CLIENT: &str = "dynamic_agent_client";
/// Identity scopes, then the scopes that let the tokens use the plan.
const SCOPES: &str =
    "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct";
/// The callback port the guide's examples use, if it is free.
const PREFERRED_PORT: u16 = 1455;
const CALLBACK_PATH: &str = "/auth/callback";
/// Most of a callback request that is read.
const MAX_CALLBACK_BYTES: usize = 16 * 1024;
/// Refresh error codes that mean the refresh token is of no more use.
const UNUSABLE: [&str; 6] = [
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
];

/// `bytes` random bytes, base64url without padding.
fn random(bytes: usize) -> Result<String, SignInError> {
    let mut buf = vec![0u8; bytes];
    getrandom::fill(&mut buf).map_err(|e| SignInError::Local(e.to_string()))?;
    Ok(URL_SAFE_NO_PAD.encode(buf))
}

/// The PKCE challenge for `verifier`: base64url of its SHA-256.
fn challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(ring::digest::digest(
        &ring::digest::SHA256,
        verifier.as_bytes(),
    ))
}

/// Percent-encodes a query value: everything but the unreserved characters.
fn encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for b in value.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~') {
            out.push(char::from(b));
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// Decodes a query value, `+` as a space. `None` if it is not UTF-8.
fn decode(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => out.push(b' '),
            b'%' => {
                let hex = std::str::from_utf8(bytes.get(i + 1..i + 3)?).ok()?;
                out.push(u8::from_str_radix(hex, 16).ok()?);
                i += 2;
            }
            b => out.push(b),
        }
        i += 1;
    }
    String::from_utf8(out).ok()
}

/// The query of a request target as name and value pairs.
fn query(target: &str) -> Vec<(String, String)> {
    let Some((_, query)) = target.split_once('?') else {
        return Vec::new();
    };
    query
        .split('&')
        .filter_map(|pair| {
            let (name, value) = pair.split_once('=').unwrap_or((pair, ""));
            Some((decode(name)?, decode(value)?))
        })
        .collect()
}

/// Why a sign-in, a sign-out or an import did not complete. Each message
/// says what to do next, and none repeats a token or a path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SignInError {
    /// The owner declined in the browser, or OpenAI did not let the
    /// sign-in through. The code is OpenAI's.
    Declined(String),
    /// No answer came back to the callback in time.
    TimedOut,
    /// The callback did not hold what a completed sign-in returns.
    Callback(String),
    /// The token endpoint did not accept the sign-in.
    Exchange(String),
    /// The ID token did not validate.
    IdToken(String),
    /// A different account signed in than the one this file holds.
    OtherAccount,
    /// OpenAI could not be reached, or did not answer as expected.
    Network(String),
    File(FileError),
    /// Something on this machine failed: the callback port, randomness.
    Local(String),
}

impl fmt::Display for SignInError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SignInError::Declined(code) => write!(
                f,
                "the sign-in was not completed ({code}); nothing was saved. To let Core Memory \
                 use your ChatGPT plan, sign in again and allow it"
            ),
            SignInError::TimedOut => f.write_str(
                "no sign-in came back in time; nothing was saved. Run the command again and \
                 finish signing in in the browser",
            ),
            SignInError::Callback(why) => {
                write!(f, "the sign-in did not complete: {why}; nothing was saved")
            }
            SignInError::Exchange(code) => write!(
                f,
                "OpenAI did not accept the sign-in ({code}); nothing was saved. Run the command \
                 again to start a new sign-in"
            ),
            SignInError::IdToken(why) => write!(
                f,
                "the sign-in's ID token was refused: {why}; nothing was saved"
            ),
            SignInError::OtherAccount => f.write_str(
                "a different ChatGPT account signed in than the one this credentials file \
                 holds; nothing was saved. Sign in with that account, or keep the other one in \
                 another file with --credentials",
            ),
            SignInError::Network(why) => write!(f, "OpenAI could not be reached: {why}"),
            SignInError::File(e) => e.fmt(f),
            SignInError::Local(why) => write!(f, "{why}"),
        }
    }
}

impl std::error::Error for SignInError {}

impl From<FileError> for SignInError {
    fn from(e: FileError) -> Self {
        SignInError::File(e)
    }
}

fn network(e: TransportError) -> SignInError {
    SignInError::Network(e.to_string())
}

/// The OAuth error code of a reply's body: `{"error": "code"}` or
/// `{"error": {"code": "code"}}`.
fn error_code(body: &str) -> Option<String> {
    let body: Value = serde_json::from_str(body).ok()?;
    plain_code(&body["error"])
        .or_else(|| plain_code(&body["error"]["code"]))
        .map(str::to_string)
}

/// What the token endpoint returned.
#[derive(Debug, Clone)]
pub(crate) struct Tokens {
    access: Secret,
    refresh: Option<Secret>,
    id: Option<Secret>,
    token_type: Option<String>,
    expires_in: Option<u64>,
    scopes: Option<Vec<String>>,
    earliest_refresh_at: Option<Value>,
}

impl Tokens {
    fn parse(body: &str) -> Option<Self> {
        let body: Value = serde_json::from_str(body).ok()?;
        let secret = |name: &str| {
            body[name]
                .as_str()
                .filter(|t| !t.is_empty())
                .map(Secret::new)
        };
        Some(Self {
            access: secret("access_token")?,
            refresh: secret("refresh_token"),
            id: secret("id_token"),
            token_type: body["token_type"].as_str().map(str::to_string),
            expires_in: body["expires_in"].as_u64(),
            scopes: body["scope"]
                .as_str()
                .map(|s| s.split_whitespace().map(str::to_string).collect()),
            earliest_refresh_at: Some(body["earliest_refresh_at"].clone()).filter(|v| !v.is_null()),
        })
    }

    /// Puts a renewal into `credentials`: the access token, its expiry, the
    /// scopes and the rotated refresh token, together.
    pub(crate) fn apply(self, credentials: &mut Credentials) {
        credentials.access_token = Some(self.access);
        if let Some(refresh) = self.refresh {
            credentials.refresh_token = Some(refresh);
        }
        if let Some(id) = self.id {
            credentials.id_token = Some(id);
        }
        if let Some(kind) = self.token_type {
            credentials.token_type = kind;
        }
        credentials.expires_in = self.expires_in.unwrap_or(0);
        if let Some(scopes) = self.scopes {
            credentials.scopes = scopes;
        }
        credentials.earliest_refresh_at = self.earliest_refresh_at;
        credentials.saved_at = now_rfc3339();
    }
}

/// Why a renewal failed.
#[derive(Debug)]
pub(crate) enum RefreshError {
    /// The refresh token is of no more use; the code is OpenAI's.
    Unusable(String),
    Failed(TransportError),
}

/// Renews `current`'s tokens with `refresh_token`, keeping the grant as it
/// is: no scope is asked for.
pub(crate) fn refresh(
    client: &HttpClient,
    endpoints: &Endpoints,
    current: &Credentials,
    refresh_token: &Secret,
) -> Result<Tokens, RefreshError> {
    let reply = client
        .post_form(
            &endpoints.token_url(),
            &[],
            &[
                ("grant_type", "refresh_token"),
                ("client_id", &current.client_id),
                ("refresh_token", refresh_token.expose()),
                ("resource", ENDPOINT),
            ],
        )
        .map_err(|e| {
            RefreshError::Failed(match e {
                // The model was sent nothing, whatever became of this.
                TransportError::Untrusted { .. } => e,
                other => TransportError::Transient {
                    message: format!("renewing the sign-in: {other}"),
                    retry_after_ms: None,
                },
            })
        })?;
    if reply.status == 200 {
        return Tokens::parse(&reply.body).ok_or_else(|| {
            RefreshError::Failed(TransportError::Transient {
                message: "renewing the sign-in: the reply held no access token".into(),
                retry_after_ms: None,
            })
        });
    }
    let code = error_code(&reply.body);
    match (reply.status, code.as_deref()) {
        (_, Some(code)) if UNUSABLE.contains(&code) => Err(RefreshError::Unusable(code.into())),
        (_, Some("invalid_client")) => Err(RefreshError::Failed(TransportError::Permanent {
            message: "OpenAI no longer accepts this sign-in's client (invalid_client). Move the \
                      credentials file aside and run `core-memory login chatgpt` to register \
                      again"
                .into(),
        })),
        (408 | 429 | 500..=599, _) => Err(RefreshError::Failed(TransportError::Transient {
            message: format!("renewing the sign-in: OpenAI returned {}", reply.status),
            retry_after_ms: reply.retry_after_ms,
        })),
        (401, code) => Err(RefreshError::Failed(TransportError::SignInRequired {
            message: format!(
                "OpenAI did not accept the sign-in's renewal (401{}). Run `core-memory login \
                 chatgpt`",
                code.map(|c| format!(", {c}")).unwrap_or_default()
            ),
        })),
        (status, code) => Err(RefreshError::Failed(TransportError::Permanent {
            message: format!(
                "renewing the sign-in: OpenAI returned {status}{}",
                code.map(|c| format!(", {c}")).unwrap_or_default()
            ),
        })),
    }
}

/// How a sign-in starts.
#[derive(Debug, Clone)]
pub struct SignInOptions {
    /// Where the sign-in is saved.
    pub credentials: PathBuf,
    /// Asks OpenAI to show its consent screen again (`prompt=consent`), as
    /// to grant the plan after declining it.
    pub ask_again: bool,
    /// The callback port. `None` for 1455 if it is free, else any.
    pub port: Option<u16>,
    /// The owner agreed to Core Memory using the plan in the background.
    /// Recorded with the sign-in; without it no call is made.
    pub background_use: bool,
}

/// What a completed sign-in saved.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignedIn {
    pub email: Option<String>,
    /// Whether the plan's use was granted. Without it the sign-in is kept
    /// but no call is made.
    pub plan_granted: bool,
    /// Whether this is the account's first sign-in from this file.
    pub first: bool,
}

/// A sign-in waiting for the owner to finish it in the browser.
#[derive(Debug)]
pub struct PendingSignIn {
    listener: TcpListener,
    url: String,
    redirect_uri: String,
    state: String,
    nonce: String,
    verifier: Secret,
    /// The account's issued client, if it signed in before.
    previous: Option<Credentials>,
    host_id: String,
    options: SignInOptions,
    endpoints: Endpoints,
    client: HttpClient,
}

impl PendingSignIn {
    /// Prepares a sign-in to OpenAI: this host's ID made and saved if it is
    /// the first, the callback listening, and fresh state, nonce and PKCE.
    pub fn start(options: SignInOptions, client: HttpClient) -> Result<Self, SignInError> {
        Self::start_at(options, client, Endpoints::openai())
    }

    pub(crate) fn start_at(
        options: SignInOptions,
        client: HttpClient,
        endpoints: Endpoints,
    ) -> Result<Self, SignInError> {
        let host_id = credentials::ensure_host_id(&options.credentials)?;
        let previous = credentials::load(&options.credentials)?;
        let listener = match options.port {
            Some(port) => TcpListener::bind((Ipv4Addr::LOCALHOST, port)),
            None => TcpListener::bind((Ipv4Addr::LOCALHOST, PREFERRED_PORT))
                .or_else(|_| TcpListener::bind((Ipv4Addr::LOCALHOST, 0))),
        }
        .map_err(|e| SignInError::Local(format!("no port on 127.0.0.1 to listen on: {e}")))?;
        let port = listener
            .local_addr()
            .map_err(|e| SignInError::Local(e.to_string()))?
            .port();
        // Never `localhost`: the registration names 127.0.0.1.
        let redirect_uri = format!("http://127.0.0.1:{port}{CALLBACK_PATH}");
        let state = random(32)?;
        let nonce = random(32)?;
        let verifier = random(32)?;
        let mut params: Vec<(&str, &str)> = Vec::new();
        match &previous {
            Some(account) => {
                params.push(("client_id", &account.client_id));
                if let Some(email) = &account.email {
                    params.push(("login_hint", email));
                }
            }
            None => {
                params.push(("client_id", REGISTRATION_CLIENT));
                params.push(("agent_name_hint", APP_NAME));
            }
        }
        let code_challenge = challenge(&verifier);
        params.extend([
            ("ext_agent_host_id", host_id.as_str()),
            ("response_type", "code"),
            ("redirect_uri", &redirect_uri),
            ("scope", SCOPES),
            ("resource", ENDPOINT),
            ("state", &state),
            ("nonce", &nonce),
            ("code_challenge_method", "S256"),
            ("code_challenge", &code_challenge),
        ]);
        if options.ask_again {
            params.push(("prompt", "consent"));
        }
        let query: Vec<String> = params
            .iter()
            .map(|(name, value)| format!("{name}={}", encode(value)))
            .collect();
        let url = format!("{}?{}", endpoints.authorize_url(), query.join("&"));
        Ok(Self {
            listener,
            url,
            redirect_uri,
            state,
            nonce,
            verifier: Secret::new(verifier),
            previous,
            host_id,
            options,
            endpoints,
            client,
        })
    }

    /// The URL to open in the browser. It holds no token.
    pub fn url(&self) -> &str {
        &self.url
    }

    /// Whether the account signed in from this file before, so its issued
    /// client is reused.
    pub fn returning(&self) -> bool {
        self.previous.is_some()
    }

    /// Waits up to `wait` for the browser to come back, then completes the
    /// sign-in and saves it.
    pub fn finish(self, wait: Duration) -> Result<SignedIn, SignInError> {
        self.listener
            .set_nonblocking(true)
            .map_err(|e| SignInError::Local(e.to_string()))?;
        let deadline = Instant::now() + wait;
        loop {
            match self.listener.accept() {
                Ok((stream, peer)) => {
                    if let Some(done) = self.answer(stream, peer) {
                        return done;
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return Err(SignInError::TimedOut);
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
                Err(e) => return Err(SignInError::Local(e.to_string())),
            }
        }
    }

    /// Answers one request to the callback. `None` if it was not the
    /// browser's return from this sign-in, and the wait goes on.
    fn answer(
        &self,
        mut stream: TcpStream,
        peer: SocketAddr,
    ) -> Option<Result<SignedIn, SignInError>> {
        if !peer.ip().is_loopback() {
            return None;
        }
        let _ = stream.set_nonblocking(false);
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let target = read_target(&stream)?;
        let path = target.split('?').next().unwrap_or_default();
        if path != CALLBACK_PATH {
            respond(&mut stream, 404, "Not found.");
            return None;
        }
        let params = query(&target);
        let param = |name: &str| {
            params
                .iter()
                .find(|(k, _)| k == name)
                .map(|(_, v)| v.as_str())
        };
        // A request that does not carry this sign-in's state is not its
        // answer, whatever else it says.
        if param("state") != Some(self.state.as_str()) {
            respond(
                &mut stream,
                400,
                "This is not the sign-in Core Memory is waiting for. Return to the terminal.",
            );
            return None;
        }
        let result = self.complete(
            param("error"),
            param("code"),
            param("client_id"),
            param("scope"),
        );
        let page = match &result {
            Ok(signed) if signed.plan_granted => {
                "Core Memory is signed in and may use your ChatGPT plan. You can close this tab."
            }
            Ok(_) => {
                "Core Memory is signed in, but was not allowed to use your ChatGPT plan. Return \
                 to the terminal."
            }
            Err(_) => "The sign-in did not complete. Return to the terminal to see why.",
        };
        respond(&mut stream, 200, page);
        Some(result)
    }

    fn complete(
        &self,
        error: Option<&str>,
        code: Option<&str>,
        issued: Option<&str>,
        callback_scope: Option<&str>,
    ) -> Result<SignedIn, SignInError> {
        if let Some(error) = error {
            return Err(SignInError::Declined(
                plain(error).unwrap_or("error").into(),
            ));
        }
        let code = code
            .filter(|c| !c.is_empty())
            .ok_or_else(|| SignInError::Callback("no authorization code came back".into()))?;
        let client_id = match (&self.previous, issued) {
            // A returning account keeps its client. A callback naming
            // another would mix one registration's tokens with another's.
            (Some(account), Some(issued)) if issued != account.client_id => {
                return Err(SignInError::Callback(
                    "it named a different client than this account's".into(),
                ));
            }
            (Some(account), _) => account.client_id.clone(),
            (None, Some(issued)) if issued != REGISTRATION_CLIENT && plain(issued).is_some() => {
                issued.to_string()
            }
            (None, _) => {
                return Err(SignInError::Callback(
                    "the registration is incomplete: no client was issued".into(),
                ));
            }
        };
        let reply = self
            .client
            .post_form(
                &self.endpoints.token_url(),
                &[],
                &[
                    ("grant_type", "authorization_code"),
                    ("client_id", &client_id),
                    ("code", code),
                    ("code_verifier", self.verifier.expose()),
                    ("redirect_uri", &self.redirect_uri),
                    ("resource", ENDPOINT),
                ],
            )
            .map_err(network)?;
        if reply.status != 200 {
            return Err(SignInError::Exchange(
                error_code(&reply.body).unwrap_or_else(|| format!("status {}", reply.status)),
            ));
        }
        let tokens = Tokens::parse(&reply.body)
            .ok_or_else(|| SignInError::Exchange("the reply held no access token".into()))?;
        let id_token = tokens
            .id
            .clone()
            .ok_or_else(|| SignInError::IdToken("none was returned".into()))?;
        let jwks = self
            .client
            .get(&self.endpoints.jwks_url(), &[])
            .map_err(network)?;
        let jwks: Value = serde_json::from_str(&jwks.body)
            .ok()
            .filter(|_| jwks.status == 200)
            .ok_or_else(|| {
                SignInError::Network("OpenAI's signing keys could not be read".into())
            })?;
        let identity = idtoken::validate(
            id_token.expose(),
            &jwks,
            idtoken::Expected {
                issuer: &self.endpoints.issuer,
                client_id: &client_id,
                nonce: &self.nonce,
                now: OffsetDateTime::now_utc().unix_timestamp(),
            },
        )
        .map_err(SignInError::IdToken)?;
        if self
            .previous
            .as_ref()
            .is_some_and(|account| account.subject != identity.subject)
        {
            return Err(SignInError::OtherAccount);
        }
        let callback_scopes = || {
            callback_scope
                .map(|s| s.split_whitespace().map(str::to_string).collect())
                .unwrap_or_default()
        };
        let mut saved = Credentials {
            email: identity
                .email
                .or_else(|| self.previous.as_ref().and_then(|p| p.email.clone())),
            issuer: self.endpoints.issuer.clone(),
            subject: identity.subject,
            client_id,
            ext_agent_host_id: self.host_id.clone(),
            id_token: None,
            access_token: None,
            refresh_token: None,
            token_type: "Bearer".into(),
            expires_in: 0,
            earliest_refresh_at: None,
            scopes: callback_scopes(),
            saved_at: now_rfc3339(),
            background_use: None,
        };
        tokens.apply(&mut saved);
        if self.options.background_use {
            saved.background_use = Some(BackgroundUse {
                agreed_at: now_rfc3339(),
                host_id: self.host_id.clone(),
                statement: CONSENT_REVISION,
            });
        }
        credentials::save(&self.options.credentials, &saved)?;
        Ok(SignedIn {
            email: saved.email.clone(),
            plan_granted: saved.plan_granted(),
            first: self.previous.is_none(),
        })
    }
}

/// The request target of a GET, or `None` for anything else.
fn read_target(stream: &TcpStream) -> Option<String> {
    let mut reader = BufReader::new(stream.take(MAX_CALLBACK_BYTES as u64));
    let mut line = String::new();
    reader.read_line(&mut line).ok()?;
    let mut parts = line.split_whitespace();
    let (Some("GET"), Some(target)) = (parts.next(), parts.next()) else {
        return None;
    };
    // The rest of the head, so the reply does not cut the request off.
    loop {
        let mut header = String::new();
        match reader.read_line(&mut header) {
            Ok(0) | Err(_) => break,
            Ok(_) if header.trim_end().is_empty() => break,
            Ok(_) => {}
        }
    }
    Some(target.to_string())
}

/// A fixed page: nothing from the request is repeated in it.
fn respond(stream: &mut TcpStream, status: u16, text: &str) {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        _ => "Not Found",
    };
    let page = format!(
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>{APP_NAME}</title></head>\
         <body style=\"font-family: sans-serif; margin: 3em\"><p>{text}</p></body></html>"
    );
    let _ = write!(
        stream,
        "HTTP/1.1 {status} {reason}\r\ncontent-type: text/html; charset=utf-8\r\n\
         content-length: {}\r\ncache-control: no-store\r\nconnection: close\r\n\r\n{page}",
        page.len()
    );
    let _ = stream.flush();
}

/// What became of the renewable session when signing out.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Revocation {
    /// OpenAI confirmed it ended.
    Confirmed,
    /// It could not be confirmed. The tokens were cleared here anyway; the
    /// owner can disconnect the app in ChatGPT's settings.
    NotConfirmed(String),
    /// There was nothing to end: no refresh token was saved.
    NothingToEnd,
}

/// Signs out: ends the renewable session with OpenAI, then clears the
/// tokens and the background-use consent here. The account, its client and
/// this host's ID are kept for a later sign-in. `None` if there was no
/// sign-in to end.
pub fn sign_out(path: &Path, client: &HttpClient) -> Result<Option<Revocation>, SignInError> {
    sign_out_at(
        path,
        client,
        &Endpoints::openai(),
        &[Duration::from_secs(1), Duration::from_secs(3)],
    )
}

pub(crate) fn sign_out_at(
    path: &Path,
    client: &HttpClient,
    endpoints: &Endpoints,
    backoff: &[Duration],
) -> Result<Option<Revocation>, SignInError> {
    if credentials::load(path)?.is_none() {
        return Ok(None);
    }
    // No call renews the tokens while they are being ended.
    let _lock = credentials::lock(path, Duration::from_secs(30))?;
    let Some(mut saved) = credentials::load(path)? else {
        return Ok(None);
    };
    let revocation = match &saved.refresh_token {
        None => Revocation::NothingToEnd,
        Some(refresh) => revoke(client, endpoints, &saved.client_id, refresh, backoff),
    };
    saved.clear_tokens();
    credentials::save(path, &saved)?;
    Ok(Some(revocation))
}

/// Ends the session `refresh` renews, at the revocation endpoint OpenAI's
/// configuration names. Retries a failure to reach it, or a server error,
/// once per `backoff` entry.
fn revoke(
    client: &HttpClient,
    endpoints: &Endpoints,
    client_id: &str,
    refresh: &Secret,
    backoff: &[Duration],
) -> Revocation {
    let mut waits = backoff.iter();
    loop {
        let outcome = revoke_once(client, endpoints, client_id, refresh);
        match (outcome, waits.next()) {
            (Ok(revocation), _) => return revocation,
            (Err(_), Some(wait)) => std::thread::sleep(*wait),
            (Err(why), None) => return Revocation::NotConfirmed(why),
        }
    }
}

/// One try. `Err` for what is worth trying again.
fn revoke_once(
    client: &HttpClient,
    endpoints: &Endpoints,
    client_id: &str,
    refresh: &Secret,
) -> Result<Revocation, String> {
    let configuration = client
        .get(&endpoints.openid_configuration_url(), &[])
        .map_err(|e| format!("OpenAI could not be reached: {e}"))?;
    if configuration.status >= 500 {
        return Err(format!("OpenAI returned {}", configuration.status));
    }
    let configuration: Value = serde_json::from_str(&configuration.body).unwrap_or(Value::Null);
    let Some(endpoint) = configuration["revocation_endpoint"].as_str() else {
        return Ok(Revocation::NotConfirmed(
            "OpenAI's configuration names no revocation endpoint".into(),
        ));
    };
    // The refresh token goes to OpenAI's sign-in service and nowhere else.
    if !endpoint.starts_with(&format!("{}/", endpoints.auth)) {
        return Ok(Revocation::NotConfirmed(
            "OpenAI's configuration names a revocation endpoint elsewhere".into(),
        ));
    }
    let reply = client
        .post_form(
            endpoint,
            &[],
            &[
                ("token", refresh.expose()),
                ("token_type_hint", "refresh_token"),
                ("client_id", client_id),
            ],
        )
        .map_err(|e| format!("OpenAI could not be reached: {e}"))?;
    match reply.status {
        200 => Ok(Revocation::Confirmed),
        status @ 500..=599 => Err(format!("OpenAI returned {status}")),
        status => Ok(Revocation::NotConfirmed(format!(
            "OpenAI returned {status}{}",
            error_code(&reply.body)
                .map(|c| format!(", {c}"))
                .unwrap_or_default()
        ))),
    }
}

/// What an import saved.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportOutcome {
    pub email: Option<String>,
    pub plan_granted: bool,
    /// This machine's host ID, which the imported sign-in now carries.
    pub host_id: String,
}

/// Imports a sign-in made on another machine, as for a server with no
/// browser: the file at `source`, copied here over a secure channel, saved
/// at `target` with this machine's own host ID in place of the other's.
/// `background_use` is the owner's consent, given here, to calls made in
/// the background from this machine.
pub fn import_credentials(
    source: &Path,
    target: &Path,
    background_use: bool,
) -> Result<ImportOutcome, SignInError> {
    let bytes = std::fs::read(source).map_err(|e| {
        SignInError::Local(format!(
            "the file to import could not be read: {}",
            e.kind()
        ))
    })?;
    let mut imported = credentials::parse(&bytes)?;
    if !imported.signed_in() {
        return Err(SignInError::Local(
            "the file to import holds no tokens: sign in on the other machine first".into(),
        ));
    }
    // Made before the sign-in is saved, and kept: the copied file's host ID
    // is the other machine's.
    let host_id = credentials::ensure_host_id(target)?;
    let _lock = credentials::lock(target, Duration::from_secs(30))?;
    if let Some(existing) = credentials::load(target)?
        && (existing.subject != imported.subject || existing.client_id != imported.client_id)
    {
        return Err(SignInError::OtherAccount);
    }
    imported.ext_agent_host_id = host_id.clone();
    imported.background_use = background_use.then(|| BackgroundUse {
        agreed_at: now_rfc3339(),
        host_id: host_id.clone(),
        statement: CONSENT_REVISION,
    });
    credentials::save(target, &imported)?;
    Ok(ImportOutcome {
        email: imported.email.clone(),
        plan_granted: imported.plan_granted(),
        host_id,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::credentials::tests::sample;
    use crate::idtoken::tests::{claims, jwks, sign};
    use core_memory_model_http::testing::{Received, Reply, serve};
    use serde_json::json;
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};

    fn client() -> HttpClient {
        HttpClient::new(Duration::from_secs(5))
    }

    fn params(url: &str) -> HashMap<String, String> {
        query(url).into_iter().collect()
    }

    /// Plays the browser: comes back to the callback with `query`.
    fn come_back(redirect_uri: &str, query: &str) -> String {
        let address = redirect_uri
            .strip_prefix("http://")
            .unwrap()
            .split('/')
            .next()
            .unwrap()
            .to_string();
        let mut stream = TcpStream::connect(address).unwrap();
        write!(
            stream,
            "GET {CALLBACK_PATH}?{query} HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n"
        )
        .unwrap();
        let mut page = String::new();
        stream.read_to_string(&mut page).unwrap();
        page
    }

    struct Auth {
        url: String,
        received: Arc<Mutex<Vec<Received>>>,
        server: std::thread::JoinHandle<Vec<Received>>,
    }

    /// OpenAI's sign-in, standing in: a token endpoint answering with an ID
    /// token for `client` that carries the nonce it is told, and the keys.
    fn auth_server(client: &'static str, scope: &'static str, nonce: Arc<Mutex<String>>) -> Auth {
        let received = Arc::new(Mutex::new(Vec::new()));
        let seen = Arc::clone(&received);
        let base = Arc::new(Mutex::new(String::new()));
        let base_in = Arc::clone(&base);
        let (url, server) = serve(2, move |r: &Received| {
            seen.lock().unwrap().push(r.clone());
            let base = base_in.lock().unwrap().clone();
            if r.request_line.starts_with("POST /api/accounts/oauth/token") {
                let now = OffsetDateTime::now_utc().unix_timestamp();
                let id = sign("k1", &claims(&base, client, &nonce.lock().unwrap(), now));
                Reply::json(
                    200,
                    json!({
                        "access_token": "access-secret", "refresh_token": "refresh-secret",
                        "id_token": id, "token_type": "Bearer", "expires_in": 3600,
                        "scope": scope, "earliest_refresh_at": now + 60,
                    })
                    .to_string(),
                )
            } else {
                Reply::json(200, jwks("k1").to_string())
            }
        });
        *base.lock().unwrap() = url.clone();
        Auth {
            url,
            received,
            server,
        }
    }

    fn options(path: &Path) -> SignInOptions {
        SignInOptions {
            credentials: path.to_path_buf(),
            ask_again: false,
            port: Some(0),
            background_use: true,
        }
    }

    #[test]
    fn a_first_sign_in_registers_core_memory_and_saves_the_issued_client() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let nonce = Arc::new(Mutex::new(String::new()));
        let auth = auth_server("oaiapp_issued", SCOPES, Arc::clone(&nonce));
        let pending =
            PendingSignIn::start_at(options(&path), client(), Endpoints::at(&auth.url)).unwrap();
        assert!(!pending.returning());
        let asked = params(pending.url());
        assert!(
            pending
                .url()
                .starts_with(&format!("{}/api/accounts/authorize?", auth.url))
        );
        assert_eq!(asked["client_id"], "dynamic_agent_client");
        assert_eq!(asked["agent_name_hint"], "Core Memory");
        assert_eq!(asked["response_type"], "code");
        assert_eq!(asked["scope"], SCOPES);
        assert_eq!(asked["resource"], "https://api.openai.com/v1");
        assert_eq!(asked["code_challenge_method"], "S256");
        assert!(asked["redirect_uri"].starts_with("http://127.0.0.1:"));
        assert!(asked["redirect_uri"].ends_with("/auth/callback"));
        // The host ID was made and saved before the sign-in.
        let host = credentials::host_id(&path).unwrap().unwrap();
        assert_eq!(asked["ext_agent_host_id"], host);
        assert!(!asked.contains_key("prompt"));
        assert!(!asked.contains_key("id_token_hint"));
        assert_eq!(asked["state"].len(), 43);
        assert_ne!(asked["state"], asked["nonce"]);
        *nonce.lock().unwrap() = asked["nonce"].clone();

        let redirect = asked["redirect_uri"].clone();
        let state = asked["state"].clone();
        let browser = std::thread::spawn(move || {
            // A request that is not this sign-in's answer is turned away
            // and the wait goes on.
            let stray = come_back(&redirect, "code=c&state=forged&client_id=oaiapp_issued");
            assert!(stray.starts_with("HTTP/1.1 400"), "{stray}");
            come_back(
                &redirect,
                &format!(
                    "code=the-code&state={state}&client_id=oaiapp_issued&scope={}",
                    encode(SCOPES)
                ),
            )
        });
        let signed = pending.finish(Duration::from_secs(10)).unwrap();
        let page = browser.join().unwrap();
        assert!(page.contains("may use your ChatGPT plan"), "{page}");
        assert_eq!(
            signed,
            SignedIn {
                email: Some("user@example.com".into()),
                plan_granted: true,
                first: true,
            }
        );
        auth.server.join().unwrap();
        let received = auth.received.lock().unwrap();
        let exchange = &received[0].body;
        let form: HashMap<String, String> = query(&format!("?{exchange}")).into_iter().collect();
        assert_eq!(form["grant_type"], "authorization_code");
        // The issued client, not the registration one.
        assert_eq!(form["client_id"], "oaiapp_issued");
        assert_eq!(form["code"], "the-code");
        assert_eq!(form["redirect_uri"], asked["redirect_uri"]);
        assert_eq!(form["resource"], "https://api.openai.com/v1");
        // The verifier answers the challenge sent.
        assert_eq!(challenge(&form["code_verifier"]), asked["code_challenge"]);
        assert!(!form.contains_key("client_secret"));
        assert_eq!(
            received[1].request_line,
            "GET /.well-known/jwks.json HTTP/1.1"
        );

        let saved = credentials::load(&path).unwrap().unwrap();
        assert_eq!(saved.client_id, "oaiapp_issued");
        assert_eq!(saved.subject, "user-123");
        assert_eq!(saved.ext_agent_host_id, host);
        assert!(saved.plan_granted());
        assert!(saved.background_use_agreed(&host));
        assert_eq!(saved.refresh_token, Some(Secret::new("refresh-secret")));
        assert!(saved.earliest_refresh_at.is_some());
    }

    #[test]
    fn a_returning_account_reuses_its_client_and_must_be_the_same_account() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let host = credentials::ensure_host_id(&path).unwrap();
        let mut before = sample(&host);
        before.client_id = "oaiapp_issued".into();
        before.clear_tokens();
        credentials::save(&path, &before).unwrap();

        let nonce = Arc::new(Mutex::new(String::new()));
        let auth = auth_server("oaiapp_issued", SCOPES, Arc::clone(&nonce));
        let mut asking = options(&path);
        asking.ask_again = true;
        let pending = PendingSignIn::start_at(asking, client(), Endpoints::at(&auth.url)).unwrap();
        assert!(pending.returning());
        let asked = params(pending.url());
        assert_eq!(asked["client_id"], "oaiapp_issued");
        assert_eq!(asked["login_hint"], "user@example.com");
        assert_eq!(asked["prompt"], "consent");
        assert!(!asked.contains_key("agent_name_hint"));
        assert_eq!(asked["ext_agent_host_id"], host);
        *nonce.lock().unwrap() = asked["nonce"].clone();
        let (redirect, state) = (asked["redirect_uri"].clone(), asked["state"].clone());
        // A returning sign-in may leave the client out of the callback.
        let browser =
            std::thread::spawn(move || come_back(&redirect, &format!("code=c&state={state}")));
        let signed = pending.finish(Duration::from_secs(10)).unwrap();
        browser.join().unwrap();
        assert!(!signed.first);
        auth.server.join().unwrap();
        assert_eq!(
            credentials::load(&path).unwrap().unwrap().client_id,
            "oaiapp_issued"
        );

        // Another account in the browser is refused and nothing is saved.
        let mut other = before.clone();
        other.subject = "someone-else".into();
        credentials::save(&path, &other).unwrap();
        let auth = auth_server("oaiapp_issued", SCOPES, Arc::clone(&nonce));
        let pending =
            PendingSignIn::start_at(options(&path), client(), Endpoints::at(&auth.url)).unwrap();
        let asked = params(pending.url());
        *nonce.lock().unwrap() = asked["nonce"].clone();
        let (redirect, state) = (asked["redirect_uri"].clone(), asked["state"].clone());
        let browser =
            std::thread::spawn(move || come_back(&redirect, &format!("code=c&state={state}")));
        assert_eq!(
            pending.finish(Duration::from_secs(10)).unwrap_err(),
            SignInError::OtherAccount
        );
        browser.join().unwrap();
        assert_eq!(credentials::load(&path).unwrap().unwrap(), other);
    }

    #[test]
    fn a_callback_naming_another_client_or_none_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        // A first sign-in that comes back with no issued client.
        let path = dir.path().join("first.json");
        let pending = PendingSignIn::start_at(
            options(&path),
            client(),
            Endpoints::at("http://127.0.0.1:9"),
        )
        .unwrap();
        let asked = params(pending.url());
        let (redirect, state) = (asked["redirect_uri"].clone(), asked["state"].clone());
        let browser = std::thread::spawn(move || {
            come_back(
                &redirect,
                &format!("code=c&state={state}&client_id=dynamic_agent_client"),
            )
        });
        let err = pending.finish(Duration::from_secs(10)).unwrap_err();
        assert!(matches!(err, SignInError::Callback(_)), "{err:?}");
        browser.join().unwrap();
        assert_eq!(credentials::load(&path).unwrap(), None);

        // A returning account whose callback names another client.
        let path = dir.path().join("returning.json");
        let host = credentials::ensure_host_id(&path).unwrap();
        credentials::save(&path, &sample(&host)).unwrap();
        let pending = PendingSignIn::start_at(
            options(&path),
            client(),
            Endpoints::at("http://127.0.0.1:9"),
        )
        .unwrap();
        let asked = params(pending.url());
        let (redirect, state) = (asked["redirect_uri"].clone(), asked["state"].clone());
        let browser = std::thread::spawn(move || {
            come_back(
                &redirect,
                &format!("code=c&state={state}&client_id=oaiapp_other"),
            )
        });
        let err = pending.finish(Duration::from_secs(10)).unwrap_err();
        assert!(err.to_string().contains("different client"), "{err}");
        browser.join().unwrap();
    }

    #[test]
    fn declining_in_the_browser_saves_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let pending = PendingSignIn::start_at(
            options(&path),
            client(),
            Endpoints::at("http://127.0.0.1:9"),
        )
        .unwrap();
        let asked = params(pending.url());
        let (redirect, state) = (asked["redirect_uri"].clone(), asked["state"].clone());
        let browser = std::thread::spawn(move || {
            come_back(&redirect, &format!("error=access_denied&state={state}"))
        });
        assert_eq!(
            pending.finish(Duration::from_secs(10)).unwrap_err(),
            SignInError::Declined("access_denied".into())
        );
        browser.join().unwrap();
        assert_eq!(credentials::load(&path).unwrap(), None);
    }

    #[test]
    fn without_the_plan_scope_the_sign_in_is_kept_but_says_so() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let nonce = Arc::new(Mutex::new(String::new()));
        let auth = auth_server("oaiapp_issued", "openid profile email", Arc::clone(&nonce));
        let pending =
            PendingSignIn::start_at(options(&path), client(), Endpoints::at(&auth.url)).unwrap();
        let asked = params(pending.url());
        *nonce.lock().unwrap() = asked["nonce"].clone();
        let (redirect, state) = (asked["redirect_uri"].clone(), asked["state"].clone());
        let browser = std::thread::spawn(move || {
            come_back(
                &redirect,
                &format!("code=c&state={state}&client_id=oaiapp_issued"),
            )
        });
        let signed = pending.finish(Duration::from_secs(10)).unwrap();
        let page = browser.join().unwrap();
        assert!(!signed.plan_granted);
        assert!(
            page.contains("not allowed to use your ChatGPT plan"),
            "{page}"
        );
        auth.server.join().unwrap();
        assert!(!credentials::load(&path).unwrap().unwrap().plan_granted());
    }

    #[test]
    fn an_id_token_for_another_sign_in_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        // The token endpoint answers with a nonce that is not this sign-in's.
        let nonce = Arc::new(Mutex::new("stale-nonce".to_string()));
        let auth = auth_server("oaiapp_issued", SCOPES, nonce);
        let pending =
            PendingSignIn::start_at(options(&path), client(), Endpoints::at(&auth.url)).unwrap();
        let asked = params(pending.url());
        let (redirect, state) = (asked["redirect_uri"].clone(), asked["state"].clone());
        let browser = std::thread::spawn(move || {
            come_back(
                &redirect,
                &format!("code=c&state={state}&client_id=oaiapp_issued"),
            )
        });
        let err = pending.finish(Duration::from_secs(10)).unwrap_err();
        assert!(
            matches!(&err, SignInError::IdToken(why) if why.contains("nonce")),
            "{err:?}"
        );
        browser.join().unwrap();
        auth.server.join().unwrap();
        assert_eq!(credentials::load(&path).unwrap(), None);
        // Nothing the token endpoint returned is in the message.
        assert!(!err.to_string().contains("secret"));
    }

    #[test]
    fn a_sign_in_nobody_finishes_times_out() {
        let dir = tempfile::tempdir().unwrap();
        let pending = PendingSignIn::start_at(
            options(&dir.path().join("chatgpt.json")),
            client(),
            Endpoints::at("http://127.0.0.1:9"),
        )
        .unwrap();
        assert_eq!(
            pending.finish(Duration::from_millis(100)).unwrap_err(),
            SignInError::TimedOut
        );
    }

    fn revocation_server(statuses: Vec<u16>) -> (String, std::thread::JoinHandle<Vec<Received>>) {
        let count = statuses.len() * 2;
        let statuses = Arc::new(Mutex::new(statuses.into_iter()));
        let base = Arc::new(Mutex::new(String::new()));
        let base_in = Arc::clone(&base);
        let (url, server) = serve(count, move |r: &Received| {
            if r.request_line
                .starts_with("GET /.well-known/openid-configuration")
            {
                let base = base_in.lock().unwrap().clone();
                Reply::json(
                    200,
                    json!({"revocation_endpoint": format!("{base}/oauth/revoke")}).to_string(),
                )
            } else {
                let status = statuses.lock().unwrap().next().unwrap();
                Reply::json(status, "")
            }
        });
        *base.lock().unwrap() = url.clone();
        (url, server)
    }

    #[test]
    fn signing_out_revokes_then_clears_the_tokens_but_keeps_the_client_and_host() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let host = credentials::ensure_host_id(&path).unwrap();
        credentials::save(&path, &sample(&host)).unwrap();
        let (url, server) = revocation_server(vec![503, 200]);
        let outcome = sign_out_at(
            &path,
            &client(),
            &Endpoints::at(&url),
            &[Duration::from_millis(10)],
        )
        .unwrap();
        assert_eq!(outcome, Some(Revocation::Confirmed));
        let received = server.join().unwrap();
        let form = &received[3].body;
        assert!(form.contains("token=refresh-token-secret"), "{form}");
        assert!(form.contains("token_type_hint=refresh_token"), "{form}");
        assert!(form.contains("client_id=oaiapp_test"), "{form}");
        let saved = credentials::load(&path).unwrap().unwrap();
        assert!(!saved.signed_in());
        assert_eq!(saved.id_token, None);
        assert_eq!(saved.background_use, None);
        assert_eq!(saved.client_id, "oaiapp_test");
        assert_eq!(saved.ext_agent_host_id, host);
        assert_eq!(credentials::host_id(&path).unwrap(), Some(host));
        // Signing out again has nothing to end.
        assert_eq!(
            sign_out_at(&path, &client(), &Endpoints::at(&url), &[]).unwrap(),
            Some(Revocation::NothingToEnd)
        );
    }

    #[test]
    fn an_unconfirmed_revocation_still_clears_the_tokens_and_says_so() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let host = credentials::ensure_host_id(&path).unwrap();
        credentials::save(&path, &sample(&host)).unwrap();
        // Nothing listens: every try fails to connect.
        let outcome = sign_out_at(
            &path,
            &client(),
            &Endpoints::at("http://127.0.0.1:9"),
            &[Duration::from_millis(10)],
        )
        .unwrap();
        assert!(
            matches!(outcome, Some(Revocation::NotConfirmed(_))),
            "{outcome:?}"
        );
        assert!(!credentials::load(&path).unwrap().unwrap().signed_in());
        // No file, no sign-in to end.
        assert_eq!(
            sign_out_at(
                &dir.path().join("none.json"),
                &client(),
                &Endpoints::at("http://127.0.0.1:9"),
                &[]
            )
            .unwrap(),
            None
        );
    }

    #[test]
    fn an_import_keeps_this_machines_host_id_and_asks_its_own_consent() {
        let laptop = tempfile::tempdir().unwrap();
        let laptop_file = laptop.path().join("chatgpt.json");
        let laptop_host = credentials::ensure_host_id(&laptop_file).unwrap();
        credentials::save(&laptop_file, &sample(&laptop_host)).unwrap();

        let server = tempfile::tempdir().unwrap();
        let target = server.path().join("chatgpt.json");
        let outcome = import_credentials(&laptop_file, &target, true).unwrap();
        assert_ne!(outcome.host_id, laptop_host);
        assert_eq!(
            credentials::host_id(&target).unwrap(),
            Some(outcome.host_id.clone())
        );
        let saved = credentials::load(&target).unwrap().unwrap();
        assert_eq!(saved.ext_agent_host_id, outcome.host_id);
        assert!(saved.background_use_agreed(&outcome.host_id));
        assert_eq!(
            saved.refresh_token,
            Some(Secret::new("refresh-token-secret"))
        );

        // Without consent given here, it is imported but no call is made.
        let quiet = server.path().join("quiet.json");
        import_credentials(&laptop_file, &quiet, false).unwrap();
        assert_eq!(
            credentials::load(&quiet).unwrap().unwrap().background_use,
            None
        );

        // A different account is not imported over this one.
        let mut other = sample(&laptop_host);
        other.subject = "someone-else".into();
        let other_file = laptop.path().join("other.json");
        credentials::save(&other_file, &other).unwrap();
        assert_eq!(
            import_credentials(&other_file, &target, true).unwrap_err(),
            SignInError::OtherAccount
        );
    }

    #[test]
    fn query_values_round_trip() {
        let value = "a b&c=d/é+";
        assert_eq!(decode(&encode(value)).as_deref(), Some(value));
        assert_eq!(decode("a+b%2Bc").as_deref(), Some("a b+c"));
        assert_eq!(decode("%zz"), None);
        let pairs = query("/auth/callback?code=x%20y&state=s");
        assert_eq!(
            pairs,
            [("code".into(), "x y".into()), ("state".into(), "s".into())]
        );
    }
}

//! `core-memory serve`: the engine over HTTP/JSON.
//!
//! Callers authenticate with a bearer token; each token carries its own
//! scope grants (SEC-01): the scopes it may write, and the scopes it may
//! recall from. Each admission is one short transaction on a pooled store
//! connection. What it commits is the record, so there is nothing to
//! deliver afterwards and nothing to drain on shutdown.
//!
//! The server speaks plain HTTP/1.1. It belongs on the loopback interface
//! or behind a proxy that terminates TLS.

use std::cell::Cell;
use std::fmt;
use std::future::Future;
use std::io;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Duration;

use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::rejection::QueryRejection;
use axum::extract::{Path, Query, Request, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use core_memory_contract::schema::Contract;
use core_memory_contract::{
    AdmissionOutcome, BusyCause, FieldError, RecallRequest, RefusalReason, Sha256Digest,
    SourceEvent,
};
use core_memory_core::store::Store;
use core_memory_core::{Engine, EngineError, RecallQuery, ScopeGrants, StoreError};
use core_memory_store_postgres::{CheckoutError, PostgresPool, SCHEMA_VERSION};
use schemars::JsonSchema;
use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tower_service::Service;

use crate::config::{HttpConfig, Loaded, grants_from};
use crate::openapi;
use crate::reading::Reader;
use crate::report::{self, Stop};
use crate::runtime::{CHECKOUT_WAIT, build_engine, open_pool};

/// A configured caller.
#[derive(Debug)]
struct Token {
    hash: Sha256Digest,
    grants: ScopeGrants,
    recall: ScopeGrants,
}

/// The scopes the request's token may write, set once it is authenticated.
#[derive(Debug, Clone)]
struct Grants(ScopeGrants);

/// The scopes the request's token may recall from, set with [`Grants`].
#[derive(Debug, Clone)]
struct Recalls(ScopeGrants);

/// Shared server state.
#[derive(Debug)]
pub struct AppState {
    engine: Arc<Engine>,
    pool: PostgresPool,
    tokens: Vec<Token>,
    max_batch: usize,
    max_body_bytes: usize,
    busy_retry_after_ms: u64,
    read_timeout: Duration,
}

/// Why a request could not be answered with a typed outcome.
enum Failure {
    /// The store cannot take the request right now: it is unreachable or
    /// busy, its schema is being upgraded, or it refuses this engine's
    /// connection. The request is not at fault, and the caller may offer
    /// it again. The last two need whoever runs the engine to act, which
    /// `/v1/ready` and the log tell them.
    Unavailable(String),
    Internal(String),
}

impl From<CheckoutError> for Failure {
    fn from(e: CheckoutError) -> Self {
        match e {
            CheckoutError::Busy => Failure::Unavailable(e.to_string()),
            CheckoutError::Store(e) => e.into(),
        }
    }
}

impl From<StoreError> for Failure {
    fn from(e: StoreError) -> Self {
        match e {
            StoreError::Unavailable(_)
            | StoreError::SchemaMismatch(_)
            | StoreError::AccessRefused(_) => Failure::Unavailable(e.to_string()),
            other => Failure::Internal(other.to_string()),
        }
    }
}

impl From<EngineError> for Failure {
    fn from(e: EngineError) -> Self {
        match e {
            EngineError::Store(e) => e.into(),
            other => Failure::Internal(other.to_string()),
        }
    }
}

impl AppState {
    /// The token presented in `Authorization: Bearer ...`, compared by
    /// SHA-256 against every configured token without stopping early.
    fn authorize(&self, headers: &HeaderMap) -> Option<&Token> {
        let presented = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.split_once(' '))
            // The scheme name is case-insensitive (RFC 9110).
            .filter(|(scheme, _)| scheme.eq_ignore_ascii_case("bearer"))
            .map(|(_, t)| Sha256Digest::of(t.trim().as_bytes()))?;
        let mut found = None;
        for token in &self.tokens {
            if constant_time_eq(token.hash.as_bytes(), presented.as_bytes()) {
                found = Some(token);
            }
        }
        found
    }

    /// Admits one event. A store connection is held only for each store
    /// call, not while the event is validated, chunked and faceted.
    fn admit(
        &self,
        grants: &ScopeGrants,
        event: &SourceEvent,
    ) -> Result<AdmissionOutcome, Failure> {
        let mut store = self.pool.store(CHECKOUT_WAIT);
        Ok(self.engine.admit(&mut store, grants, event)?)
    }

    fn busy(&self) -> AdmissionOutcome {
        AdmissionOutcome::Busy {
            retry_after_ms: self.busy_retry_after_ms,
            cause: BusyCause::Capacity,
        }
    }

    /// The outcome for an admission that failed: `busy` when the store may
    /// take the event later, otherwise an internal error. The cause goes to
    /// the log, never to the caller.
    fn failed(&self, failure: Failure) -> Result<AdmissionOutcome, Box<Response>> {
        match failure {
            Failure::Unavailable(cause) => {
                report::log(serde_json::json!({"store_unavailable": cause}));
                Ok(self.busy())
            }
            Failure::Internal(cause) => Err(Box::new(internal(cause))),
        }
    }

    /// Reads a request body of at most `max_body_bytes`, within the read
    /// timeout.
    async fn read_body(&self, headers: &HeaderMap, body: Body) -> Result<Bytes, Box<Response>> {
        // Each of these answers before the body has been read to its end,
        // so the connection cannot carry another request.
        let too_large = || {
            Box::new(closing(error(
                StatusCode::PAYLOAD_TOO_LARGE,
                "body_too_large",
                format!(
                    "the request body is limited to {} bytes",
                    self.max_body_bytes
                ),
            )))
        };
        // A declared length over the limit is refused before a byte of the
        // body is read.
        let declared = headers
            .get(header::CONTENT_LENGTH)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.parse::<u64>().ok());
        if declared.is_some_and(|len| len > self.max_body_bytes as u64) {
            return Err(too_large());
        }
        let read = axum::body::to_bytes(body, self.max_body_bytes);
        match tokio::time::timeout(self.read_timeout, read).await {
            Ok(Ok(bytes)) => Ok(bytes),
            Ok(Err(e)) => {
                let over = std::error::Error::source(&e)
                    .is_some_and(|cause| cause.is::<http_body_util::LengthLimitError>());
                if over {
                    Err(too_large())
                } else {
                    Err(Box::new(closing(error(
                        StatusCode::BAD_REQUEST,
                        "malformed",
                        "the request body could not be read",
                    ))))
                }
            }
            Err(_) => Err(Box::new(closing(error(
                StatusCode::REQUEST_TIMEOUT,
                "request_timeout",
                "the request body did not arrive in time",
            )))),
        }
    }
}

fn constant_time_eq(a: &[u8; 32], b: &[u8; 32]) -> bool {
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// A server ready to run.
#[derive(Debug)]
pub struct Prepared {
    pub router: Router,
    pub listen: String,
    pub limits: ServeLimits,
    /// The reader to run beside the server, if one is configured.
    pub reader: Option<Reader>,
}

/// Limits on connections, as opposed to requests.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ServeLimits {
    /// How long a connection may take to send a request's headers, and how
    /// long an idle connection is kept.
    pub read_timeout: Duration,
    /// How long a response may wait for its client to read.
    pub write_timeout: Duration,
    /// How long requests in flight get to finish after a shutdown signal.
    pub shutdown_grace: Duration,
    /// Connections served at once. More wait to be accepted.
    pub max_connections: usize,
}

impl ServeLimits {
    fn from_config(http: &HttpConfig) -> Self {
        Self {
            read_timeout: Duration::from_millis(http.read_timeout_ms),
            write_timeout: Duration::from_millis(http.write_timeout_ms),
            shutdown_grace: Duration::from_millis(http.shutdown_grace_ms),
            max_connections: http.max_connections,
        }
    }
}

/// Builds the engine and the router for `loaded`. The store must be
/// reachable and at this build's schema version, and every profile revision
/// must match what the store has pinned.
pub fn prepare(loaded: &Loaded) -> Result<Prepared, Stop> {
    let http = loaded
        .config
        .http
        .as_ref()
        .ok_or("add an [http] section with at least one [[http.token]] to serve")?;
    if http.tokens.is_empty() {
        return Err(
            "configure at least one [[http.token]]; unauthenticated serving is not supported"
                .into(),
        );
    }
    let engine = Arc::new(build_engine(loaded)?);
    let pool = open_pool(loaded)?;
    {
        let mut store = pool.get(CHECKOUT_WAIT)?;
        store.check()?;
        engine.pin_profiles(&mut *store)?;
    }
    let reader = Reader::open(loaded, Arc::clone(&engine))?;
    let tokens = http
        .tokens
        .iter()
        .map(|t| Token {
            hash: t.sha256,
            grants: grants_from(&t.scopes),
            recall: grants_from(&t.recall),
        })
        .collect();
    let limits = ServeLimits::from_config(http);
    let state = Arc::new(AppState {
        engine,
        pool,
        tokens,
        max_batch: http.max_batch_events,
        max_body_bytes: http.max_body_bytes,
        busy_retry_after_ms: http.busy_retry_after_ms,
        read_timeout: limits.read_timeout,
    });
    Ok(Prepared {
        router: router(state),
        listen: http.listen.clone(),
        limits,
        reader,
    })
}

pub fn router(state: Arc<AppState>) -> Router {
    // The token is checked before a handler runs, and so before any of the
    // request body is read.
    let authenticated = Router::new()
        .route("/v1/events", post(post_event))
        .route("/v1/events/batch", post(post_batch))
        .route("/v1/checkpoints", get(get_checkpoint))
        .route("/v1/recall", post(post_recall))
        .route_layer(middleware::from_fn_with_state(
            Arc::clone(&state),
            authenticate,
        ));
    Router::new()
        .merge(authenticated)
        .route("/v1/ready", get(get_ready))
        .route(
            "/v1/health",
            get(|| async { axum::Json(serde_json::json!({"status": "ok"})) }),
        )
        .route(
            "/v1/openapi.json",
            get(|| async { axum::Json(openapi::document()) }),
        )
        .route("/v1/schemas/{name}", get(get_schema))
        .layer(middleware::map_response(close_unless_authenticated))
        .with_state(state)
}

/// Marks the answer to a request that showed a valid token.
#[derive(Debug, Clone, Copy)]
struct Authenticated;

/// Ends the connection of a request that showed no valid token: one for a
/// route that needs none, for a path or a method that is not served, or
/// with a token that was refused.
///
/// A connection is kept open between requests only for a caller that has
/// shown a token. Without one, asking again and again would hold one of
/// the places `max_connections` allows for as long as the caller liked.
async fn close_unless_authenticated(response: Response) -> Response {
    if response.extensions().get::<Authenticated>().is_some() {
        response
    } else {
        closing(response)
    }
}

async fn authenticate(
    State(state): State<Arc<AppState>>,
    mut request: Request,
    next: Next,
) -> Response {
    let Some(token) = state.authorize(request.headers()) else {
        let mut response = error(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "a valid bearer token is required",
        );
        response
            .headers_mut()
            .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
        // The request's body is not read, so the connection ends here.
        return closing(response);
    };
    let grants = Grants(token.grants.clone());
    request.extensions_mut().insert(grants);
    request
        .extensions_mut()
        .insert(Recalls(token.recall.clone()));
    let mut response = next.run(request).await;
    response.extensions_mut().insert(Authenticated);
    response
}

/// The grants the authentication layer attached to a request.
fn grants_of(request: &Request) -> Result<ScopeGrants, Box<Response>> {
    request
        .extensions()
        .get::<Grants>()
        .map(|g| g.0.clone())
        .ok_or_else(|| Box::new(internal("a request reached a handler unauthenticated")))
}

/// Body of `POST /v1/events/batch`.
#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BatchRequest {
    pub events: Vec<SourceEvent>,
}

/// Why a batch body was not read.
enum BatchError {
    TooMany,
    Malformed(serde_json::Error),
}

/// Reads `{"events": [...]}`, keeping each event as raw JSON so one bad
/// event does not refuse the batch, and stopping as soon as there are more
/// than `max` of them.
fn parse_batch(body: &[u8], max: usize) -> Result<Vec<Box<RawValue>>, BatchError> {
    let too_many = Cell::new(false);
    let mut de = serde_json::Deserializer::from_slice(body);
    let parsed = BatchSeed {
        max,
        too_many: &too_many,
    }
    .deserialize(&mut de)
    .and_then(|events| de.end().map(|()| events));
    match parsed {
        Ok(events) => Ok(events),
        Err(_) if too_many.get() => Err(BatchError::TooMany),
        Err(e) => Err(BatchError::Malformed(e)),
    }
}

#[derive(Clone, Copy)]
struct BatchSeed<'a> {
    max: usize,
    too_many: &'a Cell<bool>,
}

impl<'de> DeserializeSeed<'de> for BatchSeed<'_> {
    type Value = Vec<Box<RawValue>>;

    fn deserialize<D: serde::Deserializer<'de>>(self, d: D) -> Result<Self::Value, D::Error> {
        d.deserialize_map(self)
    }
}

impl<'de> Visitor<'de> for BatchSeed<'_> {
    type Value = Vec<Box<RawValue>>;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("an object with an `events` array")
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
        let mut events = None;
        while let Some(key) = map.next_key::<String>()? {
            if key != "events" {
                return Err(de::Error::custom(format!("unknown field `{key}`")));
            }
            if events.is_some() {
                return Err(de::Error::custom("duplicate key `events`"));
            }
            events = Some(map.next_value_seed(EventsSeed(self))?);
        }
        events.ok_or_else(|| de::Error::custom("missing field `events`"))
    }
}

struct EventsSeed<'a>(BatchSeed<'a>);

impl<'de> DeserializeSeed<'de> for EventsSeed<'_> {
    type Value = Vec<Box<RawValue>>;

    fn deserialize<D: serde::Deserializer<'de>>(self, d: D) -> Result<Self::Value, D::Error> {
        d.deserialize_seq(self)
    }
}

impl<'de> Visitor<'de> for EventsSeed<'_> {
    type Value = Vec<Box<RawValue>>;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("an array of events")
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
        let mut events = Vec::new();
        while let Some(event) = seq.next_element::<Box<RawValue>>()? {
            if events.len() == self.0.max {
                self.0.too_many.set(true);
                return Err(de::Error::custom("too many events"));
            }
            events.push(event);
        }
        Ok(events)
    }
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct BatchResponse {
    /// One result per event, in request order.
    pub results: Vec<AdmissionOutcome>,
}

/// Query of `GET /v1/checkpoints`.
#[derive(Debug, Deserialize, JsonSchema)]
pub struct CheckpointQuery {
    pub scope: String,
    pub key: String,
}

/// A reader's stored position.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct CheckpointResponse {
    pub scope: String,
    pub key: String,
    pub sequence: u64,
    pub hash: Sha256Digest,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ReadyResponse {
    pub ready: bool,
    /// The schema version this build needs.
    pub schema_version: u32,
    /// Why the engine is not ready: `store_unavailable`, `store_refused`
    /// (the database will not open a session for the engine as it is
    /// configured) or `schema_mismatch`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub problem: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ErrorResponse {
    pub error: ErrorDetail,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ErrorDetail {
    pub code: String,
    pub message: String,
}

fn error(status: StatusCode, code: &str, message: impl Into<String>) -> Response {
    (
        status,
        axum::Json(ErrorResponse {
            error: ErrorDetail {
                code: code.into(),
                message: message.into(),
            },
        }),
    )
        .into_response()
}

/// Marks a response as the last on its connection. The server closes the
/// connection once it is sent, and the client is told not to reuse it.
fn closing(mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::CONNECTION, HeaderValue::from_static("close"));
    response
}

/// The start of a parser's message. It can quote what was sent, at any
/// length, and an answer must not grow with the request it refuses.
fn brief(e: impl std::fmt::Display) -> String {
    const MOST: usize = 300;
    let text = e.to_string();
    if text.chars().count() <= MOST {
        return text;
    }
    let mut cut: String = text.chars().take(MOST).collect();
    cut.push_str("...");
    cut
}

fn internal(e: impl std::fmt::Display) -> Response {
    report::log(serde_json::json!({"internal_error": e.to_string()}));
    error(
        StatusCode::INTERNAL_SERVER_ERROR,
        "internal",
        "the engine could not complete the request",
    )
}

fn malformed(field: &str, problem: impl std::fmt::Display) -> AdmissionOutcome {
    AdmissionOutcome::Refused {
        reason: RefusalReason::Malformed,
        problems: vec![FieldError::new(field, problem.to_string())],
    }
}

/// Whether `value` can name a scope or a checkpoint: not empty, not
/// over-long, and free of control characters, as an event's own fields are.
fn is_name(value: &str, max_bytes: usize) -> bool {
    !value.is_empty() && value.len() <= max_bytes && !value.chars().any(char::is_control)
}

/// HTTP status for an admission outcome. The body is always the outcome.
pub fn status_for(outcome: &AdmissionOutcome) -> StatusCode {
    match outcome {
        AdmissionOutcome::Accepted { .. } | AdmissionOutcome::Replay { .. } => StatusCode::OK,
        AdmissionOutcome::IdentityConflict { .. } | AdmissionOutcome::CheckpointConflict { .. } => {
            StatusCode::CONFLICT
        }
        AdmissionOutcome::Busy { .. } => StatusCode::SERVICE_UNAVAILABLE,
        AdmissionOutcome::Refused { reason, .. } => match reason {
            RefusalReason::ScopeDenied | RefusalReason::ScopeNotActive => StatusCode::FORBIDDEN,
            RefusalReason::Tombstoned => StatusCode::GONE,
            RefusalReason::Oversize => StatusCode::PAYLOAD_TOO_LARGE,
            RefusalReason::InvalidProfile => StatusCode::UNPROCESSABLE_ENTITY,
            RefusalReason::Malformed => StatusCode::BAD_REQUEST,
        },
    }
}

/// A `Retry-After` value: whole seconds, at least one.
fn retry_after(ms: u64) -> HeaderValue {
    HeaderValue::from(ms.div_ceil(1000).max(1))
}

fn admission_response(outcome: AdmissionOutcome) -> Response {
    let status = status_for(&outcome);
    let mut response = (status, axum::Json(&outcome)).into_response();
    if let AdmissionOutcome::Busy { retry_after_ms, .. } = outcome {
        response
            .headers_mut()
            .insert(header::RETRY_AFTER, retry_after(retry_after_ms));
    }
    response
}

async fn post_event(State(state): State<Arc<AppState>>, request: Request) -> Response {
    let grants = match grants_of(&request) {
        Ok(grants) => grants,
        Err(response) => return *response,
    };
    let (parts, body) = request.into_parts();
    let body = match state.read_body(&parts.headers, body).await {
        Ok(body) => body,
        Err(response) => return *response,
    };
    let st = Arc::clone(&state);
    // Parsing is done off the async workers too: its cost grows with the
    // body, and a worker that parses answers nobody else meanwhile.
    let result =
        tokio::task::spawn_blocking(move || match serde_json::from_slice::<SourceEvent>(&body) {
            Ok(event) => st.admit(&grants, &event),
            Err(e) => Ok(malformed("body", e)),
        })
        .await;
    let outcome = match result {
        Ok(Ok(outcome)) => Ok(outcome),
        Ok(Err(failure)) => state.failed(failure),
        Err(e) => Err(Box::new(internal(e))),
    };
    match outcome {
        Ok(outcome) => admission_response(outcome),
        Err(response) => *response,
    }
}

async fn post_batch(State(state): State<Arc<AppState>>, request: Request) -> Response {
    let grants = match grants_of(&request) {
        Ok(grants) => grants,
        Err(response) => return *response,
    };
    let (parts, body) = request.into_parts();
    let body = match state.read_body(&parts.headers, body).await {
        Ok(body) => body,
        Err(response) => return *response,
    };
    let st = Arc::clone(&state);
    let result = tokio::task::spawn_blocking(move || {
        let events = match parse_batch(&body, st.max_batch) {
            Ok(events) => events,
            Err(BatchError::TooMany) => {
                return Err(Box::new(error(
                    StatusCode::PAYLOAD_TOO_LARGE,
                    "batch_too_large",
                    format!("at most {} events per batch", st.max_batch),
                )));
            }
            Err(BatchError::Malformed(e)) => {
                return Err(Box::new(error(
                    StatusCode::BAD_REQUEST,
                    "malformed",
                    format!("expected {{\"events\": [...]}}: {}", brief(e)),
                )));
            }
        };
        let mut results = Vec::with_capacity(events.len());
        let mut unavailable = false;
        for (i, event) in events.iter().enumerate() {
            // Once the store is unavailable, the rest would only wait too.
            // Order matters to a reader, so none of them is tried.
            if unavailable {
                results.push(st.busy());
                continue;
            }
            let outcome = match serde_json::from_str::<SourceEvent>(event.get()) {
                Err(e) => malformed(&format!("events[{i}]"), e),
                Ok(event) => match st.admit(&grants, &event) {
                    Ok(outcome) => outcome,
                    Err(failure) => {
                        unavailable = matches!(failure, Failure::Unavailable(_));
                        st.failed(failure)?
                    }
                },
            };
            results.push(outcome);
        }
        Ok::<_, Box<Response>>(results)
    })
    .await;
    match result {
        Ok(Ok(results)) => {
            // A batch the store took none of answers like one busy event,
            // so a caller that reads only the status still knows to retry.
            let none_taken = !results.is_empty()
                && results
                    .iter()
                    .all(|r| matches!(r, AdmissionOutcome::Busy { .. }));
            let status = if none_taken {
                StatusCode::SERVICE_UNAVAILABLE
            } else {
                StatusCode::OK
            };
            let mut response = (status, axum::Json(BatchResponse { results })).into_response();
            if none_taken {
                response
                    .headers_mut()
                    .insert(header::RETRY_AFTER, retry_after(state.busy_retry_after_ms));
            }
            response
        }
        Ok(Err(response)) => *response,
        Err(e) => internal(e),
    }
}

async fn get_checkpoint(
    State(state): State<Arc<AppState>>,
    query: Result<Query<CheckpointQuery>, QueryRejection>,
    request: Request,
) -> Response {
    use core_memory_contract::event::{MAX_CHECKPOINT_KEY_BYTES, MAX_IDENTIFIER_BYTES};

    let grants = match grants_of(&request) {
        Ok(grants) => grants,
        Err(response) => return *response,
    };
    let query = match query {
        Ok(Query(query))
            if is_name(&query.scope, MAX_IDENTIFIER_BYTES)
                && is_name(&query.key, MAX_CHECKPOINT_KEY_BYTES) =>
        {
            query
        }
        _ => {
            return error(
                StatusCode::BAD_REQUEST,
                "malformed",
                "give `scope` and `key` as query parameters, each a name without \
                 control characters",
            );
        }
    };
    if !grants.allows(&query.scope) {
        return error(
            StatusCode::FORBIDDEN,
            "scope_denied",
            "the token holds no grant for that scope",
        );
    }
    let st = Arc::clone(&state);
    let (scope, key) = (query.scope.clone(), query.key.clone());
    let result = tokio::task::spawn_blocking(move || {
        Ok::<_, Failure>(st.pool.store(CHECKOUT_WAIT).checkpoint(&scope, &key)?)
    })
    .await;
    match result {
        Ok(Ok(Some(stored))) => match stored.hash {
            Some(hash) => axum::Json(CheckpointResponse {
                scope: query.scope,
                key: query.key,
                sequence: stored.sequence,
                hash,
            })
            .into_response(),
            None => internal("a stored checkpoint has no hash"),
        },
        Ok(Ok(None)) => error(
            StatusCode::NOT_FOUND,
            "not_found",
            "no checkpoint is stored under that key",
        ),
        Ok(Err(Failure::Unavailable(cause))) => {
            report::log(serde_json::json!({"store_unavailable": cause}));
            let mut response = error(
                StatusCode::SERVICE_UNAVAILABLE,
                "store_unavailable",
                "the store cannot answer right now",
            );
            response
                .headers_mut()
                .insert(header::RETRY_AFTER, retry_after(state.busy_retry_after_ms));
            response
        }
        Ok(Err(Failure::Internal(cause))) => internal(cause),
        Err(e) => internal(e),
    }
}

/// Answers `POST /v1/recall` from the scopes the token may recall from.
/// The answer carries source text, so nothing of the request or the
/// answer is logged.
async fn post_recall(State(state): State<Arc<AppState>>, request: Request) -> Response {
    let Some(Recalls(may)) = request.extensions().get::<Recalls>().cloned() else {
        return internal("a request reached a handler unauthenticated");
    };
    let (parts, body) = request.into_parts();
    let body = match state.read_body(&parts.headers, body).await {
        Ok(body) => body,
        Err(response) => return *response,
    };
    // The parser's message is not passed on: it can quote the body, and a
    // query may be words of what a source said.
    let Ok(asked) = serde_json::from_slice::<RecallRequest>(&body) else {
        return error(
            StatusCode::BAD_REQUEST,
            "malformed",
            "expected {\"query\": \"...\"} and, if wanted, scopes, limit, follow and \
             include_ended, and nothing else",
        );
    };
    let query = match RecallQuery::from_request(&asked, &may) {
        Ok(query) => query,
        Err(refusal) => {
            let status = if refusal.is_forbidden() {
                StatusCode::FORBIDDEN
            } else {
                StatusCode::BAD_REQUEST
            };
            return error(status, refusal.code(), refusal.to_string());
        }
    };
    let st = Arc::clone(&state);
    let result = tokio::task::spawn_blocking(move || {
        Ok::<_, Failure>(st.pool.store(CHECKOUT_WAIT).recall(&query)?)
    })
    .await;
    match result {
        Ok(Ok(answer)) => axum::Json(answer).into_response(),
        Ok(Err(Failure::Unavailable(cause))) => {
            report::log(serde_json::json!({"store_unavailable": cause}));
            let mut response = error(
                StatusCode::SERVICE_UNAVAILABLE,
                "store_unavailable",
                "the store cannot answer right now",
            );
            response
                .headers_mut()
                .insert(header::RETRY_AFTER, retry_after(state.busy_retry_after_ms));
            response
        }
        Ok(Err(Failure::Internal(cause))) => internal(cause),
        Err(e) => internal(e),
    }
}

async fn get_ready(State(state): State<Arc<AppState>>) -> Response {
    let st = Arc::clone(&state);
    let result = tokio::task::spawn_blocking(move || {
        let mut store = st.pool.get(CHECKOUT_WAIT)?;
        store.check()?;
        Ok::<_, CheckoutError>(())
    })
    .await;
    // The cause is logged. Probes are unauthenticated, so they get a category.
    let (problem, cause) = match result {
        Ok(Ok(())) => (None, None),
        Ok(Err(CheckoutError::Store(
            e @ (StoreError::SchemaMismatch(_) | StoreError::Corrupt(_)),
        ))) => (Some("schema_mismatch"), Some(e.to_string())),
        Ok(Err(CheckoutError::Store(e @ StoreError::AccessRefused(_)))) => {
            (Some("store_refused"), Some(e.to_string()))
        }
        Ok(Err(e)) => (Some("store_unavailable"), Some(e.to_string())),
        Err(e) => (Some("store_unavailable"), Some(e.to_string())),
    };
    if let Some(cause) = cause {
        report::log(serde_json::json!({"not_ready": cause}));
    }
    let status = if problem.is_none() {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    let body = ReadyResponse {
        ready: problem.is_none(),
        schema_version: SCHEMA_VERSION,
        problem: problem.map(str::to_string),
    };
    (status, axum::Json(body)).into_response()
}

async fn get_schema(Path(name): Path<String>) -> Response {
    match Contract::from_name(&name) {
        Some(contract) => axum::Json(contract.schema()).into_response(),
        // The name is not repeated: this route needs no token, and the
        // name is as long as its caller likes.
        None => error(
            StatusCode::NOT_FOUND,
            "not_found",
            "no contract has that name",
        ),
    }
}

/// How much of a response the operating system may hold for a client that
/// has not read it yet.
///
/// [`Deadlined`] sees a client's progress only when a write gets through,
/// and a write gets through once about a third of this has been read. Left
/// to itself the operating system grows the buffer to megabytes, and a
/// client reading a large answer steadily but slowly would then look
/// stalled for longer than `write_timeout`. Fixed at this size, progress
/// shows in steps of some tens of kilobytes.
const SEND_BUFFER_BYTES: usize = 64 * 1024;

/// A connection that is closed when its client stops reading.
///
/// hyper bounds how long a client may take to send a request's headers,
/// and the handlers bound its body. Nothing else bounds a response: a
/// client that asks and then does not read would keep its connection, and
/// its place among the connections served, for good. Here a write that has
/// waited `timeout` for the client fails, which ends the connection. Every
/// write that gets through starts the wait again.
///
/// A write gets through when the client has read enough to make room, in
/// steps of up to about [`SEND_BUFFER_BYTES`]. So a client is served for as
/// long as it reads about that much in every `timeout`, and one that reads
/// more slowly than that is taken for one that has stopped.
struct Deadlined<S> {
    stream: S,
    timeout: Duration,
    /// Runs while a write is waiting for the client.
    waited: Option<Pin<Box<tokio::time::Sleep>>>,
}

impl<S> Deadlined<S> {
    fn new(stream: S, timeout: Duration) -> Self {
        Self {
            stream,
            timeout,
            waited: None,
        }
    }

    /// What a write that cannot go through yet returns: pending, or an
    /// error once it has waited too long.
    fn stalled<T>(&mut self, cx: &mut Context<'_>) -> Poll<io::Result<T>> {
        let timeout = self.timeout;
        let waited = self
            .waited
            .get_or_insert_with(|| Box::pin(tokio::time::sleep(timeout)));
        match waited.as_mut().poll(cx) {
            Poll::Ready(()) => Poll::Ready(Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "the client stopped reading",
            ))),
            Poll::Pending => Poll::Pending,
        }
    }

    fn written(
        &mut self,
        polled: Poll<io::Result<usize>>,
        cx: &mut Context<'_>,
    ) -> Poll<io::Result<usize>> {
        match polled {
            Poll::Pending => self.stalled(cx),
            ready => {
                self.waited = None;
                ready
            }
        }
    }
}

impl<S: AsyncRead + Unpin> AsyncRead for Deadlined<S> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.stream).poll_read(cx, buf)
    }
}

impl<S: AsyncWrite + Unpin> AsyncWrite for Deadlined<S> {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        let polled = Pin::new(&mut self.stream).poll_write(cx, buf);
        self.written(polled, cx)
    }

    fn poll_write_vectored(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bufs: &[io::IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        let polled = Pin::new(&mut self.stream).poll_write_vectored(cx, bufs);
        self.written(polled, cx)
    }

    fn is_write_vectored(&self) -> bool {
        self.stream.is_write_vectored()
    }

    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        match Pin::new(&mut self.stream).poll_flush(cx) {
            Poll::Pending => self.stalled(cx),
            ready => ready,
        }
    }

    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        match Pin::new(&mut self.stream).poll_shutdown(cx) {
            Poll::Pending => self.stalled(cx),
            ready => ready,
        }
    }
}

/// Serves `router` on `listener` until `shutdown` resolves, then gives
/// requests in flight `limits.shutdown_grace` to finish.
///
/// A connection that does not send a request's headers within
/// `limits.read_timeout` is closed, whether it is new or idle between
/// requests, and so is one whose client has not read for
/// `limits.write_timeout` while a response waited. At most
/// `limits.max_connections` are served at once.
pub async fn serve(
    listener: tokio::net::TcpListener,
    router: Router,
    limits: ServeLimits,
    shutdown: impl Future<Output = ()>,
) -> std::io::Result<()> {
    use hyper_util::rt::{TokioIo, TokioTimer};

    let mut http = hyper::server::conn::http1::Builder::new();
    http.timer(TokioTimer::new())
        .header_read_timeout(limits.read_timeout)
        // A client may finish sending and close its side before it reads
        // the answer. Its request is still answered.
        .half_close(true);
    let graceful = hyper_util::server::graceful::GracefulShutdown::new();
    let slots = Arc::new(tokio::sync::Semaphore::new(limits.max_connections.max(1)));
    let mut shutdown = std::pin::pin!(shutdown);
    loop {
        // A slot is taken before a connection is accepted, so past the
        // limit new connections wait in the listener's queue.
        let slot = tokio::select! {
            slot = Arc::clone(&slots).acquire_owned() => slot,
            () = &mut shutdown => break,
        };
        let Ok(slot) = slot else { break };
        let accepted = tokio::select! {
            accepted = listener.accept() => accepted,
            () = &mut shutdown => break,
        };
        let stream = match accepted {
            Ok((stream, _)) => stream,
            Err(e) => {
                // Out of file descriptors, or the peer gave up. Neither
                // ends the server.
                report::log(serde_json::json!({"accept_failed": e.to_string()}));
                tokio::time::sleep(Duration::from_millis(50)).await;
                continue;
            }
        };
        let _ = stream.set_nodelay(true);
        // See SEND_BUFFER_BYTES. If the size cannot be set, slow readers of
        // large answers are cut off sooner, and nothing else changes.
        let _ = socket2::SockRef::from(&stream).set_send_buffer_size(SEND_BUFFER_BYTES);
        let router = router.clone();
        // A router is cheap to clone, and one call needs it mutably.
        let service =
            hyper::service::service_fn(move |request: hyper::Request<hyper::body::Incoming>| {
                router.clone().call(request)
            });
        let stream = Deadlined::new(stream, limits.write_timeout);
        let connection = graceful.watch(http.serve_connection(TokioIo::new(stream), service));
        tokio::spawn(async move {
            // A connection that ends in an error ends only itself.
            let _ = connection.await;
            drop(slot);
        });
    }
    drop(listener);
    report::log(serde_json::json!({"shutdown": "finishing requests"}));
    tokio::select! {
        () = graceful.shutdown() => {}
        () = tokio::time::sleep(limits.shutdown_grace) => {
            report::log(serde_json::json!({"shutdown": "closing connections still open"}));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn batch(body: &str, max: usize) -> Result<usize, &'static str> {
        match parse_batch(body.as_bytes(), max) {
            Ok(events) => Ok(events.len()),
            Err(BatchError::TooMany) => Err("too many"),
            Err(BatchError::Malformed(_)) => Err("malformed"),
        }
    }

    #[test]
    fn a_batch_is_read_no_further_than_its_limit() {
        assert_eq!(batch(r#"{"events": []}"#, 2), Ok(0));
        assert_eq!(batch(r#"{"events": [{}, {"a": [1, 2]}]}"#, 2), Ok(2));
        assert_eq!(batch(r#"{"events": [1, 2, 3]}"#, 2), Err("too many"));
        for bad in [
            "[1, 2]",
            r#"{"events": 1}"#,
            r#"{"event": []}"#,
            r#"{"events": [], "events": []}"#,
            r#"{"events": [], "more": 1}"#,
            r#"{}"#,
            r#"{"events": [1,]}"#,
            r#"{"events": []} trailing"#,
        ] {
            assert_eq!(batch(bad, 2), Err("malformed"), "{bad}");
        }
        // Millions of tiny elements are refused at the third.
        let huge = format!(r#"{{"events": [{}0]}}"#, "0,".repeat(4_000_000));
        let started = std::time::Instant::now();
        assert_eq!(batch(&huge, 2), Err("too many"));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn names_are_bounded_and_free_of_control_characters() {
        assert!(is_name("acme", 8));
        assert!(is_name("session 1/a.jsonl", 64));
        for bad in ["", "a\0b", "line\nbreak", "much-too-long"] {
            assert!(!is_name(bad, 8), "{bad:?}");
        }
    }
}

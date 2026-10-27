//! `core-memory serve`: the engine over HTTP/JSON.
//!
//! Callers authenticate with a bearer token; each token carries its own
//! scope grants (SEC-01). Admission happens on one store connection behind
//! a lock. A delivery thread with its own connection to the same SQLite file
//! drains the outbox to the sinks, woken after each admission.

use std::sync::mpsc::{self, RecvTimeoutError, SyncSender};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread::JoinHandle;
use std::time::Duration;

use axum::Router;
use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, Path, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use core_memory_contract::schema::Contract;
use core_memory_contract::{
    AdmissionOutcome, FieldError, RefusalReason, Sha256Digest, SourceEvent,
};
use core_memory_core::{Engine, ModelTransport, ScopeGrants};
use core_memory_store_sqlite::SqliteStore;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;

use crate::config::{Loaded, grants_from};
use crate::openapi;
use crate::runtime::{
    BoxedSink, build_engine, build_transport, classify_line, deliver_round, delivery_line,
    open_sinks, open_store,
};

/// A configured caller.
#[derive(Debug)]
struct Token {
    hash: Sha256Digest,
    grants: ScopeGrants,
}

/// Shared server state.
#[derive(Debug)]
pub struct AppState {
    engine: Engine,
    store: Mutex<SqliteStore>,
    tokens: Vec<Token>,
    /// Woken after admissions: the classifier and the delivery thread.
    wake: Vec<SyncSender<Signal>>,
    max_batch: usize,
}

impl AppState {
    fn store(&self) -> MutexGuard<'_, SqliteStore> {
        // Admission commits are transactional, so a panic mid-admission
        // leaves no partial state; the guard can be reused.
        self.store.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn wake_workers(&self) {
        for tx in &self.wake {
            // A full channel means a wake is already pending.
            let _ = tx.try_send(Signal::Wake);
        }
    }

    /// The token presented in `Authorization: Bearer ...`, compared by
    /// SHA-256 against every configured token without stopping early.
    fn authorize(&self, headers: &HeaderMap) -> Result<&Token, Box<Response>> {
        let presented = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.split_once(' '))
            // The scheme name is case-insensitive (RFC 9110).
            .filter(|(scheme, _)| scheme.eq_ignore_ascii_case("bearer"))
            .map(|(_, t)| Sha256Digest::of(t.trim().as_bytes()));
        let mut found = None;
        if let Some(hash) = presented {
            for token in &self.tokens {
                if constant_time_eq(token.hash.as_bytes(), hash.as_bytes()) {
                    found = Some(token);
                }
            }
        }
        found.ok_or_else(|| {
            let mut response = error(
                StatusCode::UNAUTHORIZED,
                "unauthorized",
                "a valid bearer token is required",
            );
            response
                .headers_mut()
                .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
            Box::new(response)
        })
    }
}

fn constant_time_eq(a: &[u8; 32], b: &[u8; 32]) -> bool {
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Messages to the delivery thread.
#[derive(Debug)]
pub enum Signal {
    Wake,
    Stop,
}

/// The background thread that delivers outbox records to the sinks.
#[derive(Debug)]
pub struct DeliveryWorker {
    tx: SyncSender<Signal>,
    handle: JoinHandle<()>,
}

impl DeliveryWorker {
    pub fn start(
        store: SqliteStore,
        sinks: Vec<BoxedSink>,
        required: Vec<String>,
        interval: Duration,
    ) -> Self {
        let (tx, rx) = mpsc::sync_channel(1);
        let handle = std::thread::spawn(move || run_delivery(rx, store, sinks, required, interval));
        Self { tx, handle }
    }

    fn sender(&self) -> SyncSender<Signal> {
        self.tx.clone()
    }

    /// Runs a last delivery round and waits for the thread to finish.
    pub fn stop(self) {
        let _ = self.tx.send(Signal::Stop);
        let _ = self.handle.join();
    }
}

fn run_delivery(
    rx: mpsc::Receiver<Signal>,
    mut store: SqliteStore,
    mut sinks: Vec<BoxedSink>,
    required: Vec<String>,
    interval: Duration,
) {
    // Deliver anything left from a previous run straight away.
    let mut wait = Duration::ZERO;
    loop {
        let stop = matches!(
            rx.recv_timeout(wait),
            Ok(Signal::Stop) | Err(RecvTimeoutError::Disconnected)
        );
        wait = interval;
        match deliver_round(&mut store, &mut sinks, &required) {
            Ok(reports) => {
                for (id, r) in &reports {
                    if r.accepted + r.already_accepted + r.failed + r.skipped_gap > 0
                        || r.retry_after_ms.is_some()
                    {
                        eprintln!("{}", delivery_line(id, r));
                    }
                    if let Some(ms) = r.retry_after_ms {
                        wait = wait.max(Duration::from_millis(ms));
                    }
                }
            }
            Err(e) => eprintln!("{}", serde_json::json!({"delivery_error": e})),
        }
        if stop {
            return;
        }
    }
}

/// The background thread that classifies held chunks through the model
/// binding, then wakes delivery for the records it emitted.
#[derive(Debug)]
pub struct ClassifierWorker {
    tx: SyncSender<Signal>,
    handle: JoinHandle<()>,
}

impl ClassifierWorker {
    pub fn start(
        engine: Engine,
        store: SqliteStore,
        transport: Box<dyn ModelTransport>,
        interval: Duration,
        delivery: Option<SyncSender<Signal>>,
    ) -> Self {
        let (tx, rx) = mpsc::sync_channel(1);
        let handle = std::thread::spawn(move || {
            run_classifier(rx, engine, store, transport, interval, delivery)
        });
        Self { tx, handle }
    }

    fn sender(&self) -> SyncSender<Signal> {
        self.tx.clone()
    }

    /// Finishes the batch in progress and waits for the thread to end.
    /// Held records stay held; the next start picks them up.
    pub fn stop(self) {
        let _ = self.tx.send(Signal::Stop);
        let _ = self.handle.join();
    }
}

fn run_classifier(
    rx: mpsc::Receiver<Signal>,
    engine: Engine,
    mut store: SqliteStore,
    transport: Box<dyn ModelTransport>,
    interval: Duration,
    delivery: Option<SyncSender<Signal>>,
) {
    let mut wait = Duration::ZERO;
    loop {
        if matches!(
            rx.recv_timeout(wait),
            Ok(Signal::Stop) | Err(RecvTimeoutError::Disconnected)
        ) {
            return;
        }
        // One batch per turn, so a stop never waits behind many calls.
        wait = interval;
        match engine.classify_due(&mut store, transport.as_ref(), 1) {
            Ok(report) => {
                if report.calls + report.released + report.abandoned > 0 {
                    eprintln!("{}", classify_line(&report));
                }
                if report.decided + report.released + report.abandoned > 0
                    && let Some(tx) = &delivery
                {
                    let _ = tx.try_send(Signal::Wake);
                }
                if report.calls > 0 {
                    // There may be more due; keep draining.
                    wait = Duration::ZERO;
                }
            }
            Err(e) => eprintln!("{}", serde_json::json!({"classify_error": e.to_string()})),
        }
    }
}

/// A server ready to run: its router and, unless disabled, its background
/// threads.
#[derive(Debug)]
pub struct Prepared {
    pub router: Router,
    pub worker: Option<DeliveryWorker>,
    pub classifier: Option<ClassifierWorker>,
    pub listen: String,
}

impl Prepared {
    /// Stops the classifier, then delivery, so the last decisions are
    /// delivered before exit.
    pub fn shutdown(self) {
        if let Some(classifier) = self.classifier {
            classifier.stop();
        }
        if let Some(worker) = self.worker {
            worker.stop();
        }
    }
}

/// Opens the engine, store and sinks for `loaded` and builds the router.
/// `start_delivery` is false only in tests that need records to stay queued.
pub fn prepare(loaded: &Loaded, start_delivery: bool) -> Result<Prepared, String> {
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
    let engine = build_engine(loaded)?;
    let mut store = open_store(loaded)?;
    engine.pin_profiles(&mut store).map_err(|e| e.to_string())?;
    let transport = build_transport(loaded)?;
    let worker = if start_delivery {
        Some(DeliveryWorker::start(
            open_store(loaded)?,
            open_sinks(loaded)?,
            engine.required_sinks().to_vec(),
            Duration::from_millis(http.delivery_interval_ms),
        ))
    } else {
        None
    };
    let interval = Duration::from_millis(http.delivery_interval_ms);
    let classifier = match transport {
        Some(transport) if start_delivery => Some(ClassifierWorker::start(
            build_engine(loaded)?,
            open_store(loaded)?,
            transport,
            interval,
            worker.as_ref().map(DeliveryWorker::sender),
        )),
        _ => None,
    };
    let tokens = http
        .tokens
        .iter()
        .map(|t| Token {
            hash: t.sha256,
            grants: grants_from(&t.scopes),
        })
        .collect();
    let state = Arc::new(AppState {
        engine,
        store: Mutex::new(store),
        tokens,
        wake: classifier
            .iter()
            .map(ClassifierWorker::sender)
            .chain(worker.iter().map(DeliveryWorker::sender))
            .collect(),
        max_batch: http.max_batch_events,
    });
    Ok(Prepared {
        router: router(state, http.max_body_bytes),
        worker,
        classifier,
        listen: http.listen.clone(),
    })
}

pub fn router(state: Arc<AppState>, max_body_bytes: usize) -> Router {
    Router::new()
        .route("/v1/events", post(post_event))
        .route("/v1/events/batch", post(post_batch))
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
        .layer(DefaultBodyLimit::max(max_body_bytes))
        .with_state(state)
}

/// Body of `POST /v1/events/batch`.
#[derive(Debug, Deserialize, JsonSchema)]
pub struct BatchRequest {
    pub events: Vec<SourceEvent>,
}

/// Events are parsed one by one, so one bad event does not refuse the batch.
#[derive(Deserialize)]
struct RawBatch {
    events: Vec<Box<RawValue>>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct BatchResponse {
    /// One result per event, in request order.
    pub results: Vec<AdmissionOutcome>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ReadyResponse {
    pub ready: bool,
    pub backlog: u64,
    pub max_backlog: u64,
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

fn internal(e: impl std::fmt::Display) -> Response {
    eprintln!("{}", serde_json::json!({"internal_error": e.to_string()}));
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

/// HTTP status for an admission outcome. The body is always the outcome.
pub fn status_for(outcome: &AdmissionOutcome) -> StatusCode {
    match outcome {
        AdmissionOutcome::Accepted { .. } | AdmissionOutcome::Replay { .. } => StatusCode::OK,
        AdmissionOutcome::IdentityConflict { .. } => StatusCode::CONFLICT,
        AdmissionOutcome::Busy { .. } => StatusCode::SERVICE_UNAVAILABLE,
        AdmissionOutcome::Refused { reason, .. } => match reason {
            RefusalReason::ScopeDenied => StatusCode::FORBIDDEN,
            RefusalReason::Tombstoned => StatusCode::GONE,
            RefusalReason::Oversize => StatusCode::PAYLOAD_TOO_LARGE,
            RefusalReason::InvalidProfile => StatusCode::UNPROCESSABLE_ENTITY,
            RefusalReason::Malformed => StatusCode::BAD_REQUEST,
        },
    }
}

fn admission_response(outcome: AdmissionOutcome) -> Response {
    let status = status_for(&outcome);
    let mut response = (status, axum::Json(&outcome)).into_response();
    if let AdmissionOutcome::Busy { retry_after_ms, .. } = outcome {
        let seconds = retry_after_ms.div_ceil(1000).max(1);
        if let Ok(v) = HeaderValue::from_str(&seconds.to_string()) {
            response.headers_mut().insert(header::RETRY_AFTER, v);
        }
    }
    response
}

async fn post_event(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let grants = match state.authorize(&headers) {
        Ok(token) => token.grants.clone(),
        Err(response) => return *response,
    };
    let event: SourceEvent = match serde_json::from_slice(&body) {
        Ok(event) => event,
        Err(e) => return admission_response(malformed("body", e)),
    };
    let st = Arc::clone(&state);
    let result =
        tokio::task::spawn_blocking(move || st.engine.admit(&mut *st.store(), &grants, &event))
            .await;
    match result {
        Ok(Ok(outcome)) => {
            if matches!(outcome, AdmissionOutcome::Accepted { .. }) {
                state.wake_workers();
            }
            admission_response(outcome)
        }
        Ok(Err(e)) => internal(e),
        Err(e) => internal(e),
    }
}

async fn post_batch(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let grants = match state.authorize(&headers) {
        Ok(token) => token.grants.clone(),
        Err(response) => return *response,
    };
    let raw: RawBatch = match serde_json::from_slice(&body) {
        Ok(raw) => raw,
        Err(e) => {
            return error(
                StatusCode::BAD_REQUEST,
                "malformed",
                format!("expected {{\"events\": [...]}}: {e}"),
            );
        }
    };
    if raw.events.len() > state.max_batch {
        return error(
            StatusCode::PAYLOAD_TOO_LARGE,
            "batch_too_large",
            format!("at most {} events per batch", state.max_batch),
        );
    }
    let st = Arc::clone(&state);
    let result = tokio::task::spawn_blocking(move || {
        let mut results = Vec::with_capacity(raw.events.len());
        for (i, event) in raw.events.iter().enumerate() {
            let outcome = match serde_json::from_str::<SourceEvent>(event.get()) {
                Ok(event) => st.engine.admit(&mut *st.store(), &grants, &event)?,
                Err(e) => malformed(&format!("events[{i}]"), e),
            };
            results.push(outcome);
        }
        Ok::<_, core_memory_core::EngineError>(results)
    })
    .await;
    match result {
        Ok(Ok(results)) => {
            if results
                .iter()
                .any(|o| matches!(o, AdmissionOutcome::Accepted { .. }))
            {
                state.wake_workers();
            }
            (StatusCode::OK, axum::Json(BatchResponse { results })).into_response()
        }
        Ok(Err(e)) => internal(e),
        Err(e) => internal(e),
    }
}

async fn get_ready(State(state): State<Arc<AppState>>) -> Response {
    let st = Arc::clone(&state);
    match tokio::task::spawn_blocking(move || st.engine.readiness(&*st.store())).await {
        Ok(Ok(r)) => {
            let status = if r.ready {
                StatusCode::OK
            } else {
                StatusCode::SERVICE_UNAVAILABLE
            };
            let body = ReadyResponse {
                ready: r.ready,
                backlog: r.backlog,
                max_backlog: r.max_backlog,
            };
            (status, axum::Json(body)).into_response()
        }
        Ok(Err(e)) => internal(e),
        Err(e) => internal(e),
    }
}

async fn get_schema(Path(name): Path<String>) -> Response {
    match Contract::from_name(&name) {
        Some(contract) => axum::Json(contract.schema()).into_response(),
        None => error(
            StatusCode::NOT_FOUND,
            "not_found",
            format!("no contract named `{name}`"),
        ),
    }
}

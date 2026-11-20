//! The transport: one streamed Responses API call per request, with a
//! token taken from the credentials file and renewed when due.

use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use core_memory_core::model::{
    BindingConfig, ModelRequest, ModelResponse, ModelTransport, TransportError,
};
use core_memory_model_http::HttpClient;
use serde::Serialize;
use time::OffsetDateTime;

use crate::credentials::{self, Credentials, FileError, Secret};
use crate::{ENDPOINT, Endpoints, PLAN_SCOPE, PROVIDER, oauth, responses};

/// How long before its expiry an access token is renewed.
const RENEW_MARGIN: Duration = Duration::from_secs(300);
/// How long a call waits for another process renewing the same sign-in.
const LOCK_WAIT: Duration = Duration::from_secs(30);

/// A model the signed-in account may use, as its catalog lists it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Model {
    /// What a binding names as its `model`.
    pub slug: String,
    pub display_name: String,
}

/// Calls the Responses API on the owner's ChatGPT plan.
pub struct ChatGptTransport {
    client: HttpClient,
    credentials: PathBuf,
    endpoints: Endpoints,
    /// Set when the API turned a token down, so the next call renews it
    /// before trying again.
    renew: AtomicBool,
}

impl fmt::Debug for ChatGptTransport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ChatGptTransport").finish_non_exhaustive()
    }
}

fn sign_in_required(message: impl Into<String>) -> TransportError {
    TransportError::SignInRequired {
        message: message.into(),
    }
}

fn file_error(e: FileError) -> TransportError {
    match e {
        FileError::Busy => TransportError::Transient {
            message: e.to_string(),
            retry_after_ms: None,
        },
        other => sign_in_required(other.to_string()),
    }
}

/// `message` with every one of `secrets` taken out, in case a server
/// repeated one.
fn scrub(message: String, secrets: &[&str]) -> String {
    secrets
        .iter()
        .filter(|s| s.len() >= 8)
        .fold(message, |m, s| m.replace(s, "[token]"))
}

fn scrub_error(error: TransportError, secrets: &[&str]) -> TransportError {
    use TransportError as E;
    match error {
        E::Transient {
            message,
            retry_after_ms,
        } => E::Transient {
            message: scrub(message, secrets),
            retry_after_ms,
        },
        E::Permanent { message } => E::Permanent {
            message: scrub(message, secrets),
        },
        E::Refused { message } => E::Refused {
            message: scrub(message, secrets),
        },
        E::DidNotFit { message } => E::DidNotFit {
            message: scrub(message, secrets),
        },
        E::InDoubt { message } => E::InDoubt {
            message: scrub(message, secrets),
        },
        E::Untrusted { message } => E::Untrusted {
            message: scrub(message, secrets),
        },
        E::SignInRequired { message } => E::SignInRequired {
            message: scrub(message, secrets),
        },
        E::LimitReached { message, started } => E::LimitReached {
            message: scrub(message, secrets),
            started,
        },
    }
}

impl ChatGptTransport {
    /// A transport that reads the sign-in saved at `credentials` and sends
    /// through `client` to OpenAI.
    pub fn new(credentials: PathBuf, client: HttpClient) -> Self {
        Self::with_endpoints(credentials, client, Endpoints::openai())
    }

    pub(crate) fn with_endpoints(
        credentials: PathBuf,
        client: HttpClient,
        endpoints: Endpoints,
    ) -> Self {
        Self {
            client,
            credentials,
            endpoints,
            renew: AtomicBool::new(false),
        }
    }

    /// Checks a binding's settings for this provider. Reads no file and
    /// sends nothing.
    pub fn check_binding(binding: &BindingConfig) -> Result<(), String> {
        Self::check_settings(binding)?;
        if binding.endpoint.trim_end_matches('/') != ENDPOINT {
            return Err(format!(
                "binding `{}`: provider `{PROVIDER}` takes endpoint = \"{ENDPOINT}\", and no \
                 other",
                binding.id
            ));
        }
        Ok(())
    }

    /// What the plan's route refuses, whatever the endpoint.
    fn check_settings(binding: &BindingConfig) -> Result<(), String> {
        let id = &binding.id;
        if binding.provider != PROVIDER {
            return Err(format!(
                "binding `{id}` is for provider `{}`, not `{PROVIDER}`",
                binding.provider
            ));
        }
        if binding.api_key_env.is_some() {
            return Err(format!(
                "binding `{id}`: provider `{PROVIDER}` is signed in to, not given a key. Remove \
                 api_key_env and run `core-memory login chatgpt`"
            ));
        }
        // A scope's consent is checked against the binding's destination
        // before anything reaches the transport. Unmarked, that would be
        // the bare host an API key is allowed for, and the plan would serve
        // scopes its owner never allowed for it.
        if !binding.signed_in {
            return Err(format!(
                "binding `{id}`: provider `{PROVIDER}` needs the binding marked signed_in, so \
                 that scopes are allowed for the plan apart from any API key"
            ));
        }
        for (name, set) in [
            ("temperature", binding.temperature.is_some()),
            ("seed", binding.seed.is_some()),
        ] {
            if set {
                return Err(format!(
                    "binding `{id}`: the ChatGPT plan's API does not take {name}; remove it"
                ));
            }
        }
        Ok(())
    }

    /// The credentials, checked for the plan's permission and, if
    /// `background` is set, the owner's consent on this host.
    fn signed_in(&self, background: bool) -> Result<Credentials, TransportError> {
        let credentials = credentials::load(&self.credentials)
            .map_err(file_error)?
            .ok_or_else(|| sign_in_required("not signed in: run `core-memory login chatgpt`"))?;
        if !credentials.signed_in() {
            return Err(sign_in_required(
                "signed out: run `core-memory login chatgpt` to sign in again",
            ));
        }
        if !credentials.plan_granted() {
            return Err(sign_in_required(format!(
                "the sign-in does not let Core Memory use the ChatGPT plan ({PLAN_SCOPE} was \
                 not granted). Run `core-memory login chatgpt --consent` to grant it"
            )));
        }
        if background {
            let host = credentials::host_id(&self.credentials).map_err(file_error)?;
            if !host.is_some_and(|host| credentials.background_use_agreed(&host)) {
                return Err(sign_in_required(
                    "the owner has not agreed, on this machine, to Core Memory using the \
                     ChatGPT plan in the background. Run `core-memory login chatgpt` here, or \
                     `core-memory login chatgpt --import FILE` for a sign-in made on another \
                     machine",
                ));
            }
        }
        Ok(credentials)
    }

    /// An access token to call with, renewed first if it is due or was
    /// turned down.
    fn access_token(&self, background: bool) -> Result<Secret, TransportError> {
        let credentials = self.signed_in(background)?;
        let renew = self.renew.load(Ordering::Acquire);
        match &credentials.access_token {
            Some(token)
                if !renew && !credentials.refresh_due(OffsetDateTime::now_utc(), RENEW_MARGIN) =>
            {
                Ok(token.clone())
            }
            seen => self.renew_token(seen.clone(), renew, background),
        }
    }

    /// Renews the access token, holding the file's lock so no other process
    /// spends the same refresh token. If another renewed it meanwhile, its
    /// token is used.
    fn renew_token(
        &self,
        seen: Option<Secret>,
        forced: bool,
        background: bool,
    ) -> Result<Secret, TransportError> {
        let _lock = credentials::lock(&self.credentials, LOCK_WAIT).map_err(file_error)?;
        let mut current = self.signed_in(background)?;
        let now = OffsetDateTime::now_utc();
        if let Some(token) = &current.access_token {
            let renewed_by_another = seen.as_ref() != Some(token);
            if (renewed_by_another || !forced) && !current.refresh_due(now, RENEW_MARGIN) {
                self.renew.store(false, Ordering::Release);
                return Ok(token.clone());
            }
        }
        let Some(refresh_token) = current.refresh_token.clone() else {
            return Err(sign_in_required(
                "the sign-in cannot be renewed: run `core-memory login chatgpt`",
            ));
        };
        match oauth::refresh(&self.client, &self.endpoints, &current, &refresh_token) {
            Ok(tokens) => {
                tokens.apply(&mut current);
                credentials::save(&self.credentials, &current).map_err(file_error)?;
                self.renew.store(false, Ordering::Release);
                if !current.plan_granted() {
                    return Err(sign_in_required(format!(
                        "the renewed sign-in no longer lets Core Memory use the ChatGPT plan \
                         ({PLAN_SCOPE}). Run `core-memory login chatgpt --consent`"
                    )));
                }
                current
                    .access_token
                    .clone()
                    .ok_or_else(|| sign_in_required("the renewal returned no access token"))
            }
            Err(oauth::RefreshError::Unusable(code)) => {
                // Signed out elsewhere, expired or spent: the tokens are of
                // no more use. The account, its client and the consent stay
                // for the next sign-in.
                current.access_token = None;
                current.refresh_token = None;
                current.earliest_refresh_at = None;
                credentials::save(&self.credentials, &current).map_err(file_error)?;
                Err(sign_in_required(format!(
                    "the sign-in has ended ({code}): run `core-memory login chatgpt` to sign in \
                     again"
                )))
            }
            Err(oauth::RefreshError::Failed(error)) => Err(error),
        }
    }

    /// The models the signed-in account may use, in the order its catalog
    /// gives them. Asked for by the owner, so no background consent is
    /// needed.
    pub fn list_models(&self) -> Result<Vec<Model>, TransportError> {
        let token = self.access_token(false)?;
        let auth = format!("Bearer {}", token.expose());
        let reply = self
            .client
            .get(&self.endpoints.models_url(), &[("authorization", &auth)])
            .map_err(|e| scrub_error(e, &[token.expose()]))?;
        if !(200..300).contains(&reply.status) {
            if reply.status == 401 {
                self.renew.store(true, Ordering::Release);
            }
            return Err(scrub_error(
                responses::status_error(&reply),
                &[token.expose()],
            ));
        }
        let body: serde_json::Value =
            serde_json::from_str(&reply.body).map_err(|_| TransportError::Permanent {
                message: "the model catalog is not JSON".into(),
            })?;
        Ok(body["models"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|m| m["visibility"] == "list")
            .filter_map(|m| {
                Some(Model {
                    slug: m["slug"].as_str()?.to_string(),
                    display_name: m["display_name"]
                        .as_str()
                        .or_else(|| m["slug"].as_str())?
                        .to_string(),
                })
            })
            .collect())
    }

    /// Where the sign-in is kept.
    pub fn credentials(&self) -> &Path {
        &self.credentials
    }
}

impl ModelTransport for ChatGptTransport {
    fn send(
        &self,
        binding: &BindingConfig,
        request: &ModelRequest,
    ) -> Result<ModelResponse, TransportError> {
        Self::check_settings(binding).map_err(|message| TransportError::Permanent { message })?;
        // The call goes where the binding says, and the binding may say
        // only this: a token is never sent anywhere else.
        if binding.endpoint.trim_end_matches('/') != self.endpoints.api {
            return Err(TransportError::Permanent {
                message: format!(
                    "binding `{}`: provider `{PROVIDER}` takes endpoint = \"{ENDPOINT}\", and no \
                     other",
                    binding.id
                ),
            });
        }
        let token = self.access_token(true)?;
        let auth = format!("Bearer {}", token.expose());
        let secrets = [token.expose()];
        let reply = self
            .client
            .post_json(
                &self.endpoints.responses_url(),
                &[("authorization", &auth), ("accept", "text/event-stream")],
                &responses::body(binding, request),
            )
            .map_err(|e| scrub_error(e, &secrets))?;
        if !(200..300).contains(&reply.status) {
            if reply.status == 401 {
                self.renew.store(true, Ordering::Release);
            }
            return Err(scrub_error(responses::status_error(&reply), &secrets));
        }
        responses::read_stream(&reply).map_err(|e| scrub_error(e, &secrets))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::credentials::tests::sample;
    use core_memory_model_http::testing::{Received, Reply, serve};
    use serde_json::{Value, json};
    use std::sync::{Arc, Mutex};

    struct Fixture {
        _dir: tempfile::TempDir,
        path: PathBuf,
        host: String,
    }

    fn signed_in(edit: impl FnOnce(&mut Credentials)) -> Fixture {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let host = credentials::ensure_host_id(&path).unwrap();
        let mut c = sample(&host);
        edit(&mut c);
        credentials::save(&path, &c).unwrap();
        Fixture {
            _dir: dir,
            path,
            host,
        }
    }

    fn binding(endpoint: &str) -> BindingConfig {
        let mut b: BindingConfig = serde_json::from_value(json!({
            "id": "plan", "provider": PROVIDER, "endpoint": endpoint,
            "model": "gpt-test", "max_output_tokens": 1024, "timeout_ms": 5000,
        }))
        .unwrap();
        b.signed_in = true;
        b
    }

    fn request() -> ModelRequest {
        ModelRequest {
            system: "classify".into(),
            user: "{\"chunks\": []}".into(),
            response_schema: json!({"type": "object"}),
            enforce_schema: true,
            max_output_tokens: 1024,
        }
    }

    fn sse(text: &str) -> String {
        let done = json!({"type": "response.completed", "response": {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 10, "output_tokens": 2}}});
        format!("event: response.completed\ndata: {done}\n\n")
    }

    fn sse_reply(text: &str) -> Reply {
        Reply {
            status: 200,
            headers: vec![("content-type".into(), "text/event-stream".into())],
            body: sse(text),
        }
    }

    fn transport(path: &Path, url: &str) -> ChatGptTransport {
        ChatGptTransport::with_endpoints(
            path.to_path_buf(),
            HttpClient::new(Duration::from_secs(5)),
            Endpoints::at(url),
        )
    }

    #[test]
    fn a_call_streams_with_the_saved_token_and_nothing_the_route_refuses() {
        let f = signed_in(|_| {});
        let (url, server) = serve(1, |_| sse_reply("{\"items\": []}"));
        let response = transport(&f.path, &url)
            .send(&binding(&format!("{url}/v1")), &request())
            .unwrap();
        assert_eq!(response.text, "{\"items\": []}");
        let received = server.join().unwrap().remove(0);
        assert_eq!(received.request_line, "POST /v1/responses HTTP/1.1");
        assert_eq!(
            received.header("authorization"),
            Some("Bearer access-token-secret")
        );
        assert_eq!(received.header("accept"), Some("text/event-stream"));
        let sent: Value = serde_json::from_str(&received.body).unwrap();
        assert_eq!(
            (sent["store"].clone(), sent["stream"].clone()),
            (json!(false), json!(true))
        );
        assert!(sent.get("max_output_tokens").is_none());
        // The token is in the header only, never in the URL.
        assert!(!received.request_line.contains("secret"));
    }

    #[test]
    fn the_binding_must_name_openais_endpoint_and_no_refused_setting() {
        let mut b = binding(ENDPOINT);
        ChatGptTransport::check_binding(&b).unwrap();
        b.endpoint = format!("{ENDPOINT}/");
        ChatGptTransport::check_binding(&b).unwrap();
        b.endpoint = "https://proxy.example.com/v1".into();
        assert!(
            ChatGptTransport::check_binding(&b)
                .unwrap_err()
                .contains("endpoint")
        );
        let mut b = binding(ENDPOINT);
        b.temperature = Some(0.0);
        assert!(
            ChatGptTransport::check_binding(&b)
                .unwrap_err()
                .contains("temperature")
        );
        let mut b = binding(ENDPOINT);
        b.api_key_env = Some("OPENAI_API_KEY".into());
        assert!(
            ChatGptTransport::check_binding(&b)
                .unwrap_err()
                .contains("api_key_env")
        );
        // Unmarked, its scopes' consent would be checked for the bare host
        // an API key is allowed for.
        let mut b = binding(ENDPOINT);
        b.signed_in = false;
        assert!(
            ChatGptTransport::check_binding(&b)
                .unwrap_err()
                .contains("signed_in")
        );
        // The transport itself sends a token nowhere else.
        let f = signed_in(|_| {});
        let t = ChatGptTransport::new(f.path.clone(), HttpClient::new(Duration::from_secs(1)));
        let err = t
            .send(&binding("https://proxy.example.com/v1"), &request())
            .unwrap_err();
        assert!(matches!(err, TransportError::Permanent { .. }), "{err:?}");
    }

    #[test]
    fn nothing_is_sent_without_consent_on_this_host_or_the_plan_scope() {
        // No server: a call that tried to send would fail differently.
        let unreachable = "http://127.0.0.1:9";
        let check = |f: &Fixture| {
            transport(&f.path, unreachable)
                .send(&binding(&format!("{unreachable}/v1")), &request())
                .unwrap_err()
        };
        let no_consent = signed_in(|c| c.background_use = None);
        let err = check(&no_consent);
        let TransportError::SignInRequired { message } = &err else {
            panic!("{err:?}");
        };
        assert!(message.contains("background"), "{message}");
        // Consent given on another machine does not count here.
        let elsewhere = signed_in(|c| {
            c.background_use.as_mut().unwrap().host_id =
                "urn:uuid:00000000-0000-4000-8000-0000000000aa".into();
        });
        assert!(matches!(
            check(&elsewhere),
            TransportError::SignInRequired { .. }
        ));
        let no_plan = signed_in(|c| c.scopes.retain(|s| s != PLAN_SCOPE));
        let err = check(&no_plan);
        let TransportError::SignInRequired { message } = &err else {
            panic!("{err:?}");
        };
        assert!(message.contains(PLAN_SCOPE), "{message}");
        let signed_out = signed_in(Credentials::clear_tokens);
        assert!(matches!(
            check(&signed_out),
            TransportError::SignInRequired { .. }
        ));
        let dir = tempfile::tempdir().unwrap();
        let err = transport(&dir.path().join("none.json"), unreachable)
            .send(&binding(&format!("{unreachable}/v1")), &request())
            .unwrap_err();
        assert!(matches!(err, TransportError::SignInRequired { .. }));
        let _ = &no_consent.host;
    }

    fn expired(c: &mut Credentials) {
        c.saved_at = "2026-01-01T00:00:00Z".into();
    }

    #[test]
    fn an_expiring_token_is_renewed_and_the_rotated_refresh_token_kept() {
        let f = signed_in(expired);
        let (url, server) = serve(2, |r: &Received| {
            if r.request_line.starts_with("POST /api/accounts/oauth/token") {
                Reply::json(
                    200,
                    json!({
                        "access_token": "access-2-secret", "refresh_token": "refresh-2-secret",
                        "token_type": "Bearer", "expires_in": 3600,
                        "scope": format!("openid offline_access {PLAN_SCOPE}"),
                    })
                    .to_string(),
                )
            } else {
                sse_reply("ok")
            }
        });
        transport(&f.path, &url)
            .send(&binding(&format!("{url}/v1")), &request())
            .unwrap();
        let received = server.join().unwrap();
        let form = &received[0].body;
        for field in [
            "grant_type=refresh_token",
            "client_id=oaiapp_test",
            "refresh_token=refresh-token-secret",
            "resource=https%3A%2F%2Fapi.openai.com%2Fv1",
        ] {
            assert!(form.contains(field), "{form}");
        }
        // The grant is kept as it is: no scope is asked for.
        assert!(!form.contains("scope"), "{form}");
        assert_eq!(
            received[1].header("authorization"),
            Some("Bearer access-2-secret")
        );
        let saved = credentials::load(&f.path).unwrap().unwrap();
        assert_eq!(saved.refresh_token, Some(Secret::new("refresh-2-secret")));
        assert_eq!(saved.access_token, Some(Secret::new("access-2-secret")));
        // Consent and the account outlive the renewal.
        assert!(saved.background_use_agreed(&f.host));
        assert!(!saved.refresh_due(OffsetDateTime::now_utc(), RENEW_MARGIN));
    }

    #[test]
    fn two_callers_renew_once_and_share_the_new_token() {
        let f = signed_in(expired);
        let refreshes = Arc::new(Mutex::new(0));
        let counted = Arc::clone(&refreshes);
        let (url, server) = serve(3, move |r: &Received| {
            if r.request_line.starts_with("POST /api/accounts/oauth/token") {
                *counted.lock().unwrap() += 1;
                // Slow enough that the other caller waits on the lock.
                std::thread::sleep(Duration::from_millis(150));
                Reply::json(
                    200,
                    json!({"access_token": "access-2-secret", "refresh_token": "refresh-2-secret",
                           "expires_in": 3600, "scope": PLAN_SCOPE})
                    .to_string(),
                )
            } else {
                sse_reply("ok")
            }
        });
        let a = Arc::new(transport(&f.path, &url));
        let b = Arc::new(transport(&f.path, &url));
        let endpoint = format!("{url}/v1");
        let calls: Vec<_> = [a, b]
            .into_iter()
            .map(|t| {
                let endpoint = endpoint.clone();
                std::thread::spawn(move || t.send(&binding(&endpoint), &request()))
            })
            .collect();
        for call in calls {
            call.join().unwrap().unwrap();
        }
        let received = server.join().unwrap();
        assert_eq!(*refreshes.lock().unwrap(), 1);
        let bearers: Vec<_> = received
            .iter()
            .filter(|r| r.request_line.starts_with("POST /v1/responses"))
            .map(|r| r.header("authorization").unwrap().to_string())
            .collect();
        assert_eq!(bearers, vec!["Bearer access-2-secret"; 2]);
    }

    #[test]
    fn an_unusable_refresh_token_signs_out_and_says_so() {
        for code in [
            "invalid_grant",
            "invalid_refresh_token",
            "token_expired",
            "refresh_token_expired",
            "refresh_token_invalidated",
            "refresh_token_reused",
        ] {
            let f = signed_in(expired);
            let body = json!({"error": code, "error_description": "refresh-token-secret is spent"})
                .to_string();
            let (url, _server) = serve(1, move |_| Reply::json(400, body.clone()));
            let err = transport(&f.path, &url)
                .send(&binding(&format!("{url}/v1")), &request())
                .unwrap_err();
            let TransportError::SignInRequired { message } = &err else {
                panic!("{code}: {err:?}");
            };
            assert!(message.contains(code), "{message}");
            assert!(!message.contains("secret"), "{message}");
            let saved = credentials::load(&f.path).unwrap().unwrap();
            assert_eq!((saved.access_token, saved.refresh_token), (None, None));
            // Kept for the next sign-in: the account and its client.
            assert_eq!(saved.client_id, "oaiapp_test");
        }
        // The same code nested as an error object reads the same.
        let f = signed_in(expired);
        let (url, _server) = serve(1, |_| {
            Reply::json(401, r#"{"error": {"code": "refresh_token_reused"}}"#)
        });
        assert!(matches!(
            transport(&f.path, &url).send(&binding(&format!("{url}/v1")), &request()),
            Err(TransportError::SignInRequired { .. })
        ));
    }

    #[test]
    fn a_rejected_client_or_a_failed_renewal_keeps_the_tokens() {
        let f = signed_in(expired);
        let (url, _server) = serve(1, |_| Reply::json(401, r#"{"error": "invalid_client"}"#));
        let err = transport(&f.path, &url)
            .send(&binding(&format!("{url}/v1")), &request())
            .unwrap_err();
        let TransportError::Permanent { message } = &err else {
            panic!("{err:?}");
        };
        assert!(message.contains("invalid_client"), "{message}");
        let f2 = signed_in(expired);
        let (url, _server) = serve(1, |_| Reply::json(503, "{}"));
        assert!(matches!(
            transport(&f2.path, &url).send(&binding(&format!("{url}/v1")), &request()),
            Err(TransportError::Transient { .. })
        ));
        for f in [f, f2] {
            let saved = credentials::load(&f.path).unwrap().unwrap();
            assert!(saved.refresh_token.is_some());
        }
    }

    #[test]
    fn a_turned_down_token_is_renewed_before_the_next_call() {
        let f = signed_in(|_| {});
        let (url, server) = serve(3, |r: &Received| {
            if r.request_line.starts_with("POST /api/accounts/oauth/token") {
                Reply::json(
                    200,
                    json!({"access_token": "access-2-secret", "refresh_token": "refresh-2-secret",
                           "expires_in": 3600, "scope": PLAN_SCOPE})
                    .to_string(),
                )
            } else if r.header("authorization") == Some("Bearer access-token-secret") {
                Reply::json(401, r#"{"detail": "not accepted"}"#)
            } else {
                sse_reply("ok")
            }
        });
        let t = transport(&f.path, &url);
        let endpoint = format!("{url}/v1");
        // One request, no hidden retry: the 401 is the answer.
        assert!(matches!(
            t.send(&binding(&endpoint), &request()),
            Err(TransportError::SignInRequired { .. })
        ));
        t.send(&binding(&endpoint), &request()).unwrap();
        let lines: Vec<_> = server
            .join()
            .unwrap()
            .into_iter()
            .map(|r| r.request_line)
            .collect();
        assert_eq!(
            lines,
            [
                "POST /v1/responses HTTP/1.1",
                "POST /api/accounts/oauth/token HTTP/1.1",
                "POST /v1/responses HTTP/1.1"
            ]
        );
    }

    #[test]
    fn no_error_repeats_a_token_a_server_echoed() {
        let f = signed_in(|_| {});
        let (url, _server) = serve(1, |_| {
            Reply::json(
                403,
                r#"{"error": {"code": "chatpass_v2_scope_not_authorized",
                    "message": "Bearer access-token-secret is not authorized"}}"#,
            )
        });
        let err = transport(&f.path, &url)
            .send(&binding(&format!("{url}/v1")), &request())
            .unwrap_err();
        let shown = format!("{err} {err:?}");
        assert!(!shown.contains("access-token-secret"), "{shown}");
        assert!(
            shown.contains("chatpass_v2_scope_not_authorized"),
            "{shown}"
        );
        assert!(!format!("{:?}", transport(&f.path, &url)).contains("secret"));
    }

    #[test]
    fn the_catalog_lists_the_models_meant_for_display() {
        let f = signed_in(|c| c.background_use = None);
        let (url, server) = serve(1, |_| {
            Reply::json(
                200,
                json!({"models": [
                    {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list"},
                    {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide"},
                    {"slug": "gpt-b", "display_name": "GPT B", "visibility": "list"},
                ]})
                .to_string(),
            )
        });
        // Asked for by the owner: no background consent is needed.
        let models = transport(&f.path, &url).list_models().unwrap();
        assert_eq!(
            models.iter().map(|m| m.slug.as_str()).collect::<Vec<_>>(),
            ["gpt-a", "gpt-b"]
        );
        let received = server.join().unwrap().remove(0);
        assert_eq!(received.request_line, "GET /v1/models HTTP/1.1");
        assert_eq!(
            received.header("authorization"),
            Some("Bearer access-token-secret")
        );
    }
}

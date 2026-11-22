//! The ChatGPT plan transport: model calls paid for by the owner's ChatGPT
//! Plus or Pro plan instead of API credits, through Sign in with ChatGPT.
//!
//! The owner signs in once, with `core-memory login chatgpt`. That saves a
//! credentials file on this machine that only its owner can read. Each call
//! reads the file, renews the access token when it is about to expire, and
//! sends one streamed `POST https://api.openai.com/v1/responses`.
//!
//! The terms of Sign in with ChatGPT are built in, not left to the operator:
//!
//! - Tokens are kept only in that local file, written whole and owner-only.
//!   They are never logged, never put in a URL, and never repeated in an
//!   error. Their `Debug` form is a placeholder.
//! - Calls are made in the background, so the owner's express consent to
//!   that is asked for at sign-in and recorded in the file. Without it, or
//!   on a machine other than the one it was given on, nothing is sent.
//! - The token is used only for this engine's own calls, to the fixed
//!   OpenAI endpoints below. Nothing in a config file or the environment
//!   can point it anywhere else, and nothing hands it to another program.
//! - The app is named "Core Memory" when the owner signs in.

mod credentials;
mod idtoken;
mod oauth;
mod responses;
mod transport;

pub use credentials::{
    BackgroundUse, CONSENT_REVISION, Credentials, FileError, Secret, default_path, ensure_host_id,
    host_id, host_id_path, load, save,
};
pub use oauth::{
    ImportOutcome, PendingSignIn, Revocation, SignInError, SignInOptions, SignedIn,
    import_credentials, sign_out,
};
pub use transport::{ChatGptTransport, Model};

/// The provider name a binding gives.
pub const PROVIDER: &str = "openai-chatgpt";
/// The only endpoint a binding may name. It is also the `resource` the
/// tokens are issued for.
pub const ENDPOINT: &str = "https://api.openai.com/v1";
/// The name the app is registered under when the owner signs in.
pub const APP_NAME: &str = "Core Memory";
/// Where the owner sees and limits what Core Memory uses of the plan.
pub const USAGE_URL: &str = "https://chatgpt.com/settings/usage";
/// The scope that lets the tokens use the owner's ChatGPT plan.
pub const PLAN_SCOPE: &str = "chatgpt.tokens.use.direct";

/// What the owner is asked before Core Memory may use the plan in the
/// background: revision [`CONSENT_REVISION`] of the question. A change of
/// substance here bumps the revision, so earlier answers are asked again.
pub const BACKGROUND_USE_QUESTION: &str = "\
Core Memory will use your ChatGPT plan for its own model calls: reading the
chunks and tool calls it stores, and judging how they relate. It makes these
calls in the background, whenever it runs, without asking you each time.
They count toward your plan's usage limits, which you can see and change at
https://chatgpt.com/settings/usage.

Your plan may be used only for your own activity. Let a scope send text to
this binding only if the scope holds your own conversations, notes and tool
runs, never another person's. Core Memory never shares this sign-in with
another program or another person.

Do you agree to Core Memory using your ChatGPT plan in the background?";

/// Where OpenAI is reached. The production values are fixed in this crate;
/// tests point them at a server of their own.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Endpoints {
    /// Sign-in and token endpoints.
    auth: String,
    /// What a valid ID token names as its issuer.
    issuer: String,
    /// The Responses API.
    api: String,
}

impl Endpoints {
    pub fn openai() -> Self {
        Self {
            auth: "https://auth.openai.com".into(),
            issuer: "https://auth.openai.com".into(),
            api: ENDPOINT.into(),
        }
    }

    /// Everything at one test server.
    #[cfg(test)]
    pub(crate) fn at(base: &str) -> Self {
        Self {
            auth: base.into(),
            issuer: base.into(),
            api: format!("{base}/v1"),
        }
    }

    fn authorize_url(&self) -> String {
        format!("{}/api/accounts/authorize", self.auth)
    }

    fn token_url(&self) -> String {
        format!("{}/api/accounts/oauth/token", self.auth)
    }

    fn jwks_url(&self) -> String {
        format!("{}/.well-known/jwks.json", self.auth)
    }

    fn openid_configuration_url(&self) -> String {
        format!("{}/.well-known/openid-configuration", self.auth)
    }

    fn responses_url(&self) -> String {
        format!("{}/responses", self.api)
    }

    fn models_url(&self) -> String {
        format!("{}/models", self.api)
    }
}

/// A short, plain code from a provider's reply, or `None`. Anything else
/// is not repeated: a reply is only as harmless as what the server put in
/// it.
fn plain_code(value: &serde_json::Value) -> Option<&str> {
    value.as_str().and_then(plain)
}

/// `text` if it is a short, plain code.
fn plain(text: &str) -> Option<&str> {
    Some(text).filter(|code| {
        !code.is_empty()
            && code.len() <= 80
            && code
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.'))
    })
}

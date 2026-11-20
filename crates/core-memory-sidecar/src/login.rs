//! `core-memory login chatgpt` and `core-memory logout chatgpt`: signing in
//! to a ChatGPT account so the engine's model calls use its plan instead of
//! API credits.
//!
//! Signing in asks the owner, first and in so many words, whether Core
//! Memory may use the plan in the background, and records the answer. No
//! call is made without it.

use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use core_memory_model_chatgpt::{
    self as chatgpt, BACKGROUND_USE_QUESTION, ChatGptTransport, PendingSignIn, Revocation,
    SignInOptions, USAGE_URL,
};
use core_memory_model_http::HttpClient;
use serde_json::{Value, json};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

use crate::config::Loaded;
use crate::report::{self, Stop};
use crate::runtime::{credentials_path, model_roots};

/// How long the browser has to come back.
const SIGN_IN_WAIT: Duration = Duration::from_secs(300);
/// Each request to OpenAI while signing in or out.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// What `login chatgpt` was asked to do.
#[derive(Debug, Clone, Default)]
pub struct LoginOptions {
    pub credentials: Option<PathBuf>,
    pub status: bool,
    pub models: bool,
    pub import: Option<PathBuf>,
    pub ask_again: bool,
    pub allow_background_use: bool,
    pub no_browser: bool,
}

/// The config, if the file is there. Signing in needs none.
fn config(path: &Path) -> Result<Option<Loaded>, Stop> {
    if path.exists() {
        Ok(Some(Loaded::read(path)?))
    } else {
        Ok(None)
    }
}

/// The credentials file to use: the one given, else the one the config's
/// `openai-chatgpt` bindings name, else the default one.
pub fn credentials_file(config: Option<&Loaded>, given: Option<&Path>) -> Result<PathBuf, Stop> {
    if let Some(given) = given {
        return Ok(given.to_path_buf());
    }
    let mut named: Vec<PathBuf> = Vec::new();
    if let Some(loaded) = config {
        for binding in &loaded.config.bindings {
            if binding.provider == chatgpt::PROVIDER {
                let path = credentials_path(loaded, binding)?;
                if !named.contains(&path) {
                    named.push(path);
                }
            }
        }
    }
    match named.len() {
        0 => chatgpt::default_path().ok_or_else(|| {
            Stop::Failed(
                "no config folder to keep the sign-in in (neither HOME nor XDG_CONFIG_HOME is \
                 set); give --credentials"
                    .into(),
            )
        }),
        1 => Ok(named.remove(0)),
        _ => Err(Stop::Failed(
            "the config's bindings name more than one credentials file; give --credentials to \
             say which"
                .into(),
        )),
    }
}

/// An HTTP client that trusts what the config's `[models] ca_file` adds.
fn client(config: Option<&Loaded>) -> Result<HttpClient, Stop> {
    let roots = match config {
        Some(loaded) => model_roots(loaded)?,
        None => None,
    };
    Ok(HttpClient::trusting(REQUEST_TIMEOUT, roots.as_ref()))
}

/// Asks whether Core Memory may use the plan in the background. Only
/// `yes` agrees.
pub fn ask_background_use(input: &mut impl BufRead, output: &mut impl Write) -> io::Result<bool> {
    writeln!(output, "\n{BACKGROUND_USE_QUESTION}")?;
    write!(output, "Type yes to agree: ")?;
    output.flush()?;
    let mut answer = String::new();
    input.read_line(&mut answer)?;
    writeln!(output)?;
    Ok(answer.trim().eq_ignore_ascii_case("yes"))
}

/// The owner's answer: given with `--allow-background-use`, or asked.
fn consent(
    options: &LoginOptions,
    input: &mut impl BufRead,
    output: &mut impl Write,
) -> Result<(), Stop> {
    if options.allow_background_use {
        return Ok(());
    }
    let agreed = ask_background_use(input, output)
        .map_err(|e| Stop::Failed(format!("the question could not be asked: {e}")))?;
    if agreed {
        Ok(())
    } else {
        Err(Stop::Failed(
            "Core Memory may not use your ChatGPT plan in the background, so it was not signed \
             in and nothing was saved. Its calls can use an API key instead"
                .into(),
        ))
    }
}

/// `core-memory login chatgpt`, reading answers from `input` and writing
/// questions to `output`.
pub fn login(
    config_path: &Path,
    options: &LoginOptions,
    input: &mut impl BufRead,
    output: &mut impl Write,
) -> Result<Vec<Value>, Stop> {
    let config = config(config_path)?;
    let path = credentials_file(config.as_ref(), options.credentials.as_deref())?;
    if options.status {
        return Ok(vec![status(&path)?]);
    }
    if options.models {
        return models(&path, client(config.as_ref())?);
    }
    if let Some(source) = &options.import {
        consent(options, input, output)?;
        let imported = chatgpt::import_credentials(source, &path, true)
            .map_err(|e| Stop::Failed(e.to_string()))?;
        report::say(&format!(
            "Imported. The copy at {} holds the sign-in's tokens: delete it now.",
            source.display()
        ));
        return Ok(vec![json!({
            "signed_in": true,
            "email": imported.email,
            "plan": plan(imported.plan_granted),
            "background_use": "agreed",
            "host_id": imported.host_id,
            "credentials": path,
            "manage_usage": USAGE_URL,
        })]);
    }
    consent(options, input, output)?;
    let pending = PendingSignIn::start(
        SignInOptions {
            credentials: path.clone(),
            ask_again: options.ask_again,
            port: None,
            background_use: true,
        },
        client(config.as_ref())?,
    )
    .map_err(|e| Stop::Failed(e.to_string()))?;
    let opened = !options.no_browser && open_browser(pending.url());
    report::say(&format!(
        "{}\n\n  {}\n\nWaiting for the browser to come back...",
        if opened {
            "Your browser is opening to sign in to ChatGPT. If it does not, open:"
        } else {
            "To sign in to ChatGPT, open this in a browser on this machine:"
        },
        pending.url()
    ));
    let signed = pending
        .finish(SIGN_IN_WAIT)
        .map_err(|e| Stop::Failed(e.to_string()))?;
    if signed.plan_granted && signed.first {
        report::say(&format!(
            "\nYou're using your ChatGPT plan. Core Memory's model calls now count toward it \
             instead of API credits. Manage what it may use at {USAGE_URL}"
        ));
    }
    if !signed.plan_granted {
        report::say(
            "\nSigned in, but Core Memory was not allowed to use your ChatGPT plan, so it will \
             make no calls with this sign-in. To allow it, run `core-memory login chatgpt \
             --consent`. Or use an API key with an `openai` binding instead.",
        );
    }
    Ok(vec![json!({
        "signed_in": true,
        "email": signed.email,
        "plan": plan(signed.plan_granted),
        "background_use": "agreed",
        "credentials": path,
        "manage_usage": USAGE_URL,
    })])
}

fn plan(granted: bool) -> &'static str {
    if granted { "granted" } else { "not granted" }
}

/// What the credentials file says, without its tokens. Reads no network.
pub fn status(path: &Path) -> Result<Value, Stop> {
    let saved = chatgpt::load(path).map_err(|e| Stop::Failed(e.to_string()))?;
    let host = chatgpt::host_id(path).map_err(|e| Stop::Failed(e.to_string()))?;
    let Some(saved) = saved else {
        return Ok(json!({"signed_in": false, "credentials": path, "host_id": host}));
    };
    let background = match (&saved.background_use, &host) {
        (Some(agreed), Some(host)) if saved.background_use_agreed(host) => {
            json!({"agreed_at": agreed.agreed_at})
        }
        (Some(_), _) => json!("agreed on another machine: import or sign in here"),
        (None, _) => Value::Null,
    };
    Ok(json!({
        "signed_in": saved.signed_in(),
        "email": saved.email,
        "plan": plan(saved.plan_granted()),
        "background_use": background,
        "access_token_expires_at": saved
            .expires_at()
            .filter(|_| saved.access_token.is_some())
            .and_then(|t| t.format(&Rfc3339).ok()),
        "renewable": saved.refresh_token.is_some(),
        "renewal_due": saved.signed_in()
            && saved.refresh_due(OffsetDateTime::now_utc(), Duration::from_secs(300)),
        "credentials": path,
        "host_id": host,
        "manage_usage": USAGE_URL,
    }))
}

/// The models the signed-in account may use.
fn models(path: &Path, client: HttpClient) -> Result<Vec<Value>, Stop> {
    let models = ChatGptTransport::new(path.to_path_buf(), client)
        .list_models()
        .map_err(|e| Stop::Failed(e.to_string()))?;
    Ok(models
        .into_iter()
        .map(|m| json!({"model": m.slug, "display_name": m.display_name}))
        .collect())
}

/// `core-memory logout chatgpt`.
pub fn logout(config_path: &Path, given: Option<&Path>) -> Result<Value, Stop> {
    let config = config(config_path)?;
    let path = credentials_file(config.as_ref(), given)?;
    let ended = chatgpt::sign_out(&path, &client(config.as_ref())?)
        .map_err(|e| Stop::Failed(e.to_string()))?;
    Ok(match ended {
        None => json!({"signed_in": false, "credentials": path}),
        Some(Revocation::Confirmed) => {
            json!({"signed_out": true, "session_ended": true, "credentials": path})
        }
        Some(Revocation::NothingToEnd) => {
            json!({"signed_out": true, "session_ended": null, "credentials": path})
        }
        Some(Revocation::NotConfirmed(why)) => {
            report::say(&format!(
                "Signed out here, but OpenAI did not confirm the session ended ({why}). \
                 Disconnect Core Memory in ChatGPT's settings to be sure: {USAGE_URL}"
            ));
            json!({"signed_out": true, "session_ended": false, "credentials": path})
        }
    })
}

/// Opens `url` in the system browser, if there is one. Best effort: the
/// URL is printed either way.
fn open_browser(url: &str) -> bool {
    let mut command = if cfg!(target_os = "macos") {
        Command::new("open")
    } else if cfg!(windows) {
        // Not `cmd /c start`, which reads `&` in the URL as its own.
        let mut command = Command::new("rundll32");
        command.arg("url.dll,FileProtocolHandler");
        command
    } else {
        Command::new("xdg-open")
    };
    command
        .arg(url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config_with(dir: &Path, bindings: &str) -> Loaded {
        let path = dir.join("core-memory.toml");
        std::fs::write(&path, bindings).unwrap();
        Loaded::read(&path).unwrap()
    }

    const BINDING: &str = r#"
[[binding]]
id = "plan"
provider = "openai-chatgpt"
endpoint = "https://api.openai.com/v1"
model = "gpt-test"
max_output_tokens = 2048
"#;

    #[test]
    fn only_yes_agrees_to_background_use() {
        for (answer, agreed) in [
            ("yes\n", true),
            ("YES\n", true),
            ("y\n", false),
            ("no\n", false),
            ("", false),
        ] {
            let mut asked = Vec::new();
            let got = ask_background_use(&mut answer.as_bytes(), &mut asked).unwrap();
            assert_eq!(got, agreed, "{answer:?}");
            let asked = String::from_utf8(asked).unwrap();
            assert!(asked.contains("in the background"), "{asked}");
            assert!(asked.contains("only for your own activity"), "{asked}");
        }
    }

    #[test]
    fn refusing_background_use_signs_nothing_in() {
        let dir = tempfile::tempdir().unwrap();
        let credentials = dir.path().join("plan").join("chatgpt.json");
        let options = LoginOptions {
            credentials: Some(credentials.clone()),
            no_browser: true,
            ..LoginOptions::default()
        };
        let err = login(
            &dir.path().join("none.toml"),
            &options,
            &mut "no\n".as_bytes(),
            &mut Vec::new(),
        )
        .unwrap_err();
        assert!(err.message().contains("nothing was saved"), "{err}");
        // Not even a host ID or a folder: nothing was started.
        assert!(!credentials.parent().unwrap().exists());
        // An import is refused alike.
        let options = LoginOptions {
            import: Some(dir.path().join("copied.json")),
            ..options
        };
        login(
            &dir.path().join("none.toml"),
            &options,
            &mut "\n".as_bytes(),
            &mut Vec::new(),
        )
        .unwrap_err();
        assert!(!credentials.parent().unwrap().exists());
    }

    #[test]
    fn the_credentials_file_is_the_one_given_or_the_bindings_or_the_default() {
        let dir = tempfile::tempdir().unwrap();
        let given = dir.path().join("given.json");
        assert_eq!(credentials_file(None, Some(&given)).unwrap(), given);
        let named = config_with(
            dir.path(),
            &format!("{BINDING}credentials_file = \"secrets/plan.json\"\n"),
        );
        assert_eq!(
            credentials_file(Some(&named), None).unwrap(),
            dir.path().join("secrets/plan.json")
        );
        // Two bindings naming two files: which is meant is not guessed.
        let two = config_with(
            dir.path(),
            &format!(
                "{BINDING}credentials_file = \"a.json\"\n{}credentials_file = \"b.json\"\n",
                BINDING.replace("id = \"plan\"", "id = \"plan2\"")
            ),
        );
        let err = credentials_file(Some(&two), None).unwrap_err();
        assert!(err.message().contains("--credentials"), "{err}");
    }

    #[test]
    fn status_shows_the_sign_in_and_never_a_token() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let none = status(&path).unwrap();
        assert_eq!(none["signed_in"], false);
        let host = chatgpt::ensure_host_id(&path).unwrap();
        let saved = chatgpt::Credentials {
            email: Some("user@example.com".into()),
            issuer: "https://auth.openai.com".into(),
            subject: "user-123".into(),
            client_id: "oaiapp_test".into(),
            ext_agent_host_id: host.clone(),
            id_token: Some(chatgpt::Secret::new("id-token-secret")),
            access_token: Some(chatgpt::Secret::new("access-token-secret")),
            refresh_token: Some(chatgpt::Secret::new("refresh-token-secret")),
            token_type: "Bearer".into(),
            expires_in: 3600,
            earliest_refresh_at: None,
            scopes: vec![chatgpt::PLAN_SCOPE.into()],
            saved_at: "2026-11-20T12:00:00Z".into(),
            background_use: Some(chatgpt::BackgroundUse {
                agreed_at: "2026-11-20T12:00:00Z".into(),
                host_id: host.clone(),
                statement: chatgpt::CONSENT_REVISION,
            }),
        };
        chatgpt::save(&path, &saved).unwrap();
        let shown = status(&path).unwrap();
        assert_eq!(shown["signed_in"], true);
        assert_eq!(shown["plan"], "granted");
        assert_eq!(shown["background_use"]["agreed_at"], "2026-11-20T12:00:00Z");
        assert_eq!(shown["access_token_expires_at"], "2026-11-20T13:00:00Z");
        assert_eq!(shown["host_id"], host);
        let text = shown.to_string();
        assert!(!text.contains("secret"), "{text}");
    }

    #[test]
    fn a_chatgpt_binding_is_checked_and_opened_with_its_file() {
        let dir = tempfile::tempdir().unwrap();
        let loaded = config_with(dir.path(), BINDING);
        let binding = &loaded.config.bindings[0];
        crate::runtime::check_binding(binding).unwrap();
        // Opening reads no file and sends nothing.
        crate::runtime::open_transport(&loaded, binding, None).unwrap();
        let mut keyed = binding.clone();
        keyed.api_key_env = Some("OPENAI_API_KEY".into());
        assert!(crate::runtime::check_binding(&keyed).is_err());
        // A provider with a key takes no credentials file.
        let mut other = binding.clone();
        other.provider = "openai".into();
        other.credentials_file = Some("plan.json".into());
        let err = crate::runtime::check_binding(&other).unwrap_err();
        assert!(err.message().contains("credentials_file"), "{err}");
        let empty = Loaded::read(&{
            let path = dir.path().join("empty.toml");
            std::fs::write(&path, format!("{BINDING}credentials_file = \" \"\n")).unwrap();
            path
        })
        .unwrap_err();
        assert!(empty.contains("credentials_file"), "{empty}");
    }
}

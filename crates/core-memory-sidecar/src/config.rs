//! The `core-memory.toml` file.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::Duration;

use core_memory_contract::Sha256Digest;
use core_memory_core::EmbedderConfig;
use core_memory_core::judge::JudgeConfig;
use core_memory_core::read::ReaderConfig;
use core_memory_core::tool::ToolReaderConfig;
use core_memory_core::{
    BindingConfig, EngineConfig, Limits, ScopeGrants, SourceProfile, Vocabulary, is_identifier,
};
use core_memory_store_postgres::{ConnectConfig, MAX_CONNECT_TIMEOUT, MAX_STATEMENT_TIMEOUT};
use serde::Deserialize;

/// Where the database URL is read from when the config names nothing else.
pub const DEFAULT_URL_ENV: &str = "CORE_MEMORY_DATABASE_URL";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SidecarConfig {
    #[serde(default)]
    pub store: StoreConfig,
    #[serde(default)]
    pub vocabulary: Option<Vocabulary>,
    #[serde(default, rename = "profile")]
    pub profiles: Vec<SourceProfile>,
    #[serde(default)]
    pub limits: Limits,
    /// Has a model read each chunk. Without it the engine calls no model.
    #[serde(default)]
    pub reader: Option<ReaderConfig>,
    /// Has a model read each tool call. Without it tool calls are stored
    /// and not read.
    #[serde(default)]
    pub tool_reader: Option<ToolReaderConfig>,
    /// Has a model judge how new beads relate to the recent ones of their
    /// workspace. Without it nothing is judged.
    #[serde(default)]
    pub judge: Option<JudgeConfig>,
    /// Has a model give each chunk a vector, so that recall finds chunks
    /// by meaning as well as by their words. Without it nothing is
    /// embedded.
    #[serde(default)]
    pub embedder: Option<EmbedderConfig>,
    /// The models the engine may call.
    #[serde(default, rename = "binding")]
    pub bindings: Vec<BindingConfig>,
    /// How the engine reaches the models it calls.
    #[serde(default)]
    pub models: ModelsConfig,
    /// Settings for `core-memory serve`.
    #[serde(default)]
    pub http: Option<HttpConfig>,
}

/// The PostgreSQL database the engine stores everything in.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StoreConfig {
    /// The environment variable holding the database URL, which may carry a
    /// password. Defaults to `CORE_MEMORY_DATABASE_URL`.
    #[serde(default)]
    pub url_env: Option<String>,
    /// The database URL itself. Prefer `url_env`, which keeps a password out
    /// of this file.
    #[serde(default)]
    pub url: Option<String>,
    /// PEM certificates of a private CA to trust as well as the public
    /// roots, relative to the config file.
    #[serde(default)]
    pub ca_file: Option<PathBuf>,
    /// Connections `core-memory serve` keeps for admissions.
    #[serde(default = "default_pool_size")]
    pub pool_size: usize,
    #[serde(default = "default_connect_timeout_ms")]
    pub connect_timeout_ms: u64,
    /// Limit for one statement.
    #[serde(default = "default_statement_timeout_ms")]
    pub statement_timeout_ms: u64,
}

impl std::fmt::Debug for StoreConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // The URL may hold a password.
        f.debug_struct("StoreConfig")
            .field("url_env", &self.url_env)
            .field("url", &self.url.as_ref().map(|_| "<redacted>"))
            .field("ca_file", &self.ca_file)
            .field("pool_size", &self.pool_size)
            .field("connect_timeout_ms", &self.connect_timeout_ms)
            .field("statement_timeout_ms", &self.statement_timeout_ms)
            .finish()
    }
}

impl Default for StoreConfig {
    fn default() -> Self {
        Self {
            url_env: None,
            url: None,
            ca_file: None,
            pool_size: default_pool_size(),
            connect_timeout_ms: default_connect_timeout_ms(),
            statement_timeout_ms: default_statement_timeout_ms(),
        }
    }
}

/// How the engine reaches the models it calls. A proxy is taken from
/// `HTTPS_PROXY`, and `NO_PROXY` lists the hosts it is not used for.
#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelsConfig {
    /// PEM certificates of a private CA to trust as well as the public
    /// roots, relative to the config file: a proxy that re-signs what it
    /// passes on, or a model server of your own.
    #[serde(default)]
    pub ca_file: Option<PathBuf>,
}

fn default_pool_size() -> usize {
    4
}

fn default_connect_timeout_ms() -> u64 {
    10_000
}

fn default_statement_timeout_ms() -> u64 {
    30_000
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HttpConfig {
    #[serde(default = "default_listen")]
    pub listen: String,
    /// Largest request body accepted, in bytes.
    #[serde(default = "default_max_body_bytes")]
    pub max_body_bytes: usize,
    /// Most events in one batch request.
    #[serde(default = "default_max_batch_events")]
    pub max_batch_events: usize,
    /// What a caller is told to wait when the store cannot take an event
    /// right now.
    #[serde(default = "default_busy_retry_after_ms")]
    pub busy_retry_after_ms: u64,
    /// How long a connection may take to send a request's headers or its
    /// body, and how long an idle connection is kept.
    #[serde(default = "default_read_timeout_ms")]
    pub read_timeout_ms: u64,
    /// How long a response may wait for its client to read before the
    /// connection is closed.
    #[serde(default = "default_write_timeout_ms")]
    pub write_timeout_ms: u64,
    /// How long requests in flight get to finish when the server is told to
    /// stop.
    #[serde(default = "default_shutdown_grace_ms")]
    pub shutdown_grace_ms: u64,
    /// Connections served at once. More wait to be accepted.
    #[serde(default = "default_max_connections")]
    pub max_connections: usize,
    /// Callers. Each token is stored only as its SHA-256; generate one with
    /// `core-memory token`.
    #[serde(default, rename = "token")]
    pub tokens: Vec<TokenConfig>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TokenConfig {
    pub id: String,
    pub sha256: Sha256Digest,
    /// Scopes this caller may write. `["*"]` grants every scope.
    #[serde(default)]
    pub scopes: Vec<String>,
    /// Scopes this caller may recall from: read their text back, with what
    /// a judge said of it. None unless named: a token that writes does not
    /// read. `["*"]` grants every scope.
    #[serde(default)]
    pub recall: Vec<String>,
}

fn default_listen() -> String {
    "127.0.0.1:7600".into()
}

fn default_max_body_bytes() -> usize {
    16 << 20
}

fn default_max_batch_events() -> usize {
    256
}

fn default_busy_retry_after_ms() -> u64 {
    1_000
}

fn default_read_timeout_ms() -> u64 {
    30_000
}

fn default_write_timeout_ms() -> u64 {
    30_000
}

fn default_shutdown_grace_ms() -> u64 {
    10_000
}

fn default_max_connections() -> usize {
    1_024
}

/// Most connections `http.max_connections` may allow. Each one is an open
/// file, and the counter that admits them has a limit of its own.
pub const MAX_CONNECTIONS: usize = 1_000_000;

/// A TOML error as its place and what was expected there.
///
/// Nothing the file says is repeated, because any part of it may be the
/// database URL. The parser's message is kept only where it is known to
/// say what was expected and not what was found.
fn describe_toml(text: &str, e: &toml::de::Error) -> String {
    let what = describe_toml_message(e.message());
    match e.span() {
        Some(span) => {
            let before = &text[..span.start.min(text.len())];
            let line = before.matches('\n').count() + 1;
            let column = before.rsplit('\n').next().map_or(0, |l| l.chars().count()) + 1;
            format!("line {line}, column {column}: {what}")
        }
        None => what,
    }
}

fn describe_toml_message(message: &str) -> String {
    // These name what was found, then what was expected. What was found
    // is from the file. What was expected comes last and is this
    // program's own words.
    const FOUND: [(&str, &str); 5] = [
        ("invalid type: ", "a value of the wrong type"),
        ("invalid value: ", "a value that is not allowed"),
        ("invalid length ", "a list of the wrong length"),
        ("unknown field `", "unknown field"),
        ("unknown variant `", "unknown value"),
    ];
    for (prefix, said) in FOUND {
        if message.starts_with(prefix) {
            return match message.rsplit_once(", expected ") {
                Some((_, expected)) if is_plain(expected) => format!("{said}, expected {expected}"),
                _ => said.to_string(),
            };
        }
    }
    // Every other message says what the parser expected, or names one of
    // this program's settings. One that does not look like that is left out.
    if is_plain(message) {
        message.to_string()
    } else {
        "not valid here".to_string()
    }
}

/// Whether `text` is plain words, with anything in backticks either a
/// symbol or a name. A URL, a path or a `key=value` pair is none of these.
fn is_plain(text: &str) -> bool {
    let words = |part: &str| {
        part.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, ' ' | ',' | '.' | '-' | '_' | '\n'))
    };
    let name = |quoted: &str| {
        quoted
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'))
    };
    let mut rest = text;
    while let Some((before, after)) = rest.split_once('`') {
        let Some((quoted, tail)) = after.split_once('`') else {
            return false;
        };
        if !words(before) || !(quoted.chars().count() <= 2 || name(quoted)) {
            return false;
        }
        rest = tail;
    }
    words(rest)
}

/// Whether `name` can be an environment variable's name. Anything else in
/// `url_env` is likely the URL itself, and must not be echoed.
fn is_env_name(name: &str) -> bool {
    let allowed = |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-');
    let mut chars = name.chars();
    chars
        .next()
        .is_some_and(|c| allowed(c) && !c.is_ascii_digit())
        && chars.all(allowed)
}

/// Whether `value` is `host:port`: a socket address, or a host name and a
/// port number. A value that passes cannot be a URL, so it may be repeated
/// in an error.
fn is_listen_address(value: &str) -> bool {
    if value.parse::<std::net::SocketAddr>().is_ok() {
        return true;
    }
    value.rsplit_once(':').is_some_and(|(host, port)| {
        port.parse::<u16>().is_ok()
            && !host.is_empty()
            && host.len() <= 253
            && host
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-'))
    })
}

/// A loaded config with paths resolved against the config file's folder.
#[derive(Debug)]
pub struct Loaded {
    pub config: SidecarConfig,
    pub base: PathBuf,
}

impl Loaded {
    pub fn read(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let mut config: SidecarConfig = toml::from_str(&text)
            .map_err(|e| format!("{}: {}", path.display(), describe_toml(&text, &e)))?;
        // A binding paid by its owner's own plan is a destination of its
        // own, so that a scope's consent to a host with a key does not
        // carry over to it. The provider says which; a config cannot.
        for binding in &mut config.bindings {
            binding.signed_in = binding.provider == core_memory_model_chatgpt::PROVIDER;
        }
        let base = path
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
        let loaded = Self { config, base };
        loaded.validate()?;
        Ok(loaded)
    }

    fn validate(&self) -> Result<(), String> {
        let c = &self.config;
        if c.store.url.is_some() && c.store.url_env.is_some() {
            return Err("[store] takes `url_env` or `url`, not both".into());
        }
        if c.store
            .url_env
            .as_deref()
            .is_some_and(|name| !is_env_name(name))
        {
            return Err(
                "store.url_env must be the name of an environment variable, such as \
                 CORE_MEMORY_DATABASE_URL. Put a URL in that variable, or in store.url"
                    .into(),
            );
        }
        if c.store.pool_size == 0 {
            return Err("store.pool_size must be 1 or more".into());
        }
        for (n, binding) in c.bindings.iter().enumerate() {
            // Anything else here is likely the key itself, and is not
            // repeated.
            if binding
                .api_key_env
                .as_deref()
                .is_some_and(|name| !is_env_name(name))
            {
                return Err(format!(
                    "binding number {}: api_key_env must be the name of an environment \
                     variable that holds the key, such as ANTHROPIC_API_KEY",
                    n + 1
                ));
            }
            if binding
                .credentials_file
                .as_deref()
                .is_some_and(|file| file.trim().is_empty())
            {
                return Err(format!(
                    "binding number {}: credentials_file must name a file, or be left out for \
                     the default one",
                    n + 1
                ));
            }
        }
        for (name, value, max) in [
            (
                "store.connect_timeout_ms",
                c.store.connect_timeout_ms,
                MAX_CONNECT_TIMEOUT,
            ),
            (
                "store.statement_timeout_ms",
                c.store.statement_timeout_ms,
                MAX_STATEMENT_TIMEOUT,
            ),
        ] {
            if value == 0 || u128::from(value) > max.as_millis() {
                return Err(format!("{name} must be between 1 and {}", max.as_millis()));
            }
        }
        if let Some(http) = &c.http {
            if !is_listen_address(&http.listen) {
                return Err(
                    "http.listen must be an address and a port, such as 127.0.0.1:7600".into(),
                );
            }
            let mut ids = BTreeSet::new();
            let mut hashes = BTreeSet::new();
            for (n, t) in http.tokens.iter().enumerate() {
                // An id is repeated in an error only once it is known to
                // be an identifier, which a URL is not.
                if !is_identifier(&t.id) {
                    return Err(format!(
                        "http token number {} has an id that is not a valid identifier",
                        n + 1
                    ));
                }
                if !ids.insert(&t.id) {
                    return Err(format!("http token `{}` is configured twice", t.id));
                }
                if !hashes.insert(t.sha256) {
                    return Err(format!("http token `{}` reuses another token's hash", t.id));
                }
                if t.scopes.is_empty() && t.recall.is_empty() {
                    return Err(format!(
                        "http token `{}` grants nothing: give it `scopes` to write, \
                         `recall` to read, or both",
                        t.id
                    ));
                }
                check_scopes(&format!("http token `{}` scopes", t.id), &t.scopes)?;
                check_scopes(&format!("http token `{}` recall", t.id), &t.recall)?;
            }
            if http.max_batch_events == 0 || http.max_body_bytes == 0 {
                return Err(
                    "http.max_batch_events and http.max_body_bytes must be 1 or more".into(),
                );
            }
            if http.read_timeout_ms == 0 || http.write_timeout_ms == 0 {
                return Err(
                    "http.read_timeout_ms and http.write_timeout_ms must be 1 or more".into(),
                );
            }
            if http.max_connections == 0 || http.max_connections > MAX_CONNECTIONS {
                return Err(format!(
                    "http.max_connections must be between 1 and {MAX_CONNECTIONS}"
                ));
            }
        }
        Ok(())
    }

    pub fn resolve(&self, path: &Path) -> PathBuf {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.base.join(path)
        }
    }

    /// How to reach the store. Fails if the database URL is not set.
    pub fn connect_config(&self) -> Result<ConnectConfig, String> {
        let store = &self.config.store;
        let url = match &store.url {
            Some(url) => url.clone(),
            None => {
                let env = store.url_env.as_deref().unwrap_or(DEFAULT_URL_ENV);
                std::env::var(env)
                    .ok()
                    .filter(|v| !v.trim().is_empty())
                    .ok_or_else(|| {
                        format!("${env} must hold the PostgreSQL URL of the Core Memory store")
                    })?
            }
        };
        Ok(ConnectConfig {
            url,
            ca_file: store.ca_file.as_deref().map(|p| self.resolve(p)),
            connect_timeout: Duration::from_millis(store.connect_timeout_ms),
            statement_timeout: Duration::from_millis(store.statement_timeout_ms),
        })
    }

    pub fn engine_config(&self) -> EngineConfig {
        EngineConfig {
            vocabulary: self.config.vocabulary.clone(),
            profiles: self.config.profiles.clone(),
            limits: self.config.limits,
            reader: self.config.reader.clone(),
            tool_reader: self.config.tool_reader.clone(),
            judge: self.config.judge.clone(),
            embedder: self.config.embedder.clone(),
            bindings: self.config.bindings.clone(),
        }
    }
}

fn check_scopes(what: &str, scopes: &[String]) -> Result<(), String> {
    if scopes.iter().any(|s| s == "*") && scopes.len() > 1 {
        return Err(format!("{what}: `*` must be the only entry"));
    }
    Ok(())
}

/// `["*"]` grants every scope; anything else grants exactly the listed ones.
pub fn grants_from(scopes: &[String]) -> ScopeGrants {
    if scopes.len() == 1 && scopes[0] == "*" {
        ScopeGrants::All
    } else {
        ScopeGrants::Only(scopes.iter().cloned().collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn load(text: &str) -> Result<Loaded, String> {
        let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let path = dir.path().join("core-memory.toml");
        std::fs::write(&path, text).map_err(|e| e.to_string())?;
        Loaded::read(&path)
    }

    #[test]
    fn an_empty_config_is_valid_and_reads_the_url_from_the_environment() {
        let loaded = load("").unwrap();
        assert_eq!(loaded.config.store.pool_size, 4);
        assert!(loaded.config.http.is_none());
    }

    #[test]
    fn the_store_takes_one_source_for_its_url() {
        let err = load("[store]\nurl = \"postgresql://x/y\"\nurl_env = \"X\"\n").unwrap_err();
        assert!(err.contains("not both"), "{err}");
        let loaded =
            load("[store]\nurl = \"postgresql://localhost/memory\"\nca_file = \"ca.pem\"\n")
                .unwrap();
        let connect = loaded.connect_config().unwrap();
        assert_eq!(connect.url, "postgresql://localhost/memory");
        assert!(connect.ca_file.unwrap().ends_with("ca.pem"));
    }

    #[test]
    fn no_error_prints_the_database_url() {
        const SECRET: &str = "hunter2";
        let url = format!("postgresql://engine:{SECRET}@db.internal/memory");
        let token = |id: &str| {
            format!(
                "[[http.token]]\nid = \"{id}\"\nsha256 = \"{}\"\nscopes = [\"acme\"]\n",
                Sha256Digest::of(b"t")
            )
        };
        for text in [
            // A quote the URL should have escaped, the key given twice, the
            // URL where a table belongs, and the URL where a name belongs.
            format!("[store]\nurl = \"{url}\"extra\"\n"),
            format!("[store]\nurl = \"{url}\"\nurl = \"{url}\"\n"),
            format!("store = \"{url}\"\n"),
            format!("[store]\nurl_env = \"{url}\"\n"),
            format!("[store]\nurl = \"{url}\"\npool_size = \"{url}\"\n"),
            format!("[store]\nurl = \"{url}\"\nstatement_timeout_ms = 0\n"),
            format!("[store]\nurl = [\"{url}\"]\n"),
            // A quote inside the value, which the parser prints escaped.
            "store = 'postgresql://engine:hun\"ter2@db.internal/memory'\n".to_string(),
            // The URL as a key, as a table's name, and where one of a
            // fixed set of words belongs.
            format!("\"{url}\" = 1\n"),
            format!("[store]\n\"{url}\" = 1\n"),
            format!("[\"{url}\"]\nx = 1\n"),
            format!(
                "[[profile]]\nid = \"a.b\"\nrev = 1\nsource_type = \"{url}\"\ncontent = \"text\"\n"
            ),
            // Keys written to look like the end of the parser's message.
            format!("\"x`, expected one of `{url}\" = 1\n"),
            format!("[store]\n\"x, expected {url}\" = 1\n"),
            format!("[store]\npool_size = \"x\\\", expected {url}\"\n"),
            // Where a token's id and a listening address belong.
            token(&url),
            format!("[http]\nlisten = \"{url}\"\n"),
        ] {
            let err = load(&text).unwrap_err();
            for part in [SECRET, "ter2", "db.internal", "engine:", "://"] {
                assert!(!err.contains(part), "{part}: {err}");
            }
        }
        let loaded = load(&format!("[store]\nurl = \"{url}\"\n")).unwrap();
        assert!(!format!("{loaded:?}").contains(SECRET));
        // A variable that is not set is named; it holds no secret.
        let err = load("[store]\nurl_env = \"CORE_MEMORY_UNSET_FOR_TESTS\"\n")
            .unwrap()
            .connect_config()
            .unwrap_err();
        assert!(err.contains("$CORE_MEMORY_UNSET_FOR_TESTS"), "{err}");
    }

    #[test]
    fn a_mistake_is_still_placed_and_described() {
        for (text, place, said) in [
            (
                "[store]\npool_size = \"four\"\n",
                "line 2, column 13",
                "a value of the wrong type, expected usize",
            ),
            (
                "[store]\npool_sise = 4\n",
                "line 2, column 1",
                "unknown field, expected one of `url_env`, `url`, `ca_file`, `pool_size`",
            ),
            (
                "[[profile]]\nid = \"a.b\"\nrev = 1\nsource_type = \"chat\"\ncontent = \"text\"\n",
                "line 4",
                "unknown value, expected one of `transcript`, `document`",
            ),
            (
                "[[http.token]]\nid = \"a\"\nscopes = [\"x\"]\n",
                "line 1",
                "missing field `sha256`",
            ),
            ("[store\npool_size = 4\n", "line 1", "expected `]`"),
            ("[store]\npool_size 4\n", "line 2", "expected `=`"),
            (
                "[store]\npool_size = -1\n",
                "line 2",
                "a value that is not allowed, expected usize",
            ),
        ] {
            let err = load(text).unwrap_err();
            assert!(err.contains(place) && err.contains(said), "{text:?}: {err}");
        }
    }

    #[test]
    fn only_plain_words_pass_for_a_parsers_message() {
        for plain in [
            "duplicate key",
            "invalid basic string, expected `\"`",
            "invalid unquoted key, expected letters, numbers, `-`, `_`",
            "one of `url_env`, `url`, `json.whole`",
            "missing field `sha256`",
            "64 lowercase hex characters",
        ] {
            assert!(is_plain(plain), "{plain}");
        }
        for not in [
            "postgresql://engine:hunter2@db.internal/memory",
            "string \"hunter2\"",
            "`host=db password=hunter2`",
            "`a/b`",
            "`unclosed",
            "user@host",
            "key=value",
        ] {
            assert!(!is_plain(not), "{not}");
        }
        // A message this program does not know is reduced to its place.
        assert_eq!(
            describe_toml_message("something new about `postgresql://u:p@h/db`"),
            "not valid here"
        );
        assert_eq!(
            describe_toml_message("unknown field `x`, there are no fields"),
            "unknown field"
        );
    }

    #[test]
    fn settings_that_could_not_work_are_refused_when_the_file_is_read() {
        for (setting, ok) in [
            ("max_connections = 1", true),
            ("max_connections = 1000000", true),
            ("max_connections = 1000001", false),
            ("max_connections = 9223372036854775807", false),
            ("max_connections = 0", false),
            ("write_timeout_ms = 1", true),
            ("write_timeout_ms = 0", false),
            ("read_timeout_ms = 0", false),
            ("listen = \"127.0.0.1:7600\"", true),
            ("listen = \"[::1]:7600\"", true),
            ("listen = \"localhost:0\"", true),
            ("listen = \"localhost\"", false),
            ("listen = \"http://localhost:7600\"", false),
            ("listen = \"127.0.0.1:99999\"", false),
            ("listen = \"\"", false),
        ] {
            let result = load(&format!("[http]\n{setting}\n"));
            assert_eq!(result.is_ok(), ok, "{setting}: {:?}", result.err());
        }
    }

    #[test]
    fn a_url_variable_may_have_any_name_an_environment_allows() {
        for (name, ok) in [
            ("CORE_MEMORY_DATABASE_URL", true),
            ("db.url", true),
            ("DB-URL", true),
            ("_x", true),
            ("9x", false),
            ("", false),
            ("a b", false),
            ("A=B", false),
            ("postgresql://x", false),
        ] {
            let result = load(&format!("[store]\nurl_env = \"{name}\"\n"));
            assert_eq!(result.is_ok(), ok, "{name}: {:?}", result.err());
        }
    }

    #[test]
    fn timeouts_the_database_driver_would_refuse_are_refused_here() {
        for (setting, ok) in [
            ("statement_timeout_ms = 1", true),
            ("statement_timeout_ms = 3600000", true),
            ("statement_timeout_ms = 3600001", false),
            ("statement_timeout_ms = 32768000", false),
            ("connect_timeout_ms = 300001", false),
            ("connect_timeout_ms = 0", false),
        ] {
            let result = load(&format!("[store]\n{setting}\n"));
            assert_eq!(result.is_ok(), ok, "{setting}: {:?}", result.err());
        }
    }

    #[test]
    fn settings_from_the_outbox_era_are_refused_by_name() {
        for old in [
            "[grants]\nscopes = [\"acme\"]\n",
            "[[sink]]\nid = \"r\"\nkind = \"jsonl\"\npath = \"r.jsonl\"\n",
            "[store]\npath = \"store.sqlite\"\n",
        ] {
            let err = load(old).unwrap_err();
            assert!(err.contains("unknown field"), "{err}");
        }
    }

    #[test]
    fn tokens_must_be_distinct_and_grant_something() {
        let hash = Sha256Digest::of(b"t");
        let token = |id: &str| {
            format!("[[http.token]]\nid = \"{id}\"\nsha256 = \"{hash}\"\nscopes = [\"acme\"]\n")
        };
        let err = load(&format!("{}{}", token("a"), token("b"))).unwrap_err();
        assert!(err.contains("reuses another token's hash"), "{err}");
        let err = load(&token("a").replace("[\"acme\"]", "[]")).unwrap_err();
        assert!(err.contains("grants nothing"), "{err}");
        let err = load(&token("a").replace("scopes = [\"acme\"]", "")).unwrap_err();
        assert!(err.contains("grants nothing"), "{err}");
        let err = load(&format!("{}recall = [\"*\", \"acme\"]\n", token("a"))).unwrap_err();
        assert!(err.contains("`*` must be the only entry"), "{err}");
    }

    #[test]
    fn a_token_may_recall_without_writing_and_writes_without_recalling() {
        let hash = Sha256Digest::of(b"t");
        let loaded = load(&format!(
            "[[http.token]]\nid = \"reader\"\nsha256 = \"{hash}\"\nrecall = [\"acme\"]\n"
        ))
        .unwrap();
        let token = &loaded.config.http.as_ref().unwrap().tokens[0];
        assert!(token.scopes.is_empty());
        assert_eq!(token.recall, ["acme"]);
        let loaded = load(&format!(
            "[[http.token]]\nid = \"writer\"\nsha256 = \"{hash}\"\nscopes = [\"acme\"]\n"
        ))
        .unwrap();
        assert!(
            loaded.config.http.as_ref().unwrap().tokens[0]
                .recall
                .is_empty()
        );
    }
}

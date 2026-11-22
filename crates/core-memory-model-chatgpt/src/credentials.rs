//! The credentials file: one ChatGPT account's sign-in, on this machine.
//!
//! It is written whole and owner-only (`0600`), so a reader never sees half
//! of it and no other user sees any of it. One that other users can read is
//! refused, as SSH refuses such a key. Beside it lies this machine's host ID
//! (`chatgpt-host-id`), which every sign-in from here sends.

use std::ffi::OsString;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;

/// The revision of the background-use question the owner answers at sign-in.
/// A consent given to an earlier wording is asked for again.
pub const CONSENT_REVISION: u32 = 1;

/// A token. Its `Debug` form is a placeholder, so one is never printed by
/// accident.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Secret(String);

impl Secret {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// The token itself, for the one header or form field it goes in.
    pub(crate) fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secret(..)")
    }
}

/// One account's sign-in: who it is, the client registered for it, and its
/// tokens. The fields are those OpenAI's guide lists, and one of Core
/// Memory's own: the owner's consent to calls made in the background.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Credentials {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub email: Option<String>,
    pub issuer: String,
    /// The account, as the validated ID token names it.
    pub subject: String,
    /// The client issued when the account was first signed in. Later
    /// sign-ins to the same account reuse it.
    pub client_id: String,
    /// The host the file was saved on.
    pub ext_agent_host_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id_token: Option<Secret>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access_token: Option<Secret>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub refresh_token: Option<Secret>,
    #[serde(default = "bearer")]
    pub token_type: String,
    /// Seconds the access token lasts from `saved_at`.
    #[serde(default)]
    pub expires_in: u64,
    /// When the token endpoint said a refresh may first be made, as it
    /// gave it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub earliest_refresh_at: Option<serde_json::Value>,
    /// The scopes granted, as the token endpoint gave them.
    #[serde(default)]
    pub scopes: Vec<String>,
    /// When the tokens were received, in RFC 3339 UTC.
    pub saved_at: String,
    /// The owner's consent to calls made in the background.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background_use: Option<BackgroundUse>,
}

fn bearer() -> String {
    "Bearer".into()
}

/// The owner's answer to the background-use question.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BackgroundUse {
    /// When they agreed, in RFC 3339 UTC.
    pub agreed_at: String,
    /// The host they agreed on. Consent given on one machine does not let
    /// another call.
    pub host_id: String,
    /// The revision of the question they answered.
    pub statement: u32,
}

impl Credentials {
    /// Whether the plan scope was granted.
    pub fn plan_granted(&self) -> bool {
        self.scopes.iter().any(|s| s == crate::PLAN_SCOPE)
    }

    /// Whether the file holds tokens a call can use or renew.
    pub fn signed_in(&self) -> bool {
        self.access_token.is_some() || self.refresh_token.is_some()
    }

    /// When the access token expires, if the file says.
    pub fn expires_at(&self) -> Option<OffsetDateTime> {
        let saved = OffsetDateTime::parse(&self.saved_at, &Rfc3339).ok()?;
        let lasts = i64::try_from(self.expires_in).ok()?;
        saved.checked_add(time::Duration::seconds(lasts))
    }

    /// When the token endpoint said a refresh may first be made: Unix
    /// seconds or RFC 3339, if it gave either.
    fn earliest_refresh(&self) -> Option<OffsetDateTime> {
        match self.earliest_refresh_at.as_ref()? {
            serde_json::Value::Number(n) => OffsetDateTime::from_unix_timestamp(n.as_i64()?).ok(),
            serde_json::Value::String(s) => OffsetDateTime::parse(s, &Rfc3339).ok(),
            _ => None,
        }
    }

    /// Whether the access token is to be renewed before a call at `now`:
    /// it expires within `margin`, or the file does not say when it does.
    /// A refresh waits for `earliest_refresh_at` while the token has more
    /// than half a minute left.
    pub fn refresh_due(&self, now: OffsetDateTime, margin: Duration) -> bool {
        let Some(expires) = self.expires_at() else {
            return true;
        };
        let margin = time::Duration::try_from(margin).unwrap_or(time::Duration::MINUTE);
        let mut due = expires - margin;
        if let Some(earliest) = self.earliest_refresh() {
            due = due.max(earliest).min(expires - time::Duration::seconds(30));
        }
        now >= due
    }

    /// Whether the owner agreed to background calls, on host `host`, to
    /// this revision of the question.
    pub fn background_use_agreed(&self, host: &str) -> bool {
        self.background_use
            .as_ref()
            .is_some_and(|c| c.host_id == host && c.statement >= CONSENT_REVISION)
            && self.ext_agent_host_id == host
    }

    /// Forgets the tokens and the consent, keeping the account, its client
    /// and the host, for a later sign-in.
    pub fn clear_tokens(&mut self) {
        self.id_token = None;
        self.access_token = None;
        self.refresh_token = None;
        self.earliest_refresh_at = None;
        self.background_use = None;
    }
}

/// Now, in RFC 3339 UTC to the second.
pub(crate) fn now_rfc3339() -> String {
    let now = OffsetDateTime::now_utc();
    now.replace_nanosecond(0)
        .unwrap_or(now)
        .format(&Rfc3339)
        .unwrap_or_default()
}

/// Why the credentials file or the host ID could not be used. No message
/// repeats a path or anything read from the file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FileError {
    /// Other users can read it.
    Exposed,
    /// It is not what this build writes.
    Unreadable(String),
    /// Another process held the file's lock for too long.
    Busy,
    Io(String),
}

impl fmt::Display for FileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FileError::Exposed => f.write_str(
                "other users can read the credentials file; make it readable by its owner \
                 only (chmod 600) and its folder too (chmod 700)",
            ),
            FileError::Unreadable(why) => write!(f, "the credentials file is not usable: {why}"),
            FileError::Busy => f.write_str("another process is renewing the sign-in"),
            FileError::Io(e) => write!(f, "the credentials file could not be used: {e}"),
        }
    }
}

impl std::error::Error for FileError {}

fn io_error(e: &io::Error) -> FileError {
    // The kind alone: an OS message can repeat the path.
    FileError::Io(e.kind().to_string())
}

/// Where the credentials file is kept unless a binding or `--credentials`
/// names another: `core-memory/chatgpt.json` in the user's config folder.
/// `None` if the environment names no such folder.
pub fn default_path() -> Option<PathBuf> {
    let var = |name| {
        std::env::var_os(name)
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    };
    let base = if cfg!(windows) {
        var("APPDATA")
    } else {
        var("XDG_CONFIG_HOME")
            .filter(|p| p.is_absolute())
            .or_else(|| var("HOME").map(|home| home.join(".config")))
    }?;
    Some(base.join("core-memory").join("chatgpt.json"))
}

/// Where this machine's host ID is kept: beside the credentials file.
pub fn host_id_path(credentials: &Path) -> PathBuf {
    credentials.with_file_name("chatgpt-host-id")
}

/// This machine's host ID, if one was made.
pub fn host_id(credentials: &Path) -> Result<Option<String>, FileError> {
    match fs::read_to_string(host_id_path(credentials)) {
        Ok(text) => {
            let id = text.trim();
            if valid_host_id(id) {
                Ok(Some(id.to_string()))
            } else {
                Err(FileError::Unreadable("the host ID file is damaged".into()))
            }
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(io_error(&e)),
    }
}

/// This machine's host ID, made and saved first if there is none. It is
/// made once per host and kept for good: signing in again reuses it.
pub fn ensure_host_id(credentials: &Path) -> Result<String, FileError> {
    if let Some(id) = host_id(credentials)? {
        return Ok(id);
    }
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(|e| FileError::Io(e.to_string()))?;
    let id = format!(
        "urn:uuid:{}",
        uuid::Builder::from_random_bytes(bytes).into_uuid()
    );
    write_private(&host_id_path(credentials), format!("{id}\n").as_bytes())
        .map_err(|e| io_error(&e))?;
    Ok(id)
}

/// A UUID host ID in the form this crate writes. It is opaque: it says
/// nothing of the user.
fn valid_host_id(id: &str) -> bool {
    id.strip_prefix("urn:uuid:")
        .and_then(|u| uuid::Uuid::try_parse(u).ok())
        .is_some()
}

/// The credentials at `path`, or `None` if there is no file.
pub fn load(path: &Path) -> Result<Option<Credentials>, FileError> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(io_error(&e)),
    };
    check_private(path)?;
    parse(&bytes).map(Some)
}

/// Credentials from the bytes of a credentials file.
pub(crate) fn parse(bytes: &[u8]) -> Result<Credentials, FileError> {
    // serde's message can quote what it read, which may be a token.
    let credentials: Credentials = serde_json::from_slice(bytes).map_err(|e| {
        FileError::Unreadable(format!(
            "it is not a Core Memory credentials file ({:?} at line {})",
            e.classify(),
            e.line()
        ))
    })?;
    if credentials.client_id.trim().is_empty() || credentials.subject.trim().is_empty() {
        return Err(FileError::Unreadable(
            "it names no client or no account".into(),
        ));
    }
    if !valid_host_id(&credentials.ext_agent_host_id) {
        return Err(FileError::Unreadable("its host ID is damaged".into()));
    }
    Ok(credentials)
}

/// Saves `credentials` at `path`, whole and owner-only.
pub fn save(path: &Path, credentials: &Credentials) -> Result<(), FileError> {
    let mut text = serde_json::to_vec_pretty(credentials)
        .map_err(|_| FileError::Io("the credentials could not be written".into()))?;
    text.push(b'\n');
    write_private(path, &text).map_err(|e| io_error(&e))
}

/// Refuses a file other users can read.
#[cfg(unix)]
fn check_private(path: &Path) -> Result<(), FileError> {
    use std::os::unix::fs::PermissionsExt;
    let mode = fs::metadata(path)
        .map_err(|e| io_error(&e))?
        .permissions()
        .mode();
    if mode & 0o077 != 0 {
        return Err(FileError::Exposed);
    }
    Ok(())
}

/// Windows keeps a file in the user's profile private to them already.
#[cfg(not(unix))]
fn check_private(_path: &Path) -> Result<(), FileError> {
    Ok(())
}

/// Writes `bytes` to `path` whole: to a new owner-only file beside it,
/// then renamed over it. A reader sees the old file or the new one, never
/// part of either. The folder is made owner-only if it is made here.
fn write_private(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let dir = match path.parent() {
        Some(dir) if !dir.as_os_str().is_empty() => dir.to_path_buf(),
        _ => PathBuf::from("."),
    };
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
    builder.create(&dir)?;
    let mut suffix = [0u8; 8];
    getrandom::fill(&mut suffix).map_err(|e| io::Error::other(e.to_string()))?;
    let mut name = OsString::from(".");
    name.push(path.file_name().unwrap_or_default());
    name.push(format!(
        ".{}.tmp",
        suffix
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    ));
    let temporary = dir.join(name);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    let written = (|| {
        let mut file = options.open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();
    if written.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    written?;
    // So the rename itself survives a crash.
    #[cfg(unix)]
    if let Ok(folder) = File::open(&dir) {
        let _ = folder.sync_all();
    }
    Ok(())
}

/// An exclusive lock on one credentials file, held while its tokens are
/// renewed so two processes never spend the same rotating refresh token.
/// Released when dropped.
#[derive(Debug)]
pub(crate) struct Lock {
    _file: File,
}

/// Takes the lock on the credentials at `path`, waiting at most `wait`.
pub(crate) fn lock(path: &Path, wait: Duration) -> Result<Lock, FileError> {
    let mut name = path.as_os_str().to_owned();
    name.push(".lock");
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(false);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    let file = options
        .open(PathBuf::from(name))
        .map_err(|e| io_error(&e))?;
    let deadline = Instant::now() + wait;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(Lock { _file: file }),
            Err(fs::TryLockError::WouldBlock) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(fs::TryLockError::WouldBlock) => return Err(FileError::Busy),
            Err(fs::TryLockError::Error(e)) => return Err(io_error(&e)),
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn sample(host: &str) -> Credentials {
        Credentials {
            email: Some("user@example.com".into()),
            issuer: "https://auth.openai.com".into(),
            subject: "user-123".into(),
            client_id: "oaiapp_test".into(),
            ext_agent_host_id: host.into(),
            id_token: Some(Secret::new("id-token-secret")),
            access_token: Some(Secret::new("access-token-secret")),
            refresh_token: Some(Secret::new("refresh-token-secret")),
            token_type: "Bearer".into(),
            expires_in: 3600,
            earliest_refresh_at: None,
            scopes: vec![
                "openid".into(),
                "offline_access".into(),
                crate::PLAN_SCOPE.into(),
            ],
            saved_at: now_rfc3339(),
            background_use: Some(BackgroundUse {
                agreed_at: now_rfc3339(),
                host_id: host.into(),
                statement: CONSENT_REVISION,
            }),
        }
    }

    #[test]
    fn a_saved_file_is_owner_only_and_reads_back_whole() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested").join("chatgpt.json");
        let host = ensure_host_id(&path).unwrap();
        assert!(host.starts_with("urn:uuid:"));
        // Made once, then kept.
        assert_eq!(ensure_host_id(&path).unwrap(), host);
        let credentials = sample(&host);
        save(&path, &credentials).unwrap();
        assert_eq!(load(&path).unwrap(), Some(credentials));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = |p: &Path| fs::metadata(p).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode(&path), 0o600);
            assert_eq!(mode(path.parent().unwrap()), 0o700);
        }
        // No temporary file is left behind.
        let names: Vec<_> = fs::read_dir(path.parent().unwrap())
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .collect();
        assert!(names.iter().all(|n| !n.ends_with(".tmp")), "{names:?}");
    }

    #[cfg(unix)]
    #[test]
    fn a_file_other_users_can_read_is_refused() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let host = ensure_host_id(&path).unwrap();
        save(&path, &sample(&host)).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(load(&path).unwrap_err(), FileError::Exposed);
    }

    #[test]
    fn no_file_is_no_sign_in_and_a_damaged_one_is_not_quoted() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        assert_eq!(load(&path).unwrap(), None);
        write_private(&path, b"{\"access_token\": \"leaked-secret\"").unwrap();
        let err = load(&path).unwrap_err().to_string();
        assert!(!err.contains("leaked-secret"), "{err}");
        assert!(!err.contains(&*dir.path().to_string_lossy()), "{err}");
    }

    #[test]
    fn tokens_never_show_in_debug_output() {
        let shown = format!(
            "{:?}",
            sample("urn:uuid:00000000-0000-4000-8000-000000000000")
        );
        for secret in [
            "id-token-secret",
            "access-token-secret",
            "refresh-token-secret",
        ] {
            assert!(!shown.contains(secret), "{shown}");
        }
    }

    #[test]
    fn a_token_is_renewed_near_its_expiry_and_not_before_it_may_be() {
        let mut c = sample("urn:uuid:00000000-0000-4000-8000-000000000000");
        c.saved_at = "2026-11-20T12:00:00Z".into();
        c.expires_in = 3600;
        let at = |s: &str| OffsetDateTime::parse(s, &Rfc3339).unwrap();
        let margin = Duration::from_secs(300);
        assert!(!c.refresh_due(at("2026-11-20T12:30:00Z"), margin));
        assert!(c.refresh_due(at("2026-11-20T12:56:00Z"), margin));
        // The endpoint asked for no refresh before 12:58; the token still
        // has more than half a minute then.
        c.earliest_refresh_at = Some(serde_json::json!("2026-11-20T12:58:00Z"));
        assert!(!c.refresh_due(at("2026-11-20T12:56:00Z"), margin));
        assert!(c.refresh_due(at("2026-11-20T12:58:00Z"), margin));
        // Never later than half a minute before it expires.
        c.earliest_refresh_at = Some(serde_json::json!(
            at("2026-11-20T14:00:00Z").unix_timestamp()
        ));
        assert!(c.refresh_due(at("2026-11-20T12:59:30Z"), margin));
        // A file that does not say when its token expires is renewed.
        c.saved_at = "not a time".into();
        assert!(c.refresh_due(at("2026-11-20T12:00:00Z"), margin));
    }

    #[test]
    fn consent_holds_only_on_the_host_it_was_given_on() {
        let here = "urn:uuid:00000000-0000-4000-8000-000000000001";
        let there = "urn:uuid:00000000-0000-4000-8000-000000000002";
        let mut c = sample(here);
        assert!(c.background_use_agreed(here));
        assert!(!c.background_use_agreed(there));
        c.background_use = None;
        assert!(!c.background_use_agreed(here));
        let mut old = sample(here);
        old.background_use.as_mut().unwrap().statement = 0;
        assert!(!old.background_use_agreed(here));
    }

    #[test]
    fn a_held_lock_makes_another_wait_then_give_up() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("chatgpt.json");
        let held = lock(&path, Duration::from_secs(1)).unwrap();
        assert_eq!(
            lock(&path, Duration::from_millis(60)).unwrap_err(),
            FileError::Busy
        );
        drop(held);
        lock(&path, Duration::from_millis(60)).unwrap();
    }
}

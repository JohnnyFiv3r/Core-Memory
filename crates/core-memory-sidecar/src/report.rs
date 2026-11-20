//! What the program prints: its results, its error messages and its log
//! lines.
//!
//! An error or a log line may not repeat the database URL, which can hold
//! a password. Each message is written without it. This is where every
//! message passes on its way out, so that one which repeats a URL after
//! all is withheld. The usual cause is a URL written in the config file
//! where another setting belongs, which that setting's own error then
//! quotes.
//!
//! Nothing here panics when what it writes to has gone away, as `println!`
//! and `eprintln!` do. A reader of the log that has gone is no reason to
//! stop, and one of the results is a failure the command reports.

use std::borrow::Cow;
use std::io::{self, Write};

use core_memory_core::{EngineError, StartupError, StoreError};
use core_memory_store_postgres::CheckoutError;

/// What is printed in place of a message that would repeat a database URL.
pub const WITHHELD: &str = "this message is withheld because it would repeat a database URL. \
     Look in the config file for a URL where another setting belongs";

/// Whether `text` holds what reads as a PostgreSQL URL or connection string.
fn repeats_a_database_url(text: &str) -> bool {
    let lower = text.to_ascii_lowercase();
    if ["postgres://", "postgresql://"]
        .iter()
        .any(|mark| lower.contains(mark))
    {
        return true;
    }
    // A connection string may have spaces around the `=` of each setting.
    lower
        .match_indices("password")
        .any(|(at, word)| lower[at + word.len()..].trim_start().starts_with('='))
}

/// `message`, or [`WITHHELD`] in its place.
pub fn presentable(message: &str) -> Cow<'_, str> {
    if repeats_a_database_url(message) {
        Cow::Borrowed(WITHHELD)
    } else {
        Cow::Borrowed(message)
    }
}

/// Writes one line to standard error, if anything still reads it.
fn to_stderr(line: &str) {
    let _ = writeln!(io::stderr().lock(), "{line}");
}

/// Writes one JSON log line to standard error.
pub fn log(line: serde_json::Value) {
    let text = line.to_string();
    if repeats_a_database_url(&text) {
        to_stderr(&serde_json::json!({"withheld": WITHHELD}).to_string());
    } else {
        to_stderr(&text);
    }
}

/// Says on standard error why the command stopped.
pub fn stopped(stop: &Stop) {
    to_stderr(&format!("core-memory: {}", presentable(stop.message())));
}

/// Writes one line of a command's result to standard output. Fails if it
/// cannot be written, as when what read it has gone away.
pub fn print(line: impl std::fmt::Display) -> Result<(), Stop> {
    writeln!(io::stdout().lock(), "{line}").map_err(not_written)
}

/// What a command reports when its result could not be written.
pub fn not_written(e: io::Error) -> Stop {
    Stop::Failed(format!("stdout: {e}"))
}

/// Why a command stopped before it was done.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Stop {
    /// The configuration, the input, or a failure that would happen again.
    Failed(String),
    /// The store could not be reached or could not take the work now. The
    /// same command will work later.
    StoreBusy(String),
}

impl Stop {
    pub fn message(&self) -> &str {
        match self {
            Stop::Failed(message) | Stop::StoreBusy(message) => message,
        }
    }
}

impl std::fmt::Display for Stop {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.message())
    }
}

impl From<String> for Stop {
    fn from(message: String) -> Self {
        Stop::Failed(message)
    }
}

impl From<&str> for Stop {
    fn from(message: &str) -> Self {
        Stop::Failed(message.to_string())
    }
}

impl From<StoreError> for Stop {
    fn from(e: StoreError) -> Self {
        match e {
            StoreError::Unavailable(_) => Stop::StoreBusy(e.to_string()),
            other => Stop::Failed(other.to_string()),
        }
    }
}

impl From<CheckoutError> for Stop {
    fn from(e: CheckoutError) -> Self {
        match e {
            CheckoutError::Busy => Stop::StoreBusy(e.to_string()),
            CheckoutError::Store(e) => e.into(),
        }
    }
}

impl From<StartupError> for Stop {
    fn from(e: StartupError) -> Self {
        match e {
            StartupError::Store(e) => e.into(),
            other => Stop::Failed(other.to_string()),
        }
    }
}

impl From<EngineError> for Stop {
    fn from(e: EngineError) -> Self {
        match e {
            EngineError::Store(e) => e.into(),
            other => Stop::Failed(other.to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_message_that_repeats_a_database_url_is_withheld() {
        for leaking in [
            "profile `postgresql://engine:hunter2@db.internal/memory`: id is not valid",
            "type `POSTGRES://u:p@h/db` is not in the vocabulary",
            "ca_file host=db user=engine password=hunter2: No such file",
            "chunker `host=db user=engine password = hunter2 dbname=memory` is unknown",
            "type `host=db PASSWORD\t=\thunter2` is not in the vocabulary",
        ] {
            assert_eq!(presentable(leaking), WITHHELD, "{leaking}");
        }
        for plain in [
            "scope `acme` is not enrolled",
            "password authentication failed for user \"engine\"",
            "the password is not printed, and neither is a password's hash",
            "cannot listen on 127.0.0.1:7600: address in use",
        ] {
            assert_eq!(presentable(plain), plain);
        }
    }

    #[test]
    fn only_an_unavailable_store_is_worth_running_the_command_again() {
        assert!(matches!(
            Stop::from(StoreError::Unavailable("cannot connect".into())),
            Stop::StoreBusy(_)
        ));
        for failed in [
            StoreError::SchemaMismatch("run migrate".into()),
            StoreError::AccessRefused("password authentication failed".into()),
            StoreError::Corrupt("x".into()),
            StoreError::Rejected("x".into()),
            StoreError::Backend("x".into()),
        ] {
            assert!(matches!(Stop::from(failed), Stop::Failed(_)));
        }
    }
}

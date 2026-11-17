use std::fmt;

/// A configuration problem found at startup. The engine refuses to start
/// rather than run with a partly valid configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigError {
    message: String,
}

impl ConfigError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ConfigError {}

/// A store failure.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    /// The store failed while working.
    #[error("store backend error: {0}")]
    Backend(String),
    /// The store cannot answer right now: it is unreachable, or a statement
    /// timed out or lost a lock wait. The same request may succeed later.
    #[error("the store is unavailable: {0}")]
    Unavailable(String),
    /// The store answered and would not open a session for this engine: it
    /// refused the login, has no such database, or failed the TLS check.
    /// Nothing was read or written. It repeats until the configuration or
    /// the server is changed, so whoever runs the engine has to act. A
    /// caller's request is not at fault and can be offered again.
    #[error("the store refused the connection: {0}")]
    AccessRefused(String),
    #[error("store data is inconsistent: {0}")]
    Corrupt(String),
    /// The store's schema is not the version this build reads and writes.
    /// Nothing was written. Migrating the store, or replacing this build,
    /// resolves it.
    #[error("{0}")]
    SchemaMismatch(String),
    /// The store is working and will not do what was asked, because a
    /// governance rule forbids the change.
    #[error("{0}")]
    Rejected(String),
}

/// A failure that stops admission without a typed outcome. Typed outcomes
/// (refused, conflict, busy) are not errors.
#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error("cannot serialize: {0}")]
    Serialize(#[from] serde_json::Error),
}

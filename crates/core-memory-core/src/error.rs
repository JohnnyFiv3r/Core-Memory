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

/// An operational store failure.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("store backend error: {0}")]
    Backend(String),
    #[error("store data is inconsistent: {0}")]
    Corrupt(String),
}

/// A failure that stops admission without a typed outcome. Typed outcomes
/// (refused, conflict, busy) are not errors.
#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error("cannot serialize a record: {0}")]
    Serialize(#[from] serde_json::Error),
}

//! Turning database errors into [`StoreError`]s.
//!
//! The split that matters to callers is whether the same request may work
//! later. A transient failure is `Unavailable`, and a caller retries it. A
//! failure that will repeat is `Backend` or `Corrupt`, and retrying it
//! would only hide it.

use core_memory_core::StoreError;
use postgres::error::SqlState;

/// The SQLSTATE `core_memory.begin_write` raises for an engine built for
/// another schema version.
const SCHEMA_MISMATCH: &str = "CM001";

/// Starts the message of every error for a database that takes no writes,
/// so the store can tell that its connection is worth replacing.
pub(crate) const READ_ONLY: &str = "the database is read-only";

/// The database's own message for an error, with its SQLSTATE. The
/// driver's `Display` gives only the kind of error.
pub(crate) fn describe(e: &postgres::Error) -> String {
    match e.as_db_error() {
        Some(db) => format!("{} (SQLSTATE {})", db.message(), db.code().code()),
        None => source_chain(e),
    }
}

/// An error with the causes behind it, which the driver's own message
/// leaves out.
pub(crate) fn source_chain(e: &dyn std::error::Error) -> String {
    let mut out = e.to_string();
    let mut source = e.source();
    while let Some(cause) = source {
        let text = cause.to_string();
        if !out.contains(&text) {
            out.push_str(": ");
            out.push_str(&text);
        }
        source = cause.source();
    }
    out
}

/// The input and output errors among an error's causes.
fn io_causes(e: &postgres::Error) -> impl Iterator<Item = &std::io::Error> {
    let mut source = std::error::Error::source(e);
    std::iter::from_fn(move || {
        while let Some(cause) = source {
            source = cause.source();
            if let Some(io) = cause.downcast_ref::<std::io::Error>() {
                return Some(io);
            }
        }
        None
    })
}

/// Whether TLS itself refused the connection: a certificate that does not
/// verify, a name it was not issued for, a protocol the two sides do not
/// share. The handshake reports these as an input error that wraps them.
fn tls_refused(e: &postgres::Error) -> bool {
    io_causes(e).any(|io| {
        io.get_ref()
            .is_some_and(|inner| inner.is::<rustls::Error>())
    })
}

/// Whether the same request may work later without anyone changing
/// anything.
pub(crate) fn is_transient(e: &postgres::Error) -> bool {
    // The server could not or would not do it now.
    let transient = [
        SqlState::LOCK_NOT_AVAILABLE,
        SqlState::QUERY_CANCELED,
        SqlState::T_R_SERIALIZATION_FAILURE,
        SqlState::T_R_DEADLOCK_DETECTED,
        SqlState::TOO_MANY_CONNECTIONS,
        SqlState::CANNOT_CONNECT_NOW,
        SqlState::ADMIN_SHUTDOWN,
        SqlState::CRASH_SHUTDOWN,
        SqlState::IDLE_IN_TRANSACTION_SESSION_TIMEOUT,
    ];
    match e.code() {
        // The server's own answer. A wrong password, a database that does
        // not exist and a statement it rejects all repeat.
        Some(code) => transient.contains(code),
        // No SQLSTATE: the error is the client's. A closed or failing
        // connection is worth a retry on a new one, unless what failed is
        // the check of the server's certificate. Anything else is a value
        // the client could not send or read, or a server without TLS,
        // which will repeat.
        None => (e.is_closed() || io_causes(e).next().is_some()) && !tls_refused(e),
    }
}

/// Classifies an error from a statement the store ran.
pub(crate) fn backend(e: postgres::Error) -> StoreError {
    match e.code() {
        Some(code) if code.code() == SCHEMA_MISMATCH => StoreError::SchemaMismatch(
            e.as_db_error()
                .map_or_else(|| describe(&e), |db| db.message().to_string()),
        ),
        // A standby, or a primary that was demoted while this connection
        // was open. Another server may take the write.
        Some(code) if *code == SqlState::READ_ONLY_SQL_TRANSACTION => {
            StoreError::Unavailable(format!("{READ_ONLY}: {}", describe(&e)))
        }
        _ if is_transient(&e) => StoreError::Unavailable(describe(&e)),
        _ => StoreError::Backend(describe(&e)),
    }
}

/// An error a governance function raised on purpose is a refusal, not a
/// failure of the store.
pub(crate) fn governed(e: postgres::Error) -> StoreError {
    let refused = [
        SqlState::CHECK_VIOLATION,
        SqlState::FOREIGN_KEY_VIOLATION,
        SqlState::NO_DATA_FOUND,
        SqlState::INVALID_PARAMETER_VALUE,
        SqlState::INSUFFICIENT_PRIVILEGE,
        SqlState::FEATURE_NOT_SUPPORTED,
    ];
    match e.as_db_error() {
        Some(db) if refused.contains(db.code()) => StoreError::Rejected(db.message().to_string()),
        _ => backend(e),
    }
}

/// A stored value that does not read back as what the engine wrote.
pub(crate) fn corrupt(what: impl std::fmt::Display) -> StoreError {
    StoreError::Corrupt(what.to_string())
}

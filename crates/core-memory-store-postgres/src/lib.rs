//! The Core Memory engine's PostgreSQL store.
//!
//! PostgreSQL is the engine's only durable store and its system of record.
//! This crate owns the `core_memory` schema: the migrations that create it
//! ([`migrate`]), the [`core_memory_core::store::Store`] implementation
//! ([`PostgresStore`]), and the governance functions a host calls to
//! enroll, pause and revoke scopes and to erase messages.
//!
//! `docs/architecture.md` describes the schema and the reasons for it.

mod connect;
mod error;
mod judge;
pub mod migrate;
mod pool;
mod store;
#[cfg(feature = "testing")]
pub mod testing;

pub use connect::{ConnectConfig, MAX_CONNECT_TIMEOUT, MAX_STATEMENT_TIMEOUT};
pub use migrate::{MigrationReport, SCHEMA_VERSION};
pub use pool::{CheckoutError, PoolStore, Pooled, PostgresPool};
pub use store::{Enrollment, EraseOutcome, PostgresStore, QueuedReads, Reread, ScopeSummary};

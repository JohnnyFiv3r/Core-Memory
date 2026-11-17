//! Core Memory turns any stream of source events into durable, auditable
//! memory records: exact chunks with deterministic facets, stored in
//! PostgreSQL in the same transaction that admits them.
//!
//! This crate re-exports the engine's parts. Most applications need
//! [`engine::Engine`] and a [`store_postgres::PostgresStore`].

pub use core_memory_chunker as chunker;
pub use core_memory_contract as contract;
pub use core_memory_core as engine;

#[cfg(feature = "jsonl")]
pub use core_memory_source_jsonl as source_jsonl;
#[cfg(feature = "postgres")]
pub use core_memory_store_postgres as store_postgres;

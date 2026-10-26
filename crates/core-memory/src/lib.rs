//! Core Memory turns any stream of source events into durable, auditable
//! memory records: exact chunks with deterministic facets, honest type
//! outcomes and versioned records for any sink.
//!
//! This crate re-exports the engine's parts. Most applications need
//! [`engine::Engine`], a store such as [`store_sqlite::SqliteStore`] and a
//! sink.

pub use core_memory_chunker as chunker;
pub use core_memory_contract as contract;
pub use core_memory_core as engine;

#[cfg(feature = "jsonl")]
pub use core_memory_sink_jsonl as sink_jsonl;
#[cfg(feature = "jsonl")]
pub use core_memory_source_jsonl as source_jsonl;
#[cfg(feature = "sqlite")]
pub use core_memory_store_sqlite as store_sqlite;

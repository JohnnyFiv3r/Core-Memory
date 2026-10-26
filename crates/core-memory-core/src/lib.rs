//! The Core Memory engine pipeline.
//!
//! [`Engine::admit`] takes one SourceEvent, checks its identity, chunks it,
//! computes facets, classifies each chunk and commits the ledger entry and
//! its records to the outbox in one transaction. [`sink::deliver_all`]
//! moves records from the outbox to a sink.
//!
//! This crate imports no store, source, sink or model-provider crate. Those
//! plug in through [`store::OperationalStore`] and [`sink::Sink`].

pub mod classify;
pub mod clock;
pub mod content;
pub mod engine;
pub mod error;
pub mod facets;
mod ident;
pub mod profile;
pub mod sink;
pub mod store;
pub mod vocab;

pub use clock::{Clock, ManualClock, SystemClock};
pub use engine::{Engine, EngineConfig, Limits, ScopeGrants, StartupError};
pub use error::{ConfigError, EngineError, StoreError};
pub use ident::is_identifier;
pub use profile::{ProfileRegistry, SourceProfile};
pub use sink::{
    DeliveryReport, Sink, SinkCaps, SinkError, WriteOutcome, deliver_all, deliver_once,
};
pub use store::{MemoryStore, OperationalStore};
pub use vocab::{LoadedVocabulary, TypeDef, Vocabulary};

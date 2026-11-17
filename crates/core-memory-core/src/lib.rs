//! The Core Memory engine pipeline.
//!
//! [`Engine::admit`] takes one SourceEvent, checks its identity, chunks it,
//! computes facets and commits the event, its chunks and its beads to the
//! store in one transaction. What that transaction commits is the record:
//! there is no outbox and no delivery step.
//!
//! [`Engine::read_once`] has a model read queued chunks: each chunk's type,
//! and where its entities, actions and outcomes are. Every model call is
//! permitted and receipted by the store before anything is sent.
//!
//! This crate imports no database or model-provider crate. Those plug in
//! through [`store::Store`] and [`model::ModelTransport`].

pub mod clock;
pub mod content;
pub mod engine;
pub mod error;
pub mod facets;
mod ident;
pub mod model;
pub mod profile;
pub mod read;
pub mod reader;
pub mod store;
pub mod vocab;

pub use clock::{Clock, ManualClock, SystemClock};
pub use engine::{Engine, EngineConfig, Limits, ScopeGrants, StartupError};
pub use error::{ConfigError, EngineError, StoreError};
pub use ident::is_identifier;
pub use model::{BindingConfig, ModelTransport};
pub use profile::{ProfileRegistry, SourceProfile};
pub use reader::{CompiledReader, ReadReport, ReadRound};
pub use store::Store;
pub use vocab::{LoadedVocabulary, TypeDef, Vocabulary};

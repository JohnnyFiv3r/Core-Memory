//! The Core Memory engine pipeline.
//!
//! [`Engine::admit`] takes one SourceEvent, checks its identity, chunks it,
//! computes facets and commits the event, its chunks and its beads to the
//! store in one transaction. What that transaction commits is the record:
//! there is no outbox and no delivery step.
//!
//! [`Engine::read_once`] has a model read queued chunks: each chunk's type,
//! and where its entities, actions and outcomes are.
//! [`Engine::read_tool_once`] has a model read a queued tool call: what it
//! did and what came of it. [`Engine::judge_once`] has a judge model read
//! new beads against the beads near them and say how they relate. Every
//! model call is permitted and receipted by the store before anything is
//! sent. [`Engine::embed_once`] has a model give queued chunks a vector,
//! on the same terms, and [`Engine::recall`] answers a query by its words
//! and, with those vectors, by its meaning.
//!
//! This crate imports no database or model-provider crate. Those plug in
//! through [`store::Store`], [`model::ModelTransport`] and
//! [`model::EmbedTransport`].

pub mod clock;
pub mod compress;
pub mod content;
pub mod embed;
pub mod engine;
pub mod error;
pub mod facets;
mod ident;
pub mod judge;
pub mod judging;
pub mod model;
pub mod profile;
pub mod read;
pub mod reader;
pub mod recall;
pub mod store;
pub mod tool;
pub mod tool_reader;
pub mod vocab;

pub use clock::{Clock, ManualClock, SystemClock};
pub use embed::{CompiledEmbedder, EmbedReport, EmbedRound, EmbedderConfig};
pub use engine::{Engine, EngineConfig, Limits, ScopeGrants, StartupError};
pub use error::{ConfigError, EngineError, StoreError};
pub use ident::is_identifier;
pub use judging::{CompiledJudge, JudgeReport, JudgeRound};
pub use model::{BindingConfig, EmbedTransport, ModelTransport};
pub use profile::{ProfileRegistry, SourceProfile};
pub use reader::{CompiledReader, ReadReport, ReadRound};
pub use recall::{MeaningLeg, QuestionVector, RecallQuery, RecallRefusal, Search};
pub use store::Store;
pub use tool_reader::CompiledToolReader;
pub use vocab::{LoadedVocabulary, TypeDef, Vocabulary};

//! The embedder: gives queued chunks a vector, one batch at a time, and
//! gives a recall query the vector to compare them with.
//!
//! A vector is an index of a chunk's text, as the word index is. It is
//! never evidence and never a relationship: recall uses it to find chunks,
//! and what it returns is the chunks' stored text. A vector is derived
//! from text, so it is erased with it.
//!
//! A call goes through the same steps as a read:
//!
//! 1. **Permit.** The scope must be active and must have granted the
//!    binding's destination, and the budget must allow the call. If so the
//!    store writes the call's receipt and reads the text, together.
//! 2. **Call** the model through the injected transport: one request, with
//!    no retry of its own.
//! 3. **Check** the reply: a vector for each chunk, all of one length, of
//!    numbers.
//! 4. **Finish.** The store records how the call ended, stores the vectors
//!    and settles the work, together.
//!
//! A call that fails is made again by the queue: after a wait, a counted
//! number of times, each with a receipt of its own. Nothing here logs or
//! returns chunk text.

use core_memory_contract::hash::canonical_json_digest;
use core_memory_contract::{ChunkId, ReaderRef, Sha256Digest};
use serde::Deserialize;
use serde_json::json;

use crate::engine::Engine;
use crate::error::{ConfigError, EngineError};
use crate::ident::is_identifier;
use crate::model::{BindingConfig, EmbedRequest, EmbedTransport, TransportError};
use crate::reader::{DEFER_MS, LEASE_MARGIN_MS, asked_again, failure, refusal};
use crate::store::{
    CallResult, EmbedBatch, EmbedFinish, Permit, PermitRequest, PermittedChunk, Purpose, Sending,
    Store, VectorWrite, WorkItem, WorkNext, WorkUpdate, tokens_in,
};

/// What a chunk's text is made into before it is sent: the prefix, then
/// the text from its start, as far as `max_input_bytes`. Changing that is
/// a new revision, and so new vectors.
pub const EMBED_INPUT_REV: u32 = 1;
/// The longest vector the store's vector type compares.
pub const MAX_DIMENSIONS: u32 = 16_000;
/// Most chunks one call may carry.
pub const MAX_BATCH: u32 = 64;
/// Longest prefix, in bytes.
const MAX_PREFIX_BYTES: usize = 256;

const EMBEDDER_REV_DOMAIN: &str = "core-memory/embedder-rev/v1";

/// Settings for the embedder, from the `[embedder]` config section.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EmbedderConfig {
    /// The binding that embeds. Configuring it turns embedding on.
    pub binding: String,
    /// The length of vector to ask the model for, where it can be asked.
    /// Without it the model's own length is taken.
    #[serde(default)]
    pub dimensions: Option<u32>,
    /// Most bytes of one chunk that are sent. A longer chunk is embedded
    /// by its start, and its vector says how much of it that was.
    #[serde(default = "default_max_input_bytes")]
    pub max_input_bytes: u32,
    /// Put before each chunk, for a model that is told what it embeds by
    /// a prefix such as `search_document: `.
    #[serde(default)]
    pub document_prefix: String,
    /// Put before a recall query, for the same.
    #[serde(default)]
    pub query_prefix: String,
    /// Chunks per call.
    #[serde(default = "default_batch_size")]
    pub batch_size: u32,
    /// Times a chunk is handed to the model before it is set aside as
    /// failed, for an operator to look at.
    #[serde(default = "default_max_attempts")]
    pub max_attempts: u32,
    /// How long a chunk waits after a failed call.
    #[serde(default = "default_retry_backoff_ms")]
    pub retry_backoff_ms: u64,
    /// How long a claimed batch is reserved for one worker.
    #[serde(default = "default_lease_ms")]
    pub lease_ms: u64,
    /// How long one try at embedding a recall query may take. A recall
    /// waits for its query's vector, so this is far shorter than the
    /// binding's own timeout.
    #[serde(default = "default_question_timeout_ms")]
    pub question_timeout_ms: u64,
    /// Times a recall query is offered to the model before the answer is
    /// made from the words alone.
    #[serde(default = "default_question_attempts")]
    pub question_attempts: u32,
    /// The farthest a chunk's vector may be from a query's and still
    /// match, as a cosine distance from 0 to 2. Without it the nearest
    /// chunks match however far they are, and a caller reads each match's
    /// distance.
    #[serde(default)]
    pub max_distance: Option<f32>,
}

fn default_max_input_bytes() -> u32 {
    8_000
}
fn default_batch_size() -> u32 {
    32
}
fn default_max_attempts() -> u32 {
    5
}
fn default_retry_backoff_ms() -> u64 {
    30_000
}
fn default_lease_ms() -> u64 {
    300_000
}
fn default_question_timeout_ms() -> u64 {
    2_000
}
fn default_question_attempts() -> u32 {
    3
}

/// The embedder's revision: a hash of everything that decides what vector
/// a text gets. Vectors of two revisions are never compared. Timeouts,
/// budgets, batch sizes and `max_input_bytes` are left out: they change how
/// much is embedded and when, not what a text's vector is.
pub fn embedder_rev(
    binding: &BindingConfig,
    settings: &EmbedderConfig,
) -> Result<Sha256Digest, serde_json::Error> {
    canonical_json_digest(
        EMBEDDER_REV_DOMAIN,
        &json!({
            "provider": binding.provider,
            "endpoint": binding.endpoint,
            "model": binding.model,
            "dimensions": settings.dimensions,
            "document_prefix": settings.document_prefix,
            "query_prefix": settings.query_prefix,
            "input_rev": EMBED_INPUT_REV,
        }),
    )
}

/// An embedder ready to run: its settings and its binding, validated.
#[derive(Debug, Clone)]
pub struct CompiledEmbedder {
    pub settings: EmbedderConfig,
    pub binding: BindingConfig,
    /// Where calls send text. What a scope consents to.
    pub destination: String,
    /// The binding, the model and, as `binding_rev`, the embedder's
    /// revision. Receipts and vectors carry it.
    pub embedder_ref: ReaderRef,
}

impl CompiledEmbedder {
    pub(crate) fn compile(
        settings: EmbedderConfig,
        bindings: &[BindingConfig],
    ) -> Result<Self, ConfigError> {
        let Some(binding) = bindings.iter().find(|b| b.id == settings.binding) else {
            // What is not an identifier is not a binding's id, and may be
            // what was meant for another setting. It is not repeated.
            return Err(ConfigError::new(if is_identifier(&settings.binding) {
                format!(
                    "embedder.binding `{}` is not a configured [[binding]]",
                    settings.binding
                )
            } else {
                "embedder.binding is not the id of a configured [[binding]]".to_string()
            }));
        };
        if settings
            .dimensions
            .is_some_and(|d| !(1..=MAX_DIMENSIONS).contains(&d))
        {
            return Err(ConfigError::new(format!(
                "embedder.dimensions must be 1 to {MAX_DIMENSIONS}"
            )));
        }
        if !(16..=1_000_000).contains(&settings.max_input_bytes) {
            return Err(ConfigError::new(
                "embedder.max_input_bytes must be 16 to 1000000",
            ));
        }
        for (name, prefix) in [
            ("document_prefix", &settings.document_prefix),
            ("query_prefix", &settings.query_prefix),
        ] {
            if prefix.len() > MAX_PREFIX_BYTES || prefix.chars().any(char::is_control) {
                return Err(ConfigError::new(format!(
                    "embedder.{name} must be at most {MAX_PREFIX_BYTES} bytes, with no control \
                     characters"
                )));
            }
        }
        if !(1..=MAX_BATCH).contains(&settings.batch_size) {
            return Err(ConfigError::new(format!(
                "embedder.batch_size must be 1 to {MAX_BATCH}"
            )));
        }
        if settings.max_attempts == 0 {
            return Err(ConfigError::new("embedder.max_attempts must be 1 or more"));
        }
        let needed = binding.timeout_ms.saturating_add(LEASE_MARGIN_MS);
        if settings.lease_ms < needed {
            return Err(ConfigError::new(format!(
                "embedder.lease_ms must be at least {needed}: one call of the binding's \
                 timeout, and a margin"
            )));
        }
        if !(1..=60_000).contains(&settings.question_timeout_ms) {
            return Err(ConfigError::new(
                "embedder.question_timeout_ms must be 1 to 60000",
            ));
        }
        if !(1..=5).contains(&settings.question_attempts) {
            return Err(ConfigError::new(
                "embedder.question_attempts must be 1 to 5",
            ));
        }
        if settings
            .max_distance
            .is_some_and(|d| !(d > 0.0 && d <= 2.0))
        {
            return Err(ConfigError::new(
                "embedder.max_distance must be more than 0 and at most 2",
            ));
        }
        let rev = embedder_rev(binding, &settings)
            .map_err(|e| ConfigError::new(format!("cannot hash embedder: {e}")))?;
        Ok(Self {
            destination: binding.destination()?,
            embedder_ref: ReaderRef {
                binding_id: binding.id.clone(),
                binding_rev: rev,
                model: binding.model.clone(),
                prompt_rev: EMBED_INPUT_REV,
            },
            binding: binding.clone(),
            settings,
        })
    }

    /// The start of `text` that is embedded: all of it, or as much as
    /// `max_input_bytes` allows, ending between two characters.
    pub fn cut<'a>(&self, text: &'a str) -> &'a str {
        let mut end = text.len().min(self.settings.max_input_bytes as usize);
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        &text[..end]
    }

    /// Whether `vectors` is what a call for `count` texts must return: a
    /// vector for each, all as long as the settings say or else as long as
    /// each other, of numbers, none of them all zeros. A vector of zeros
    /// points nowhere and is as far from every query as from none.
    pub(crate) fn sound(&self, vectors: &[Vec<f32>], count: usize) -> bool {
        let Some(first) = vectors.first() else {
            return false;
        };
        let length = match self.settings.dimensions {
            Some(asked) => asked as usize,
            None => first.len(),
        };
        vectors.len() == count
            && (1..=MAX_DIMENSIONS as usize).contains(&length)
            && vectors.iter().all(|vector| {
                vector.len() == length
                    && vector.iter().all(|x| x.is_finite())
                    && vector.iter().any(|x| *x != 0.0)
            })
    }

    /// The vector of a recall query, or `None` if the model gave none in
    /// `question_attempts` tries. Only a failure that may pass is tried
    /// again: the provider did not process the request and named no wait,
    /// or the reply did not come. A provider that says when to come back
    /// is not waited for, and one that turns the binding down would do so
    /// again.
    pub fn question(&self, ask: &dyn EmbedTransport, query: &str) -> Option<Vec<f32>> {
        let request = EmbedRequest {
            inputs: vec![format!("{}{}", self.settings.query_prefix, self.cut(query))],
            dimensions: self.settings.dimensions,
        };
        for _ in 0..self.settings.question_attempts {
            match ask.embed(&self.binding, &request) {
                Ok(mut response) if self.sound(&response.vectors, 1) => {
                    return response.vectors.pop();
                }
                // A reply of the wrong shape would be given again.
                Ok(_) => return None,
                Err(
                    TransportError::Transient {
                        retry_after_ms: None,
                        ..
                    }
                    | TransportError::InDoubt { .. },
                ) => {}
                Err(_) => return None,
            }
        }
        None
    }
}

/// What one round of embedding did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EmbedRound {
    /// No embedder is configured, or nothing is due.
    Idle,
    Handled(EmbedReport),
}

/// One batch, as counts and codes. It holds no text and no vector.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct EmbedReport {
    pub scope: String,
    /// Chunks in the batch.
    pub chunks: u32,
    /// Vectors stored.
    pub stored: u32,
    /// Vectors not stored, because their chunk was erased or their scope
    /// stopped being active while the model worked, or because the chunk
    /// already had one.
    pub dropped: u32,
    /// Model calls made: one, or none.
    pub calls: u32,
    pub input_tokens: u64,
    /// Why the batch got no vectors, if it got none.
    pub problem: Option<&'static str>,
}

impl Engine {
    /// The embedder, if one is configured.
    pub fn embedder(&self) -> Option<&CompiledEmbedder> {
        self.embedder.as_ref()
    }

    /// Embeds one batch of queued chunks, if any is due. Returns `Err`
    /// only when the store fails; everything a provider does wrong is
    /// recorded and reported in the round.
    pub fn embed_once(
        &self,
        store: &mut dyn Store,
        transport: &dyn EmbedTransport,
    ) -> Result<EmbedRound, EngineError> {
        let Some(embedder) = &self.embedder else {
            return Ok(EmbedRound::Idle);
        };
        let settings = &embedder.settings;
        let now = self.clock.now().truncated_to_micros();
        let lease_until = now.plus_millis(settings.lease_ms);
        let Some(batch) =
            store.claim_embeddings(settings.batch_size, settings.max_attempts, now, lease_until)?
        else {
            return Ok(EmbedRound::Idle);
        };
        let mut report = EmbedReport {
            scope: batch.scope.clone(),
            chunks: batch.chunks.len() as u32,
            ..EmbedReport::default()
        };
        let ids: Vec<ChunkId> = batch.chunks.iter().map(|c| c.chunk_id).collect();
        let prefix = settings.document_prefix.len() as u64;
        let request = PermitRequest {
            scope: &batch.scope,
            chunk_ids: &ids,
            purpose: Purpose::Embed,
            destination: &embedder.destination,
            reader: &embedder.embedder_ref,
            attempt: 1,
            repairs: None,
            // What leaves is each chunk's start, as far as the embedder
            // takes. A reply is numbers, so nothing is reserved for it.
            sending: &|chunks| {
                let bytes: u64 = chunks
                    .iter()
                    .map(|chunk| embedder.cut(&chunk.text).len() as u64)
                    .sum();
                Some(Sending {
                    bytes,
                    reserved_tokens: tokens_in(bytes + prefix * chunks.len() as u64),
                })
            },
            budget: embedder.binding.budget,
            at: now,
        };
        // A permit the store could not give for a moment wrote nothing:
        // its transaction rolled back. Asking again writes one receipt.
        let permit = asked_again(|| store.permit(&request))?;
        let (call_id, chunks) = match permit {
            Permit::Granted { call_id, chunks } => (call_id, chunks),
            refused => {
                report.problem = Some(self.embed_later(store, &batch, &refused)?);
                return Ok(EmbedRound::Handled(report));
            }
        };
        let inputs: Vec<String> = chunks
            .iter()
            .map(|chunk| format!("{}{}", settings.document_prefix, embedder.cut(&chunk.text)))
            .collect();
        report.calls = 1;
        let finish = |store: &mut dyn Store,
                      result: CallResult,
                      input_tokens: Option<u64>,
                      vectors: &[VectorWrite],
                      work: &[WorkUpdate]| {
            let finish = EmbedFinish {
                call_id,
                scope: &batch.scope,
                result,
                input_tokens,
                vectors,
                embedder: &embedder.embedder_ref,
                work,
                at: self.clock.now().truncated_to_micros(),
            };
            asked_again(|| store.finish_embedding(&finish))
        };
        let called = transport.embed(
            &embedder.binding,
            &EmbedRequest {
                inputs,
                dimensions: settings.dimensions,
            },
        );
        match called {
            Ok(response) if embedder.sound(&response.vectors, chunks.len()) => {
                report.input_tokens = response.input_tokens;
                let vectors: Vec<VectorWrite> = chunks
                    .iter()
                    .zip(response.vectors)
                    .map(|(chunk, vector)| VectorWrite {
                        chunk_id: chunk.chunk_id,
                        input_bytes: embedder.cut(&chunk.text).len() as u64,
                        vector,
                    })
                    .collect();
                let finished = finish(
                    store,
                    CallResult::Accepted,
                    Some(response.input_tokens),
                    &vectors,
                    &[],
                )?;
                (report.stored, report.dropped) = (finished.stored, finished.dropped);
            }
            Ok(response) => {
                // Not a vector for each chunk, or not vectors. The same
                // request may be answered soundly later; it is not sent
                // again more often than the chunks' attempts allow.
                report.input_tokens = response.input_tokens;
                let work = self.embed_again(&batch, &chunks, false, "reply_invalid", None);
                finish(
                    store,
                    CallResult::Rejected,
                    Some(response.input_tokens),
                    &[],
                    &work,
                )?;
                report.problem = Some("reply_invalid");
            }
            Err(e) => {
                let failed = failure(&e, settings.retry_backoff_ms);
                let not_before = self
                    .clock
                    .now()
                    .truncated_to_micros()
                    .plus_millis(failed.wait_ms);
                let work: Vec<WorkUpdate> = if !failed.counts {
                    // Turned away before anything was read, for a reason
                    // that passes by itself: put off, its attempt given
                    // back.
                    chunks
                        .iter()
                        .map(|chunk| WorkUpdate {
                            item: WorkItem::Embedding(chunk.chunk_id),
                            next: WorkNext::Defer { not_before },
                            problem: failed.problem,
                        })
                        .collect()
                } else if failed.result == CallResult::Refused && chunks.len() == 1 {
                    // The model declined this one chunk, or it does not
                    // fit. The same request would get the same answer.
                    vec![WorkUpdate {
                        item: WorkItem::Embedding(chunks[0].chunk_id),
                        next: WorkNext::Failed,
                        problem: failed.problem,
                    }]
                } else {
                    self.embed_again(
                        &batch,
                        &chunks,
                        failed.alone,
                        failed.problem,
                        Some(failed.wait_ms),
                    )
                };
                finish(store, failed.result, None, &[], &work)?;
                report.problem = Some(failed.problem);
            }
        }
        Ok(EmbedRound::Handled(report))
    }

    /// Puts a batch back when its permit was refused. No call was made, so
    /// no attempt counts. Returns why the permit was refused.
    fn embed_later(
        &self,
        store: &mut dyn Store,
        batch: &EmbedBatch,
        refused: &Permit,
    ) -> Result<&'static str, EngineError> {
        let now = self.clock.now().truncated_to_micros();
        let Some((next, problem)) = refusal(refused, now) else {
            // Erasing the chunks removed their work too.
            return Ok("erased");
        };
        let work: Vec<WorkUpdate> = batch
            .chunks
            .iter()
            .map(|c| WorkUpdate {
                item: WorkItem::Embedding(c.chunk_id),
                next,
                problem,
            })
            .collect();
        store.update_work(&work)?;
        Ok(problem)
    }

    /// What to do with each sent chunk after a call that gave it no
    /// vector: try again after a wait, or set it aside once its attempts
    /// are spent.
    fn embed_again(
        &self,
        batch: &EmbedBatch,
        sent: &[PermittedChunk],
        alone: bool,
        problem: &'static str,
        wait_ms: Option<u64>,
    ) -> Vec<WorkUpdate> {
        let (max_attempts, backoff) = self.embedder.as_ref().map_or((1, DEFER_MS), |e| {
            (e.settings.max_attempts, e.settings.retry_backoff_ms)
        });
        let not_before = self
            .clock
            .now()
            .truncated_to_micros()
            .plus_millis(wait_ms.unwrap_or(backoff));
        sent.iter()
            .map(|chunk| {
                let attempts = batch
                    .chunks
                    .iter()
                    .find(|c| c.chunk_id == chunk.chunk_id)
                    .map_or(max_attempts, |c| c.attempts);
                WorkUpdate {
                    item: WorkItem::Embedding(chunk.chunk_id),
                    next: if attempts >= max_attempts {
                        WorkNext::Failed
                    } else {
                        WorkNext::Retry { not_before, alone }
                    },
                    problem,
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]

    use std::sync::atomic::{AtomicU32, Ordering};

    use super::*;
    use crate::model::{EmbedResponse, FnEmbedTransport};

    fn bindings() -> Vec<BindingConfig> {
        vec![
            toml::from_str(
                r#"
id = "embed"
provider = "openai"
endpoint = "https://api.openai.com/v1"
model = "an-embedding-model"
"#,
            )
            .unwrap(),
        ]
    }

    fn compiled(settings: &str) -> Result<CompiledEmbedder, ConfigError> {
        let settings: EmbedderConfig =
            toml::from_str(&format!("binding = \"embed\"\n{settings}")).unwrap();
        CompiledEmbedder::compile(settings, &bindings())
    }

    #[test]
    fn an_embedders_binding_limits_no_reply_and_says_where_text_goes() {
        let embedder = compiled("").unwrap();
        assert_eq!(embedder.destination, "api.openai.com");
        assert_eq!(embedder.binding.max_output_tokens, 0);
        assert_eq!(embedder.embedder_ref.model, "an-embedding-model");
        assert_eq!(
            (
                embedder.settings.batch_size,
                embedder.settings.max_input_bytes,
                embedder.settings.question_attempts,
                embedder.settings.question_timeout_ms,
            ),
            (32, 8_000, 3, 2_000)
        );
        // A binding that reads or judges must still say how long a reply
        // may be.
        assert!(bindings()[0].replies().is_err());
    }

    #[test]
    fn settings_that_could_not_work_are_refused_by_name() {
        for (settings, named) in [
            ("dimensions = 0", "embedder.dimensions"),
            ("dimensions = 16001", "embedder.dimensions"),
            ("max_input_bytes = 8", "embedder.max_input_bytes"),
            ("batch_size = 0", "embedder.batch_size"),
            ("batch_size = 65", "embedder.batch_size"),
            ("max_attempts = 0", "embedder.max_attempts"),
            ("lease_ms = 1000", "embedder.lease_ms"),
            ("question_timeout_ms = 0", "embedder.question_timeout_ms"),
            ("question_attempts = 6", "embedder.question_attempts"),
            ("max_distance = 0.0", "embedder.max_distance"),
            ("max_distance = 2.5", "embedder.max_distance"),
            ("document_prefix = \"a\\nb\"", "embedder.document_prefix"),
        ] {
            let err = compiled(settings).unwrap_err().to_string();
            assert!(err.contains(named), "{settings}: {err}");
        }
        let unknown: EmbedderConfig = toml::from_str("binding = \"elsewhere\"").unwrap();
        let err = CompiledEmbedder::compile(unknown, &bindings())
            .unwrap_err()
            .to_string();
        assert!(err.contains("embedder.binding `elsewhere`"), "{err}");
        // What is not a binding's id may be a value meant for another
        // setting, and is not repeated.
        let secret: EmbedderConfig = toml::from_str("binding = \"sk-live key\"").unwrap();
        let err = CompiledEmbedder::compile(secret, &bindings())
            .unwrap_err()
            .to_string();
        assert!(!err.contains("sk-live"), "{err}");
    }

    #[test]
    fn what_decides_a_vector_decides_the_revision_and_nothing_else_does() {
        let rev = |settings: &str| compiled(settings).unwrap().embedder_ref.binding_rev;
        let base = rev("");
        for changes in [
            "dimensions = 256",
            "document_prefix = \"passage: \"",
            "query_prefix = \"query: \"",
        ] {
            assert_ne!(rev(changes), base, "{changes}");
        }
        for same in [
            "batch_size = 8",
            "max_input_bytes = 2000",
            "max_attempts = 9",
            "retry_backoff_ms = 5",
            "question_attempts = 1",
            "max_distance = 0.4",
        ] {
            assert_eq!(rev(same), base, "{same}");
        }
        let mut other = bindings();
        other[0].model = "another-model".into();
        let settings: EmbedderConfig = toml::from_str("binding = \"embed\"").unwrap();
        let moved = CompiledEmbedder::compile(settings, &other).unwrap();
        assert_ne!(moved.embedder_ref.binding_rev, base);
    }

    #[test]
    fn a_text_is_cut_between_characters() {
        let embedder = compiled("max_input_bytes = 16").unwrap();
        assert_eq!(embedder.cut("short"), "short");
        assert_eq!(embedder.cut("exactly sixteen!"), "exactly sixteen!");
        // The sixteenth byte is the second of `é`.
        assert_eq!(embedder.cut("fifteen bytes..é and more"), "fifteen bytes..");
    }

    #[test]
    fn a_reply_is_sound_only_as_a_vector_of_numbers_for_each_text() {
        let free = compiled("").unwrap();
        assert!(free.sound(&[vec![0.1, 0.2], vec![0.3, 0.4]], 2));
        assert!(!free.sound(&[vec![0.1, 0.2]], 2));
        assert!(!free.sound(&[], 0));
        assert!(!free.sound(&[vec![0.1, 0.2], vec![0.3]], 2));
        assert!(!free.sound(&[vec![f32::NAN, 0.2]], 1));
        assert!(!free.sound(&[vec![f32::INFINITY, 0.2]], 1));
        assert!(!free.sound(&[vec![0.0, 0.0]], 1));
        assert!(!free.sound(&[Vec::new()], 1));
        let fixed = compiled("dimensions = 3").unwrap();
        assert!(fixed.sound(&[vec![0.1, 0.2, 0.3]], 1));
        assert!(!fixed.sound(&[vec![0.1, 0.2]], 1));
    }

    #[test]
    fn a_question_is_asked_again_only_when_asking_again_may_help() {
        let embedder = compiled("query_prefix = \"query: \"\nquestion_attempts = 3").unwrap();
        let calls = AtomicU32::new(0);
        let ask = |fails: u32, error: fn() -> TransportError| {
            calls.store(0, Ordering::SeqCst);
            let transport = FnEmbedTransport(|request: &EmbedRequest| {
                assert_eq!(request.inputs, ["query: what was decided"]);
                if calls.fetch_add(1, Ordering::SeqCst) < fails {
                    return Err(error());
                }
                Ok(EmbedResponse {
                    vectors: vec![vec![0.5, 0.5]],
                    input_tokens: 4,
                })
            });
            let vector = embedder.question(&transport, "what was decided");
            (vector, calls.load(Ordering::SeqCst))
        };
        let down = || TransportError::Transient {
            message: "503".into(),
            retry_after_ms: None,
        };
        assert_eq!(ask(0, down), (Some(vec![0.5, 0.5]), 1));
        assert_eq!(ask(2, down), (Some(vec![0.5, 0.5]), 3));
        assert_eq!(ask(3, down), (None, 3));
        // A reply that never came is asked for again too.
        assert_eq!(
            ask(1, || TransportError::InDoubt {
                message: "timeout".into()
            }),
            (Some(vec![0.5, 0.5]), 2)
        );
        // These would fail alike, or ask for a wait a recall cannot give.
        let once: [fn() -> TransportError; 4] = [
            || TransportError::Permanent {
                message: "401".into(),
            },
            || TransportError::Transient {
                message: "429".into(),
                retry_after_ms: Some(30_000),
            },
            || TransportError::Untrusted {
                message: "certificate".into(),
            },
            || TransportError::DidNotFit {
                message: "too long".into(),
            },
        ];
        for error in once {
            assert_eq!(ask(1, error), (None, 1));
        }
    }
}

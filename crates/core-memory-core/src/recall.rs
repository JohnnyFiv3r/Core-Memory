//! Recall: what a caller may ask the store for.
//!
//! The store answers a [`RecallQuery`] with one read on one snapshot (see
//! [`crate::store::Store::recall`]). This module turns a caller's
//! [`RecallRequest`] into a query, or refuses it. A refusal never repeats
//! what the caller sent: a query may be words of what a source said.

use std::collections::BTreeSet;

use core_memory_contract::{
    RECALL_DEFAULT_FOLLOW, RECALL_DEFAULT_LIMIT, RECALL_MAX_FOLLOW, RECALL_MAX_LIMIT,
    RECALL_MAX_QUERY_BYTES, RECALL_MAX_SCOPES, RecallAnswer, RecallMeaning, RecallRequest,
    RecallSearch, Sha256Digest,
};

use crate::engine::{Engine, ScopeGrants};
use crate::error::StoreError;
use crate::model::EmbedTransport;
use crate::store::{MeaningReady, Store};

/// Longest scope name, as the store's own table allows.
const MAX_SCOPE_BYTES: usize = 512;

/// A request that may be answered.
#[derive(Debug, Clone, PartialEq)]
pub struct RecallQuery {
    /// The words, trimmed, read as a web search reads them.
    pub query: String,
    /// The scopes searched, and the only scopes whose beads the answer may
    /// hold.
    pub scopes: ScopeGrants,
    pub limit: u32,
    pub follow: u32,
    pub include_ended: bool,
    /// Which ways the caller wants searched.
    pub search: Search,
    /// The query as a vector to search by meaning with, or why there is
    /// none. A request never carries one: [`Engine::recall`] has the
    /// engine's embedder make it.
    pub meaning: MeaningLeg,
}

/// Which ways a query is searched.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Search {
    /// By the words and by meaning, as far as meaning can be searched.
    Both,
    /// By the words only. The query is sent nowhere.
    Words,
    /// By meaning only.
    Meaning,
}

/// The search by meaning a query gets.
#[derive(Debug, Clone, PartialEq)]
pub enum MeaningLeg {
    /// Compare this vector with the stored ones.
    Vector(QuestionVector),
    /// None, and why. Never [`RecallMeaning::Used`].
    Without(RecallMeaning),
}

/// A query as its embedder's vector.
#[derive(Debug, Clone, PartialEq)]
pub struct QuestionVector {
    pub vector: Vec<f32>,
    /// The embedder that made it. Only vectors it made are compared.
    pub embedder_rev: Sha256Digest,
    /// Where the embedder sends text. Only vectors of scopes that allow it
    /// now are compared.
    pub destination: String,
    /// The farthest a chunk's vector may be and still match, as a cosine
    /// distance, if the embedder sets a limit.
    pub max_distance: Option<f32>,
}

/// Why a request is not answered.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum RecallRefusal {
    #[error("the query has no words")]
    EmptyQuery,
    #[error("the query is longer than {RECALL_MAX_QUERY_BYTES} bytes")]
    QueryTooLong,
    #[error("the query holds a control character")]
    QueryControl,
    #[error("limit must be between 1 and {RECALL_MAX_LIMIT}")]
    Limit,
    #[error("follow must be between 0 and {RECALL_MAX_FOLLOW}")]
    Follow,
    #[error(
        "scopes must name between 1 and {RECALL_MAX_SCOPES} scopes, each of at most \
         {MAX_SCOPE_BYTES} bytes and without control characters"
    )]
    Scopes,
    #[error("the caller may not recall from every scope it named")]
    ScopeNotGranted,
    #[error("the caller may not recall from any scope")]
    NothingGranted,
}

impl RecallRefusal {
    /// The refusal as a code for an API's answer.
    pub fn code(self) -> &'static str {
        match self {
            RecallRefusal::EmptyQuery => "empty_query",
            RecallRefusal::QueryTooLong => "query_too_long",
            RecallRefusal::QueryControl => "query_control_character",
            RecallRefusal::Limit => "bad_limit",
            RecallRefusal::Follow => "bad_follow",
            RecallRefusal::Scopes => "bad_scopes",
            RecallRefusal::ScopeNotGranted => "scope_not_granted",
            RecallRefusal::NothingGranted => "recall_not_granted",
        }
    }

    /// Whether the caller's grants refused it, rather than its request.
    pub fn is_forbidden(self) -> bool {
        matches!(
            self,
            RecallRefusal::ScopeNotGranted | RecallRefusal::NothingGranted
        )
    }
}

fn has_control(text: &str) -> bool {
    text.chars()
        .any(|c| c.is_control() && !matches!(c, '\t' | '\n' | '\r'))
}

impl RecallQuery {
    /// The query a caller who may recall from `may` asked for, or why it
    /// is refused. Without scopes named, every scope it may recall from is
    /// searched.
    pub fn from_request(request: &RecallRequest, may: &ScopeGrants) -> Result<Self, RecallRefusal> {
        let query = request.query.trim();
        if query.is_empty() {
            return Err(RecallRefusal::EmptyQuery);
        }
        if query.len() > RECALL_MAX_QUERY_BYTES {
            return Err(RecallRefusal::QueryTooLong);
        }
        if has_control(query) {
            return Err(RecallRefusal::QueryControl);
        }
        let limit = request.limit.unwrap_or(RECALL_DEFAULT_LIMIT);
        if limit == 0 || limit > RECALL_MAX_LIMIT {
            return Err(RecallRefusal::Limit);
        }
        let follow = request.follow.unwrap_or(RECALL_DEFAULT_FOLLOW);
        if follow > RECALL_MAX_FOLLOW {
            return Err(RecallRefusal::Follow);
        }
        let scopes = match &request.scopes {
            None => match may {
                ScopeGrants::Only(none) if none.is_empty() => {
                    return Err(RecallRefusal::NothingGranted);
                }
                granted => granted.clone(),
            },
            Some(named) => {
                if named.is_empty()
                    || named.len() > RECALL_MAX_SCOPES
                    || named.iter().any(|s| {
                        s.is_empty() || s.len() > MAX_SCOPE_BYTES || s.chars().any(char::is_control)
                    })
                {
                    return Err(RecallRefusal::Scopes);
                }
                if !named.iter().all(|s| may.allows(s)) {
                    return Err(RecallRefusal::ScopeNotGranted);
                }
                ScopeGrants::Only(named.iter().cloned().collect::<BTreeSet<_>>())
            }
        };
        let search = match request.search {
            None => Search::Both,
            Some(RecallSearch::Words) => Search::Words,
            Some(RecallSearch::Meaning) => Search::Meaning,
        };
        Ok(Self {
            query: query.to_string(),
            scopes,
            limit,
            follow,
            include_ended: request.include_ended,
            search,
            // Until an engine with an embedder says otherwise.
            meaning: MeaningLeg::Without(match search {
                Search::Words => RecallMeaning::NotAsked,
                Search::Both | Search::Meaning => RecallMeaning::NotConfigured,
            }),
        })
    }
}

impl Engine {
    /// Answers a query: by its words, and by its meaning where this engine
    /// has an embedder, `ask` reaches it, and the scopes searched hold
    /// vectors to compare with.
    ///
    /// The query is the caller's own text, not stored text, so it is sent
    /// to the embedder without a receipt. It is sent only when a scope
    /// that would be searched holds vectors of this embedder and allows
    /// its destination now, and never when the caller asked for the words
    /// only. An embedder that does not answer is asked again, up to
    /// `embedder.question_attempts` times in all; then the answer is made
    /// from the words alone and says so. A failure that would repeat, such
    /// as a key the provider turns down, is not asked again.
    pub fn recall(
        &self,
        store: &mut dyn Store,
        ask: Option<&dyn EmbedTransport>,
        mut query: RecallQuery,
    ) -> Result<RecallAnswer, StoreError> {
        if query.search != Search::Words {
            query.meaning = match (&self.embedder, ask) {
                (Some(embedder), Some(ask)) => {
                    let ready = store.meaning_ready(
                        &query.scopes,
                        &embedder.destination,
                        &embedder.embedder_ref.binding_rev,
                    )?;
                    match ready {
                        MeaningReady::NoVectorType => {
                            MeaningLeg::Without(RecallMeaning::NotInstalled)
                        }
                        MeaningReady::NoVectors => MeaningLeg::Without(RecallMeaning::NoVectors),
                        MeaningReady::Ready => match embedder.question(ask, &query.query) {
                            Some(vector) => MeaningLeg::Vector(QuestionVector {
                                vector,
                                embedder_rev: embedder.embedder_ref.binding_rev,
                                destination: embedder.destination.clone(),
                                max_distance: embedder.settings.max_distance,
                            }),
                            None => MeaningLeg::Without(RecallMeaning::Unavailable),
                        },
                    }
                }
                _ => MeaningLeg::Without(RecallMeaning::NotConfigured),
            };
        }
        store.recall(&query)
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]

    use super::*;

    fn request(query: &str) -> RecallRequest {
        RecallRequest {
            query: query.into(),
            scopes: None,
            limit: None,
            follow: None,
            include_ended: false,
            search: None,
        }
    }

    fn only(scopes: &[&str]) -> ScopeGrants {
        ScopeGrants::Only(scopes.iter().map(|s| (*s).to_string()).collect())
    }

    #[test]
    fn a_request_takes_the_defaults_and_the_callers_scopes() {
        let q = RecallQuery::from_request(&request("  launch date \n"), &only(&["acme"])).unwrap();
        assert_eq!(q.query, "launch date");
        assert_eq!(q.scopes, only(&["acme"]));
        assert_eq!(
            (q.limit, q.follow),
            (RECALL_DEFAULT_LIMIT, RECALL_DEFAULT_FOLLOW)
        );
        assert!(!q.include_ended);
    }

    #[test]
    fn what_cannot_be_answered_is_refused_without_repeating_it() {
        let all = ScopeGrants::All;
        let refused =
            |r: &RecallRequest, may: &ScopeGrants| RecallQuery::from_request(r, may).unwrap_err();
        assert_eq!(refused(&request("   "), &all), RecallRefusal::EmptyQuery);
        let long = "a".repeat(RECALL_MAX_QUERY_BYTES + 1);
        assert_eq!(refused(&request(&long), &all), RecallRefusal::QueryTooLong);
        assert_eq!(
            refused(&request("launch\u{0}date"), &all),
            RecallRefusal::QueryControl
        );
        for limit in [0, RECALL_MAX_LIMIT + 1] {
            let r = RecallRequest {
                limit: Some(limit),
                ..request("launch")
            };
            assert_eq!(refused(&r, &all), RecallRefusal::Limit);
        }
        let r = RecallRequest {
            follow: Some(RECALL_MAX_FOLLOW + 1),
            ..request("launch")
        };
        assert_eq!(refused(&r, &all), RecallRefusal::Follow);
        for scopes in [vec![], vec![String::new()], vec!["a\u{7}".to_string()]] {
            let r = RecallRequest {
                scopes: Some(scopes),
                ..request("launch")
            };
            assert_eq!(refused(&r, &all), RecallRefusal::Scopes);
        }
        let r = RecallRequest {
            scopes: Some(vec!["acme".into(), "secret-scope".into()]),
            ..request("launch")
        };
        let refusal = refused(&r, &only(&["acme"]));
        assert_eq!(refusal, RecallRefusal::ScopeNotGranted);
        assert!(refusal.is_forbidden());
        assert!(!refusal.to_string().contains("secret"));
        assert_eq!(
            refused(&request("launch"), &only(&[])),
            RecallRefusal::NothingGranted
        );
    }

    #[test]
    fn named_scopes_narrow_what_the_caller_may_recall() {
        let r = RecallRequest {
            scopes: Some(vec!["acme".into(), "acme".into()]),
            limit: Some(RECALL_MAX_LIMIT),
            follow: Some(0),
            include_ended: true,
            ..request("launch")
        };
        let q = RecallQuery::from_request(&r, &ScopeGrants::All).unwrap();
        assert_eq!(q.scopes, only(&["acme"]));
        assert_eq!(
            (q.limit, q.follow, q.include_ended),
            (RECALL_MAX_LIMIT, 0, true)
        );
    }
}

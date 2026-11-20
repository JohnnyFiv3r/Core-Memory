//! Recall: the stored text that holds some words, and what a judge said of
//! how it relates.
//!
//! An answer carries what a source said, verbatim, chunk by chunk, with the
//! IDs and keys to find it again. It never carries a summary, and nothing a
//! model wrote stands in for the text. What a judge said comes as arrows of
//! its directory between the beads returned, and as whether each bead is
//! still current.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::event::EventKey;
use crate::ids::{BeadId, ChunkId, EventId};
use crate::record::TimeBasis;
use crate::relation::{Arrow, BeadKind, EdgeType};
use crate::timestamp::Timestamp;

/// Matches returned when a request does not say how many.
pub const RECALL_DEFAULT_LIMIT: u32 = 10;
/// Most matches one answer returns.
pub const RECALL_MAX_LIMIT: u32 = 50;
/// Steps along the judge's arrows taken when a request does not say.
pub const RECALL_DEFAULT_FOLLOW: u32 = 1;
/// Most steps along the judge's arrows one request may take.
pub const RECALL_MAX_FOLLOW: u32 = 2;
/// Longest query, in bytes.
pub const RECALL_MAX_QUERY_BYTES: usize = 1_024;
/// Most scopes one request may name.
pub const RECALL_MAX_SCOPES: usize = 256;
/// Most beads one answer returns that were reached along arrows rather
/// than matched.
pub const RECALL_MAX_LINKED: u32 = 100;
/// Most bytes of text one answer carries. A bead whose text would take it
/// past this comes without its text, marked.
pub const RECALL_MAX_TEXT_BYTES: u64 = 1 << 20;

/// What to recall.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RecallRequest {
    /// The words to look for, read as a web search reads them: words are
    /// all required, `"quoted words"` must stand together, `or` between
    /// two words takes either, and `-word` leaves out what holds it. Words
    /// are compared as English stems, so `deadlines` finds `deadline`, and
    /// common words such as `the` are ignored.
    pub query: String,
    /// The scopes to search. Without it, every scope the caller may recall
    /// from.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scopes: Option<Vec<String>>,
    /// Most matches to return, from 1 to 50. 10 if not given.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit: Option<u32>,
    /// Steps to take along the judge's arrows from each match, from 0 to 2.
    /// 1 if not given.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub follow: Option<u32>,
    /// Also return matches that are no longer current, marked so. Without
    /// it, a match that was replaced or withdrawn is answered by what
    /// replaced or withdrew it.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub include_ended: bool,
}

/// Whether anything was found.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RecallFound {
    /// At least one chunk holds the words.
    Matched,
    /// No chunk in the scopes searched holds the words.
    NoMatch,
    /// The query has no word to look for: only common words such as `the`,
    /// punctuation, or words to leave out. Nothing was searched.
    NoTerms,
}

/// An answer to a [`RecallRequest`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RecallAnswer {
    pub found: RecallFound,
    /// The matches, best first, each followed by what replaced it if it is
    /// no longer current; then the beads reached along arrows, nearest
    /// first and, at one distance, newest first.
    pub beads: Vec<RecalledBead>,
    /// Every arrow a judge wrote between two of the beads returned.
    pub arrows: Vec<RecalledArrow>,
    /// Beads reached along arrows that were left out because the answer
    /// holds at most 100 of them.
    pub linked_omitted: u32,
}

/// How a bead came into an answer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "via", rename_all = "snake_case")]
pub enum RecallVia {
    /// Its text holds the words. `rank` orders the matches of one answer
    /// and means nothing across answers.
    Match { rank: f32 },
    /// It is the current end of the chain that replaced or withdrew a
    /// match.
    Successor { of: BeadId },
    /// It was reached from a match along `steps` arrows.
    Linked { steps: u32 },
}

/// One bead of an answer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RecalledBead {
    pub bead_id: BeadId,
    pub kind: BeadKind,
    /// The event the bead belongs to, and its key, scope included. A group
    /// a judge made has none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub event: Option<RecalledEvent>,
    pub time_at: Timestamp,
    pub time_basis: TimeBasis,
    #[serde(flatten)]
    pub via: RecallVia,
    /// No `repl` or `withdraws` arrow points at it. A bead stays current
    /// until one does: a contradiction does not end it.
    pub current: bool,
    /// The bead whose arrow ended it, when it is not current and that bead
    /// is in a scope the caller may recall from.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ended_by: Option<BeadId>,
    /// A judge said another current bead contradicts it, and both are
    /// current: the dispute is open.
    pub disputed: bool,
    /// The type the most recent read of its chunk gave, if that read gave
    /// one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub type_key: Option<String>,
    /// Its text, chunk by chunk in order: one chunk for a message's bead,
    /// every chunk of a tool call. None for a group.
    pub chunks: Vec<RecalledChunk>,
    /// The text was left out because the answer had no room for it.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub text_omitted: bool,
}

/// The event a bead belongs to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecalledEvent {
    pub event_id: EventId,
    pub key: EventKey,
}

/// A chunk's text as the source wrote it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecalledChunk {
    pub chunk_id: ChunkId,
    pub ordinal: u32,
    pub text: String,
}

/// An arrow a judge wrote between two beads of an answer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RecalledArrow {
    /// The bead the arrow's forward name is said of. An arrow without a
    /// direction has its beads in ID order.
    pub from_bead: BeadId,
    pub to_bead: BeadId,
    pub arrow: Arrow,
    pub edge_type: EdgeType,
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]

    use super::*;
    use serde_json::json;

    #[test]
    fn a_request_names_only_known_fields_and_defaults_the_rest() {
        let request: RecallRequest = serde_json::from_value(json!({"query": "launch"})).unwrap();
        assert_eq!(request.scopes, None);
        assert_eq!(request.limit, None);
        assert_eq!(request.follow, None);
        assert!(!request.include_ended);
        assert!(
            serde_json::from_value::<RecallRequest>(json!({"query": "x", "summary": true}))
                .is_err()
        );
    }

    #[test]
    fn a_bead_says_how_it_came_into_the_answer() {
        let bead = RecalledBead {
            bead_id: BeadId(uuid::Uuid::nil()),
            kind: BeadKind::Chunk,
            event: None,
            time_at: Timestamp::from_unix_millis(0),
            time_basis: TimeBasis::Captured,
            via: RecallVia::Linked { steps: 2 },
            current: true,
            ended_by: None,
            disputed: false,
            type_key: None,
            chunks: Vec::new(),
            text_omitted: false,
        };
        let value = serde_json::to_value(&bead).unwrap();
        assert_eq!(value["via"], "linked");
        assert_eq!(value["steps"], 2);
        assert!(value.get("text_omitted").is_none());
        let back: RecalledBead = serde_json::from_value(value).unwrap();
        assert_eq!(back, bead);
    }
}

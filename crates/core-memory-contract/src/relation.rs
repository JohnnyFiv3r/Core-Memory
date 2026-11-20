//! How beads relate: what a judge may say, and the records of what it said.
//!
//! A relationship is always a judgment (invariant 4). Code computes what a
//! judge is shown and records its answer; it never proposes an edge and
//! never picks a pair. Nothing here holds text: a verdict, an edge, a label
//! and a group are IDs and values from fixed sets, and a name link is two
//! byte ranges.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::hash::Sha256Digest;
use crate::ids::{BeadId, CallId};
use crate::timestamp::Timestamp;

/// Revision of the pair columns: which values are computed and how.
pub const COLUMNS_REV: u32 = 1;

macro_rules! named {
    ($(#[$meta:meta])* $name:ident { $($(#[$vmeta:meta])* $variant:ident => $text:literal),+ $(,)? }) => {
        $(#[$meta])*
        #[derive(
            Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize,
            JsonSchema,
        )]
        #[serde(rename_all = "snake_case")]
        pub enum $name {
            $($(#[$vmeta])* $variant),+
        }

        impl $name {
            pub const ALL: &'static [$name] = &[$($name::$variant),+];

            pub fn as_str(self) -> &'static str {
                match self {
                    $($name::$variant => $text),+
                }
            }

            pub fn from_name(name: &str) -> Option<Self> {
                Self::ALL.iter().copied().find(|v| v.as_str() == name)
            }
        }
    };
}

named! {
    /// The five relationships. An edge has no state and no weight.
    EdgeType {
        /// The two call each other up. No cause is claimed and there is no
        /// direction.
        Near => "near",
        /// What one describes brought about what the other describes.
        Leadsto => "leadsto",
        /// Both are parts of one memory. Written as a group bead that
        /// contains each member.
        Contains => "contains",
        /// One states an attribute, a description or the source of what the
        /// other is about.
        Express => "express",
        /// A later statement follows an earlier one on the same matter.
        /// Written from the label bead of the later statement to the bead
        /// of the earlier one, which is no longer current from then on.
        Supersedes => "supersedes",
    }
}

named! {
    /// What a judge said of one pair. `Unsure` is not a no, and neither is
    /// a pair that was never shown.
    Verdict {
        Near => "near",
        Leadsto => "leadsto",
        Contains => "contains",
        Express => "express",
        Supersedes => "supersedes",
        /// The judge read both and found no relationship.
        None => "none",
        /// The judge could not tell.
        Unsure => "unsure",
    }
}

impl Verdict {
    /// The relationship the verdict asserts, if it asserts one.
    pub fn edge_type(self) -> Option<EdgeType> {
        match self {
            Verdict::Near => Some(EdgeType::Near),
            Verdict::Leadsto => Some(EdgeType::Leadsto),
            Verdict::Contains => Some(EdgeType::Contains),
            Verdict::Express => Some(EdgeType::Express),
            Verdict::Supersedes => Some(EdgeType::Supersedes),
            Verdict::None | Verdict::Unsure => None,
        }
    }

    /// Whether the relationship runs from one bead to the other.
    pub fn is_directed(self) -> bool {
        matches!(
            self,
            Verdict::Leadsto | Verdict::Express | Verdict::Supersedes
        )
    }
}

named! {
    /// Which way a relationship runs between two beads in time order. It is
    /// explicit because the order in which messages arrive is not the order
    /// in which things happened.
    Direction {
        EarlierToLater => "earlier_to_later",
        LaterToEarlier => "later_to_earlier",
        Undirected => "undirected",
    }
}

named! {
    /// What a later statement says of the earlier one it follows.
    Label {
        /// It replaces what the earlier one says.
        Supersede => "supersede",
        /// It withdraws what the earlier one says.
        Retract => "retract",
        /// It confirms what the earlier one says again.
        Reaffirm => "reaffirm",
        /// The two cannot both hold. The later one is current, with its
        /// label visible, until something follows it.
        Conflict => "conflict",
    }
}

named! {
    /// Why a `supersedes` verdict wrote no label. A `supersedes` verdict
    /// without a note wrote its label in the same read.
    VerdictNote {
        /// One of its beads was no longer current when the read was stored:
        /// the later statement, or one it would have followed.
        NotCurrent => "not_current",
        /// Its source already carried a label that this one could not join:
        /// another label, or one that follows two beads already.
        LabelTaken => "label_taken",
        /// The label changes what a reader takes as current, so it is held
        /// until a second read of the same beads agrees.
        AwaitsSecondRead => "awaits_second_read",
        /// This was the second read of a held label, and it did not agree
        /// with the first. Nothing was written.
        SecondReadDiffers => "second_read_differs",
    }
}

named! {
    /// What a bead stands for.
    BeadKind {
        /// One chunk of a message, a document or a record.
        Chunk => "chunk",
        /// One call of a tool, whole.
        Tool => "tool",
        /// A judge's statement about the head of a chain.
        Label => "label",
        /// Several beads a judge marked as one memory.
        Group => "group",
    }
}

/// What code computed about a pair at the moment it was judged: numbers,
/// flags and vocabulary keys, and no text.
///
/// These are inputs for a model that ranks candidate pairs elsewhere. They
/// are frozen here so that it never learns from values that were not known
/// when the verdict was given. Nothing in the engine reads them back, and a
/// score computed from them is never evidence of a relationship.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct PairColumns {
    /// Milliseconds between the two beads' ordering times.
    pub gap_ms: u64,
    pub same_scope: bool,
    /// Both come from one event: two chunks of one message.
    pub same_event: bool,
    pub same_thread: bool,
    pub same_session: bool,
    pub same_actor: bool,
    /// One's event names the other's as what it replies to or as its parent.
    pub replies: bool,
    /// Identifiers the two share, as the source profile's patterns found
    /// them.
    pub shared_identifiers: u32,
    pub shared_urls: u32,
    pub earlier_kind: BeadKind,
    pub later_kind: BeadKind,
    /// The type a model gave each, as a key of the vocabulary, if it gave
    /// one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub earlier_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub later_type: Option<String>,
    /// Whether each was the head of its chain.
    pub earlier_is_head: bool,
    pub later_is_head: bool,
    /// Whether each was new in this read. In a window read the later bead
    /// of a pair usually is, and the earlier may be.
    pub earlier_is_new: bool,
    pub later_is_new: bool,
}

/// The judge that gave a verdict.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct JudgeRef {
    pub binding_id: String,
    /// Hash of everything that affects what the judge says: provider,
    /// endpoint, model, sampling settings, rubric, reply shape, validator.
    pub binding_rev: Sha256Digest,
    pub model: String,
    pub rubric_rev: u32,
}

/// One pair a judge was shown, and what it said. A record is written for
/// every pair, a no included.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct VerdictRecord {
    pub workspace: String,
    /// The workspace's change counter when the verdict was stored.
    pub seq: u64,
    /// The two beads in the order of their times.
    pub earlier_bead: BeadId,
    pub later_bead: BeadId,
    /// The scope of each bead. A judge reads across the scopes of a
    /// workspace, and `columns` is computed from what both beads say, so
    /// who may read a verdict depends on both.
    pub earlier_scope: String,
    pub later_scope: String,
    pub verdict: Verdict,
    pub direction: Direction,
    /// The label the judge gave with a `supersedes` verdict.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub label: Option<Label>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<VerdictNote>,
    /// 2 for the second, blind read of a proposed label.
    pub read_no: u8,
    pub columns: PairColumns,
    pub columns_rev: u32,
    pub judge: JudgeRef,
    /// The call that produced the verdict. Its receipt names where the
    /// beads' text was sent.
    pub call_id: CallId,
    pub judged_at: Timestamp,
}

/// A relationship a judge wrote.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct EdgeRecord {
    pub workspace: String,
    pub seq: u64,
    pub edge_type: EdgeType,
    /// A `near` edge has no direction: its beads are in ID order.
    pub from_bead: BeadId,
    pub to_bead: BeadId,
    pub call_id: CallId,
    pub created_at: Timestamp,
}

/// A bead a judge made. It holds no text.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct BeadRecord {
    pub bead_id: BeadId,
    pub workspace: String,
    pub seq: u64,
    #[serde(flatten)]
    pub made: MadeBead,
    pub call_id: CallId,
    pub created_at: Timestamp,
}

/// What a judge-made bead is.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "bead_kind", rename_all = "snake_case")]
pub enum MadeBead {
    /// What a later statement says of the one or two it follows.
    /// `supersedes` edges run from the label bead to each of those.
    Label {
        label: Label,
        /// The bead of the later statement, which the label bead stands for.
        source: BeadId,
    },
    /// Several beads that are one memory. `contains` edges run from it to
    /// each member.
    Group,
}

/// Two names for one entity. Each is the byte range where the name stands
/// in its own chunk, counted from the start of the chunk.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct NameLinkRecord {
    pub workspace: String,
    pub seq: u64,
    pub a_bead: BeadId,
    /// The scope of the bead, as on a verdict.
    pub a_scope: String,
    pub a_start: u64,
    pub a_end: u64,
    pub b_bead: BeadId,
    pub b_scope: String,
    pub b_start: u64,
    pub b_end: u64,
    pub call_id: CallId,
    pub linked_at: Timestamp,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_name_reads_back_and_matches_its_json() {
        fn check<T: Copy + PartialEq + std::fmt::Debug + Serialize>(
            all: &[T],
            name: fn(T) -> &'static str,
            from: fn(&str) -> Option<T>,
        ) {
            for v in all {
                assert_eq!(from(name(*v)), Some(*v));
                assert_eq!(
                    serde_json::to_value(v).unwrap(),
                    serde_json::Value::from(name(*v))
                );
            }
            assert_eq!(from("something else"), None);
        }
        check(EdgeType::ALL, EdgeType::as_str, EdgeType::from_name);
        check(Verdict::ALL, Verdict::as_str, Verdict::from_name);
        check(Direction::ALL, Direction::as_str, Direction::from_name);
        check(Label::ALL, Label::as_str, Label::from_name);
        check(
            VerdictNote::ALL,
            VerdictNote::as_str,
            VerdictNote::from_name,
        );
        check(BeadKind::ALL, BeadKind::as_str, BeadKind::from_name);
    }

    #[test]
    fn a_verdict_is_an_edge_or_a_no_or_a_doubt() {
        let edges: Vec<_> = Verdict::ALL.iter().filter_map(|v| v.edge_type()).collect();
        assert_eq!(edges, EdgeType::ALL);
        assert_eq!(Verdict::None.edge_type(), None);
        assert_eq!(Verdict::Unsure.edge_type(), None);
        // Near and contains have no direction. Neither has a no.
        for v in Verdict::ALL {
            assert_eq!(
                v.is_directed(),
                matches!(v, Verdict::Leadsto | Verdict::Express | Verdict::Supersedes),
                "{v:?}"
            );
        }
    }

    #[test]
    fn a_made_bead_says_what_kind_it_is() {
        let label = MadeBead::Label {
            label: Label::Conflict,
            source: "018f3b0c-0000-8000-8000-000000000001".parse().unwrap(),
        };
        assert_eq!(
            serde_json::to_value(&label).unwrap(),
            serde_json::json!({
                "bead_kind": "label",
                "label": "conflict",
                "source": "018f3b0c-0000-8000-8000-000000000001",
            })
        );
        assert_eq!(
            serde_json::to_value(MadeBead::Group).unwrap(),
            serde_json::json!({"bead_kind": "group"})
        );
    }
}

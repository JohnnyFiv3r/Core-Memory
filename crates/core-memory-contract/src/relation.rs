//! How beads relate: what a judge may say, and the records of what it said.
//!
//! A relationship is always a judgment (invariant 4). Code computes what a
//! judge is shown and records its answer; it never proposes an edge and
//! never picks a pair. Nothing here holds text: a verdict, an edge and a
//! group are IDs and values from fixed sets, and a name link is two byte
//! ranges.
//!
//! The relationships are those of Mark Burgess's Semantic Spacetime: four
//! types of association, near, leads to, contains and expresses, and named
//! arrows within each, as his SSTorytime defines them in its `SSTconfig`
//! files. Each arrow has a forward name and, unless it is symmetric, an
//! inverse; which of the two a judge gives says which way it runs. The few
//! arrows this engine adds are marked as its own, in the way his
//! configuration invites a user to add theirs.

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
    /// The four types of association of Semantic Spacetime. Every arrow
    /// belongs to one. An edge has no state and no weight.
    EdgeType {
        /// Proximity: the two are close, alike or about one matter. It has
        /// no direction. Burgess's type 0 in SSTorytime.
        Near => "near",
        /// Order: one follows from the other, as cause, result, dependency
        /// or replacement. Type 1.
        Leadsto => "leadsto",
        /// Membership: one is a part of the other, or both are parts of one
        /// memory. Type 2.
        Contains => "contains",
        /// Expression: one states a property, a note, an example or the
        /// source of the other. Type 3.
        Express => "express",
    }
}

/// Where an arrow's names come from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArrowOrigin {
    /// Burgess's SSTorytime `SSTconfig` files, names as he gives them.
    Sstorytime,
    /// Added by this engine, in the form his files use.
    Engine,
}

macro_rules! arrows {
    ($(
        $(#[$vmeta:meta])*
        $variant:ident => $key:literal, $edge:ident, $forward:literal, $backward:expr, $origin:ident;
    )+) => {
        /// A named relationship. It is stored by its forward name, from the
        /// bead its forward name is said of to the other.
        #[derive(
            Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize,
            JsonSchema,
        )]
        pub enum Arrow {
            $($(#[$vmeta])* #[serde(rename = $key)] $variant),+
        }

        impl Arrow {
            pub const ALL: &'static [Arrow] = &[$(Arrow::$variant),+];

            /// The forward short name, which is the key the arrow is
            /// stored by.
            pub fn as_str(self) -> &'static str {
                match self {
                    $(Arrow::$variant => $key),+
                }
            }

            pub fn from_name(name: &str) -> Option<Self> {
                Self::ALL.iter().copied().find(|a| a.as_str() == name)
            }

            /// The type of association the arrow belongs to.
            pub fn edge_type(self) -> EdgeType {
                match self {
                    $(Arrow::$variant => EdgeType::$edge),+
                }
            }

            /// The forward name in full: "A causes B".
            pub fn forward(self) -> &'static str {
                match self {
                    $(Arrow::$variant => $forward),+
                }
            }

            /// The inverse's short and full names: "B is caused by A". A
            /// symmetric arrow has none.
            pub fn backward(self) -> Option<(&'static str, &'static str)> {
                match self {
                    $(Arrow::$variant => $backward),+
                }
            }

            pub fn origin(self) -> ArrowOrigin {
                match self {
                    $(Arrow::$variant => ArrowOrigin::$origin),+
                }
            }
        }
    };
}

arrows! {
    // ---- leads to (SSTconfig/arrows-LT-1.sst) ----
    Causes => "cause", Leadsto, "causes", Some(("cause-by", "is caused by")), Sstorytime;
    ResultsIn => "result", Leadsto, "results in", Some(("result-of", "was a result of")), Sstorytime;
    Enables => "enables", Leadsto, "enables", Some(("enabled-by", "is enabled by")), Sstorytime;
    Implements => "implem", Leadsto, "implements", Some(("implorg", "was implemented by")), Sstorytime;
    /// The later statement takes the earlier one's place, which is no longer
    /// current from then on.
    Replaces => "repl", Leadsto, "replaces", Some(("repl-by", "is replaced by")), Sstorytime;
    /// The later statement withdraws the earlier one and puts nothing in
    /// its place. The earlier one is no longer current from then on: a
    /// promise withdrawn, in Burgess's Promise Theory.
    Withdraws => "withdraws", Leadsto, "withdraws", Some(("withdrawn-by", "is withdrawn by")), Engine;

    // ---- contains (SSTconfig/arrows-CN-2.sst) ----
    HasPart => "has-pt", Contains, "has a part", Some(("pt-of", "is a part of")), Sstorytime;
    /// From a group bead to each of its members: a hub that unites the
    /// parts of one memory. A judge does not give it.
    ConsistsOf => "consists", Contains, "consists of", Some(("mkpt", "makes up part of")), Sstorytime;
    /// What a judge says of two parts of one memory. It is written as a
    /// group bead that consists of each of them, and appears only on
    /// verdicts.
    SameMemory => "same-memory", Contains, "is part of one memory with", None, Engine;

    // ---- expresses (SSTconfig/arrows-EP-3.sst) ----
    Note => "note", Express, "note/remark", Some(("isnotefor", "is a note or remark about")), Sstorytime;
    HasExample => "e.g.", Express, "has example", Some(("ex", "is an example of")), Sstorytime;
    InOtherWords => "i.e.", Express, "in other words", Some(("clarify", "is a clarification of")), Sstorytime;
    HasAttribute => "attr", Express, "has an attribute", Some(("attr-of", "is an attribute of")), Sstorytime;
    DefinedAs => "def", Express, "defined as", Some(("def-of", "is the definition of")), Sstorytime;
    HasSource => "source", Express, "has source", Some(("source-of", "the source for")), Sstorytime;
    PotentialSolution => "solution", Express, "potential solution", Some(("issolution", "is a potential solution to")), Sstorytime;
    Answers => "answers", Express, "answers", Some(("answered-by", "is answered by")), Engine;
    /// The later statement says again what the earlier one says. Both stay
    /// current.
    Confirms => "confirms", Express, "confirms", Some(("confirmed-by", "is confirmed by")), Engine;

    // ---- near (SSTconfig/arrows-NR-0.sst) ----
    SeeAlso => "see", Near, "see also", None, Sstorytime;
    SimilarTo => "sim", Near, "similar to", None, Sstorytime;
    /// Two that are easily taken for one and are not. A negative arrow,
    /// marked with `!` as in his files: nearness is never completed
    /// through it.
    DontConfuseWith => "!confuse", Near, "don't confuse with", None, Sstorytime;
    /// The two cannot both hold, and neither replaces or withdraws the
    /// other. Both stay current, as each observer assesses a promise for
    /// itself, and the contradiction stays open while they are.
    Contradicts => "!contradicts", Near, "contradicts", None, Engine;
}

impl Arrow {
    /// Whether the arrow has no direction.
    pub fn is_symmetric(self) -> bool {
        self.backward().is_none()
    }

    /// Whether the arrow ends what a reader takes as current: the bead it
    /// points at is no longer current once the arrow is written. Only
    /// these make a chain, and only they wait for a second read.
    pub fn ends_currency(self) -> bool {
        matches!(self, Arrow::Replaces | Arrow::Withdraws)
    }

    /// Whether the arrow says two beads may not both hold. The beads of
    /// such an arrow are shown to a judge first while both are current.
    pub fn disputes(self) -> bool {
        self == Arrow::Contradicts
    }
}

named! {
    /// What a judge said of one pair: the type of the arrow it gave, or
    /// that it found none, or that it could not tell. `Unsure` is not a
    /// no, and neither is a pair that was never shown.
    Verdict {
        Near => "near",
        Leadsto => "leadsto",
        Contains => "contains",
        Express => "express",
        /// The judge read both and found no relationship.
        None => "none",
        /// The judge could not tell.
        Unsure => "unsure",
    }
}

impl Verdict {
    /// The verdict of a pair given an arrow of this type.
    pub fn of(edge_type: EdgeType) -> Self {
        match edge_type {
            EdgeType::Near => Verdict::Near,
            EdgeType::Leadsto => Verdict::Leadsto,
            EdgeType::Contains => Verdict::Contains,
            EdgeType::Express => Verdict::Express,
        }
    }

    /// The relationship the verdict asserts, if it asserts one.
    pub fn edge_type(self) -> Option<EdgeType> {
        match self {
            Verdict::Near => Some(EdgeType::Near),
            Verdict::Leadsto => Some(EdgeType::Leadsto),
            Verdict::Contains => Some(EdgeType::Contains),
            Verdict::Express => Some(EdgeType::Express),
            Verdict::None | Verdict::Unsure => None,
        }
    }
}

named! {
    /// Which way a relationship runs between two beads in time order: from
    /// the bead its arrow's forward name is said of. It is explicit because
    /// the order in which messages arrive is not the order in which things
    /// happened.
    Direction {
        EarlierToLater => "earlier_to_later",
        LaterToEarlier => "later_to_earlier",
        Undirected => "undirected",
    }
}

named! {
    /// Why a verdict whose arrow ends currency wrote no edge. Such a
    /// verdict without a note wrote its edge in the same read.
    VerdictNote {
        /// One of its beads was no longer current when the read was stored:
        /// the later statement, or one it would have ended.
        NotCurrent => "not_current",
        /// The later statement already ends one or two others with an
        /// arrow this one could not join: the other arrow, or two beads
        /// already.
        ChainTaken => "chain_taken",
        /// The arrow changes what a reader takes as current, so it is held
        /// until a second read of the same beads agrees.
        AwaitsSecondRead => "awaits_second_read",
        /// This was the second read of a held arrow, and it did not agree
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
    /// The arrow the judge gave, with any verdict but `none` and `unsure`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub arrow: Option<Arrow>,
    pub direction: Direction,
    /// Why an arrow that ends currency wrote no edge, if it did not.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<VerdictNote>,
    /// 2 for the second, blind read of an arrow that ends currency.
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
    /// The type of the arrow, which the arrow implies.
    pub edge_type: EdgeType,
    pub arrow: Arrow,
    /// The bead the arrow's forward name is said of. An edge of a
    /// symmetric arrow has no direction: its beads are in ID order.
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
    /// Several beads that are one memory: a hub, as Burgess builds one. A
    /// `consists` edge runs from it to each member.
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
        check(Arrow::ALL, Arrow::as_str, Arrow::from_name);
        check(Verdict::ALL, Verdict::as_str, Verdict::from_name);
        check(Direction::ALL, Direction::as_str, Direction::from_name);
        check(
            VerdictNote::ALL,
            VerdictNote::as_str,
            VerdictNote::from_name,
        );
        check(BeadKind::ALL, BeadKind::as_str, BeadKind::from_name);
    }

    #[test]
    fn a_verdict_is_a_type_or_a_no_or_a_doubt() {
        let types: Vec<_> = Verdict::ALL.iter().filter_map(|v| v.edge_type()).collect();
        assert_eq!(types, EdgeType::ALL);
        for edge_type in EdgeType::ALL {
            assert_eq!(Verdict::of(*edge_type).edge_type(), Some(*edge_type));
        }
        assert_eq!(Verdict::None.edge_type(), None);
        assert_eq!(Verdict::Unsure.edge_type(), None);
    }

    #[test]
    fn every_arrow_has_one_type_and_names_that_tell_its_ends_apart() {
        let mut names = std::collections::BTreeSet::new();
        for arrow in Arrow::ALL {
            // Every short name, forward or inverse, means one arrow one
            // way, so the name a judge gives says which way it runs.
            assert!(names.insert(arrow.as_str()), "{arrow:?}");
            if let Some((short, full)) = arrow.backward() {
                assert!(names.insert(short), "{arrow:?}");
                assert_ne!(full, arrow.forward());
            }
            // Nearness has no direction, and the other three have one,
            // but for the pairwise statement that two beads are one memory.
            assert_eq!(
                arrow.is_symmetric(),
                arrow.edge_type() == EdgeType::Near || *arrow == Arrow::SameMemory,
                "{arrow:?}"
            );
            // Only leads-to arrows end what is current, and a dispute is a
            // nearness that keeps both.
            assert!(!arrow.ends_currency() || arrow.edge_type() == EdgeType::Leadsto);
            assert!(!arrow.disputes() || arrow.edge_type() == EdgeType::Near);
        }
        // Each of the four types has arrows of its own.
        for edge_type in EdgeType::ALL {
            assert!(Arrow::ALL.iter().any(|a| a.edge_type() == *edge_type));
        }
        // What this engine adds to Burgess's arrows is small and known.
        let added: Vec<&str> = Arrow::ALL
            .iter()
            .filter(|a| a.origin() == ArrowOrigin::Engine)
            .map(|a| a.as_str())
            .collect();
        assert_eq!(
            added,
            [
                "withdraws",
                "same-memory",
                "answers",
                "confirms",
                "!contradicts"
            ]
        );
        // A negative nearness is marked as his files mark it.
        assert!(Arrow::DontConfuseWith.as_str().starts_with('!'));
        assert!(Arrow::Contradicts.as_str().starts_with('!'));
    }

    #[test]
    fn a_made_bead_says_what_kind_it_is() {
        assert_eq!(
            serde_json::to_value(MadeBead::Group).unwrap(),
            serde_json::json!({"bead_kind": "group"})
        );
    }
}

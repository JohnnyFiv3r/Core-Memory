//! Deterministic, lossless text chunking (CHK-01..06).
//!
//! [`chunk_text`] cuts a text into byte ranges that partition it exactly:
//! concatenated in order, the chunks reproduce the input byte for byte. The
//! chunker never trims, rewrites or inserts text.
//!
//! The text is first divided into paragraphs (runs of lines separated by
//! blank lines; each paragraph keeps its trailing blank lines). Paragraphs
//! are packed greedily into chunks up to the limit. A paragraph is split only
//! when it alone exceeds the limit, preferring sentence boundaries, then
//! word boundaries, then grapheme clusters, and as a last resort code points
//! (a single grapheme longer than the limit). The pieces of a split
//! paragraph are packed the same way, and packing restarts at the next
//! paragraph, so paragraph boundaries stay chunk boundaries where possible.
//! Finally, a chunk that is only whitespace hands its graphemes to the
//! chunks around it as far as their limits allow, so a whitespace-only
//! chunk survives only when its neighbors are full.
//!
//! Output depends only on the text and the configuration. Any change to
//! this algorithm must bump [`ALGORITHM_REV`].

use std::ops::Range;

use serde::{Deserialize, Serialize};
use unicode_segmentation::UnicodeSegmentation;

/// Name of the algorithm in this crate.
pub const ALGORITHM: &str = "text.structural";
/// Revision of the algorithm. Bump on any change to its output.
pub const ALGORITHM_REV: u32 = 1;

/// The unit a length limit is counted in (CHK-02).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LengthUnit {
    /// UTF-8 bytes.
    Bytes,
    /// Unicode scalar values.
    CodePoints,
}

impl LengthUnit {
    fn measure(self, s: &str) -> usize {
        match self {
            LengthUnit::Bytes => s.len(),
            LengthUnit::CodePoints => s.chars().count(),
        }
    }

    /// The smallest limit that can always hold one code point.
    fn minimum(self) -> u32 {
        match self {
            LengthUnit::Bytes => 4,
            LengthUnit::CodePoints => 1,
        }
    }
}

/// Settings for [`chunk_text`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TextChunkerConfig {
    /// Maximum chunk length, in `unit`.
    pub max: u32,
    pub unit: LengthUnit,
}

impl TextChunkerConfig {
    pub fn validate(&self) -> Result<(), ChunkError> {
        if self.max < self.unit.minimum() {
            return Err(ChunkError::InvalidConfig(format!(
                "max must be at least {} {}",
                self.unit.minimum(),
                match self.unit {
                    LengthUnit::Bytes => "bytes",
                    LengthUnit::CodePoints => "code points",
                }
            )));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ChunkError {
    #[error("text has no non-whitespace content")]
    Empty,
    #[error("invalid chunker configuration: {0}")]
    InvalidConfig(String),
}

/// Cuts `text` into byte ranges that partition it, each at most
/// `config.max` long in `config.unit`.
pub fn chunk_text(text: &str, config: &TextChunkerConfig) -> Result<Vec<Range<usize>>, ChunkError> {
    config.validate()?;
    if text.trim().is_empty() {
        return Err(ChunkError::Empty);
    }
    let packer = Packer {
        text,
        unit: config.unit,
        max: config.max as usize,
    };
    let mut out = Vec::new();
    if packer.len(&(0..text.len())) <= packer.max {
        out.push(0..text.len());
        return Ok(out);
    }
    packer.pack_paragraphs(paragraphs(text), &mut out);
    packer.absorb_whitespace(&mut out);
    Ok(out)
}

/// Paragraph ranges partitioning `text`. Blank lines stay with the
/// paragraph before them; leading blank lines stay with the first one.
fn paragraphs(text: &str) -> Vec<Range<usize>> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut pos = 0;
    let mut seen_content = false;
    let mut in_separator = false;
    for line in text.split_inclusive('\n') {
        if line.trim().is_empty() {
            in_separator |= seen_content;
        } else {
            if in_separator {
                out.push(start..pos);
                start = pos;
                in_separator = false;
            }
            seen_content = true;
        }
        pos += line.len();
    }
    out.push(start..text.len());
    out
}

/// The boundary used to split a piece that is too long.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Level {
    Sentence,
    Word,
    Grapheme,
    CodePoint,
}

impl Level {
    fn next(self) -> Level {
        match self {
            Level::Sentence => Level::Word,
            Level::Word => Level::Grapheme,
            Level::Grapheme | Level::CodePoint => Level::CodePoint,
        }
    }
}

struct Packer<'a> {
    text: &'a str,
    unit: LengthUnit,
    max: usize,
}

impl Packer<'_> {
    fn len(&self, range: &Range<usize>) -> usize {
        self.unit.measure(&self.text[range.clone()])
    }

    /// Packs paragraphs into chunks. Paragraphs that fit are packed
    /// greedily; a paragraph longer than `max` is refined into pieces that
    /// fit and those are packed on their own.
    fn pack_paragraphs(&self, paragraphs: Vec<Range<usize>>, out: &mut Vec<Range<usize>>) {
        let mut acc = Accumulator::default();
        for paragraph in paragraphs {
            if self.len(&paragraph) > self.max {
                acc.flush(out);
                let mut pieces = Vec::new();
                self.refine(paragraph, Level::Sentence, &mut pieces);
                let mut inner = Accumulator::default();
                for piece in pieces {
                    let len = self.len(&piece);
                    inner.add(piece, len, self.max, out);
                }
                inner.flush(out);
            } else {
                let len = self.len(&paragraph);
                acc.add(paragraph, len, self.max, out);
            }
        }
        acc.flush(out);
    }

    /// Splits `range` at `level` boundaries, splitting further any segment
    /// still longer than `max`, so every resulting piece fits.
    fn refine(&self, range: Range<usize>, level: Level, out: &mut Vec<Range<usize>>) {
        for segment in self.segment(range, level) {
            if self.len(&segment) > self.max {
                self.refine(segment, level.next(), out);
            } else {
                out.push(segment);
            }
        }
    }

    /// Moves the graphemes of whitespace-only chunks into the previous
    /// chunk (from the front) and the next chunk (from the back) while each
    /// stays within the limit, repeating until nothing moves. Only chunks
    /// with content absorb, so whitespace only flows out of blank chunks and
    /// the loop ends. Empty chunks are dropped.
    fn absorb_whitespace(&self, spans: &mut Vec<Range<usize>>) {
        loop {
            let moved = self.absorb_pass(spans);
            // Drop emptied chunks so their neighbors become adjacent.
            spans.retain(|r| !r.is_empty());
            if !moved {
                break;
            }
        }
    }

    fn absorb_pass(&self, spans: &mut [Range<usize>]) -> bool {
        let is_blank = |r: &Range<usize>| self.text[r.clone()].trim().is_empty();
        let mut moved = false;
        for i in 0..spans.len() {
            if spans[i].is_empty() || !is_blank(&spans[i]) {
                continue;
            }
            if i > 0 && !is_blank(&spans[i - 1]) {
                let mut slack = self.max.saturating_sub(self.len(&spans[i - 1]));
                let start = spans[i].start;
                for (offset, g) in self.text[spans[i].clone()].grapheme_indices(true) {
                    let len = self.unit.measure(g);
                    if len > slack {
                        break;
                    }
                    slack -= len;
                    spans[i - 1].end = start + offset + g.len();
                    spans[i].start = spans[i - 1].end;
                    moved = true;
                }
            }
            if i + 1 < spans.len() && !spans[i].is_empty() && !is_blank(&spans[i + 1]) {
                let mut slack = self.max.saturating_sub(self.len(&spans[i + 1]));
                let start = spans[i].start;
                for (offset, g) in self.text[spans[i].clone()].grapheme_indices(true).rev() {
                    let len = self.unit.measure(g);
                    if len > slack {
                        break;
                    }
                    slack -= len;
                    spans[i + 1].start = start + offset;
                    spans[i].end = spans[i + 1].start;
                    moved = true;
                }
            }
        }
        moved
    }

    /// Splits `range` into the units of `level`, as absolute ranges.
    fn segment(&self, range: Range<usize>, level: Level) -> Vec<Range<usize>> {
        let base = range.start;
        let s = &self.text[range.clone()];
        let starts: Vec<usize> = match level {
            Level::Sentence => s.split_sentence_bound_indices().map(|(i, _)| i).collect(),
            Level::Word => s.split_word_bound_indices().map(|(i, _)| i).collect(),
            Level::Grapheme => s.grapheme_indices(true).map(|(i, _)| i).collect(),
            Level::CodePoint => s.char_indices().map(|(i, _)| i).collect(),
        };
        let mut out = Vec::with_capacity(starts.len());
        for (n, start) in starts.iter().enumerate() {
            let end = starts.get(n + 1).copied().unwrap_or(s.len());
            out.push(base + start..base + end);
        }
        out
    }
}

/// A chunk being built by greedy packing.
#[derive(Default)]
struct Accumulator {
    current: Option<(Range<usize>, usize)>,
}

impl Accumulator {
    /// Adds a piece that fits within `max` on its own, starting a new chunk
    /// when it does not fit in the current one.
    fn add(&mut self, piece: Range<usize>, len: usize, max: usize, out: &mut Vec<Range<usize>>) {
        self.current = match self.current.take() {
            Some((range, cur)) if cur + len <= max => Some((range.start..piece.end, cur + len)),
            Some((range, _)) => {
                out.push(range);
                Some((piece, len))
            }
            None => Some((piece, len)),
        };
    }

    fn flush(&mut self, out: &mut Vec<Range<usize>>) {
        if let Some((range, _)) = self.current.take() {
            out.push(range);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn bytes(max: u32) -> TextChunkerConfig {
        TextChunkerConfig {
            max,
            unit: LengthUnit::Bytes,
        }
    }

    fn pieces<'a>(text: &'a str, spans: &[Range<usize>]) -> Vec<&'a str> {
        spans.iter().map(|r| &text[r.clone()]).collect()
    }

    fn assert_partition(text: &str, spans: &[Range<usize>]) {
        assert!(!spans.is_empty());
        assert_eq!(spans[0].start, 0);
        assert_eq!(spans[spans.len() - 1].end, text.len());
        for pair in spans.windows(2) {
            assert_eq!(pair[0].end, pair[1].start);
        }
        for span in spans {
            assert!(span.start < span.end, "empty span {span:?}");
        }
    }

    #[test]
    fn short_text_is_one_chunk() {
        let text = "One line.\n\nAnother paragraph.";
        assert_eq!(chunk_text(text, &bytes(1000)).unwrap(), vec![0..text.len()]);
    }

    #[test]
    fn splits_at_paragraph_boundaries_first() {
        let text = "First paragraph here.\n\nSecond paragraph here.\n";
        let spans = chunk_text(text, &bytes(30)).unwrap();
        assert_eq!(
            pieces(text, &spans),
            vec!["First paragraph here.\n\n", "Second paragraph here.\n"]
        );
    }

    #[test]
    fn packs_small_paragraphs_together() {
        let text = "a\n\nb\n\nc\n\nd";
        let spans = chunk_text(text, &bytes(6)).unwrap();
        assert_eq!(pieces(text, &spans), vec!["a\n\nb\n\n", "c\n\nd"]);
    }

    #[test]
    fn splits_long_paragraph_at_sentences() {
        let text = "One sentence here. Two sentence here. Three here.";
        let spans = chunk_text(text, &bytes(20)).unwrap();
        assert_eq!(
            pieces(text, &spans),
            vec!["One sentence here. ", "Two sentence here. ", "Three here."]
        );
    }

    #[test]
    fn falls_back_to_words_then_graphemes() {
        let text = "supercalifragilistic word";
        let spans = chunk_text(text, &bytes(8)).unwrap();
        assert_eq!(
            pieces(text, &spans),
            vec!["supercal", "ifragili", "stic ", "word"]
        );
    }

    #[test]
    fn never_splits_a_grapheme_that_fits() {
        // Family emoji: one grapheme, 25 bytes.
        let family = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}\u{200D}\u{1F466}";
        let text = format!("{family}{family}");
        let spans = chunk_text(&text, &bytes(30)).unwrap();
        assert_eq!(pieces(&text, &spans), vec![family, family]);
    }

    #[test]
    fn splits_an_oversize_grapheme_at_code_points() {
        let text = format!("e{}", "\u{0301}".repeat(10));
        let spans = chunk_text(&text, &bytes(4)).unwrap();
        assert_partition(&text, &spans);
        assert!(spans.iter().all(|r| r.len() <= 4));
    }

    #[test]
    fn whitespace_absorption_does_not_depend_on_order() {
        // Found by the property test: a blank chunk followed by another
        // blank chunk that is absorbed first, freeing nothing for it.
        let text = "\u{1F44D}\u{1F3FD}e\u{301}. \n\n\n\n\n\n\n\n. \n\n\u{400}\u{400}";
        let spans = chunk_text(text, &bytes(6)).unwrap();
        assert_partition(text, &spans);
        assert!(spans.iter().all(|r| r.len() <= 6));
        assert_eq!(pieces(text, &spans)[4], "\n\n. \n\n");
    }

    #[test]
    fn code_point_limits_count_characters() {
        let text = "日本語の文章です。次の文です。";
        let cfg = TextChunkerConfig {
            max: 9,
            unit: LengthUnit::CodePoints,
        };
        let spans = chunk_text(text, &cfg).unwrap();
        assert_eq!(
            pieces(text, &spans),
            vec!["日本語の文章です。", "次の文です。"]
        );
    }

    #[test]
    fn whitespace_only_text_is_refused() {
        assert_eq!(chunk_text(" \n\t ", &bytes(10)), Err(ChunkError::Empty));
        assert_eq!(chunk_text("", &bytes(10)), Err(ChunkError::Empty));
    }

    #[test]
    fn limit_below_one_code_point_is_refused() {
        assert!(matches!(
            chunk_text("abc", &bytes(3)),
            Err(ChunkError::InvalidConfig(_))
        ));
    }

    #[test]
    fn leading_and_trailing_blank_lines_are_kept() {
        let text = "\n\nalpha\n\n\nbeta\n\n";
        let spans = chunk_text(text, &bytes(11)).unwrap();
        assert_eq!(pieces(text, &spans), vec!["\n\nalpha\n\n\n", "beta\n\n"]);
    }

    #[test]
    fn whitespace_left_by_a_split_moves_to_a_neighbor() {
        // The first paragraph is too long, and splitting it would leave
        // "\n\n" as a chunk of its own. The next chunk absorbs it instead.
        let text = "\n\nalpha\n\n\nbeta\n\n";
        let spans = chunk_text(text, &bytes(8)).unwrap();
        assert_eq!(pieces(text, &spans), vec!["\n\nalpha\n", "\n\nbeta\n\n"]);
    }

    fn text_strategy() -> impl Strategy<Value = String> {
        prop::collection::vec(
            prop_oneof![
                "[a-z]{1,12}",
                Just(" ".to_string()),
                Just(". ".to_string()),
                Just("\n".to_string()),
                Just("\n\n".to_string()),
                Just("\r\n".to_string()),
                "[\u{0400}-\u{04ff}]{1,6}",
                "[\u{4e00}-\u{4e40}]{1,6}",
                Just("\u{1F44D}\u{1F3FD}".to_string()),
                Just("e\u{0301}".to_string()),
            ],
            1..80,
        )
        .prop_map(|parts| parts.concat())
        .prop_filter("needs content", |s| !s.trim().is_empty())
    }

    proptest! {
        #[test]
        fn chunks_partition_the_text_within_the_limit(
            text in text_strategy(),
            max in 4u32..64,
            code_points in any::<bool>(),
        ) {
            let unit = if code_points { LengthUnit::CodePoints } else { LengthUnit::Bytes };
            let cfg = TextChunkerConfig { max, unit };
            let spans = chunk_text(&text, &cfg).unwrap();
            assert_partition(&text, &spans);
            for span in &spans {
                prop_assert!(unit.measure(&text[span.clone()]) <= max as usize);
            }
            prop_assert_eq!(pieces(&text, &spans).concat(), text.clone());
            prop_assert_eq!(chunk_text(&text, &cfg).unwrap(), spans.clone());
            // A whitespace-only chunk remains only when no neighbor with
            // content has room for the grapheme next to it.
            for (i, span) in spans.iter().enumerate() {
                let piece = &text[span.clone()];
                if !piece.trim().is_empty() {
                    continue;
                }
                let graphemes: Vec<&str> = piece.graphemes(true).collect();
                let blank = |j: usize| text[spans[j].clone()].trim().is_empty();
                if i > 0 && !blank(i - 1) {
                    let room = max as usize - unit.measure(&text[spans[i - 1].clone()]);
                    prop_assert!(unit.measure(graphemes[0]) > room);
                }
                if i + 1 < spans.len() && !blank(i + 1) {
                    let room = max as usize - unit.measure(&text[spans[i + 1].clone()]);
                    prop_assert!(unit.measure(graphemes[graphemes.len() - 1]) > room);
                }
            }
        }

        #[test]
        fn text_within_the_limit_is_a_single_chunk(text in text_strategy()) {
            let cfg = bytes(u32::try_from(text.len()).unwrap().max(4));
            prop_assert_eq!(chunk_text(&text, &cfg).unwrap(), vec![0..text.len()]);
        }
    }
}

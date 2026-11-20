//! The compressor for tool-call text: code, not a model.
//!
//! A tool's output can be thousands of lines of progress and repetition
//! around the few that say what happened. Before a model reads a tool
//! call, this shortens its text by rules that are fixed, versioned and
//! deterministic, and that say in the text itself what they left out:
//!
//! 1. A run of three or more consecutive lines that are the same, or the
//!    same but for their numbers, becomes its first line and a count. A
//!    number that is zero is only like another zero, and a line that
//!    reports trouble is only like another that does: `0 failed` never
//!    stands for `12 failed`.
//! 2. Of what remains, the first [`HEAD_LINES`] and the last
//!    [`TAIL_LINES`] lines are kept, each with its count. Between them
//!    only lines that report trouble are kept, and each stretch left out
//!    becomes a count.
//! 3. A line longer than [`MAX_LINE_BYTES`] keeps its start and its end.
//!
//! A count is written only where it takes less room than the lines it
//! stands for, so the result is never longer than the text.
//!
//! Nothing is rewritten: every line of the result is a line of the input,
//! a shortened line of the input, or a marker in square brackets. The
//! stored text of the tool call is untouched; this is only what a model is
//! shown. Changing a rule or a number here means bumping
//! [`COMPRESSOR_REV`].
//!
//! The text is read once, front to back, and what is held in memory is
//! what will be shown: a tool call of any size the engine admits is
//! shortened in time and room in proportion to it.

use std::collections::VecDeque;
use std::fmt::Write as _;

/// Names these rules in every record made from their output.
pub const COMPRESSOR_ID: &str = "tool.lines";
pub const COMPRESSOR_REV: u32 = 1;

/// Lines kept from the start of the text, after runs are collapsed.
pub const HEAD_LINES: usize = 40;
/// Lines kept from the end of the text, after runs are collapsed.
pub const TAIL_LINES: usize = 40;
/// Shortest run of similar lines that is collapsed.
pub const MIN_RUN: usize = 3;
/// Longest line kept whole, in bytes.
pub const MAX_LINE_BYTES: usize = 1_000;
/// What is kept of a longer line: this much of its start and of its end.
const LONG_LINE_HEAD: usize = 600;
const LONG_LINE_TAIL: usize = 200;
/// No marker for lines left out is as long as this, so a stretch that
/// takes more room is certain to be shorter as a marker.
const LONGEST_MARKER: usize = 64;

/// Words that mark a line as reporting trouble, compared without case.
const TROUBLE: [&str; 7] = [
    "error",
    "fail",
    "panic",
    "exception",
    "traceback",
    "warning",
    "fatal",
];
/// Phrases that report how a process ended. The line is kept if a status
/// other than zero follows any of them.
const EXITS: [&str; 3] = ["exit code", "exit status", "exited with"];

/// Compressed text, and how much it stands for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Compressed {
    pub text: String,
    /// Lines in the input.
    pub lines_in: u64,
    /// Lines of the input that are in the output, whole or shortened.
    pub lines_kept: u64,
}

/// A line of the input that is shown, and how many lines like it follow it
/// and are not. The count goes where its line goes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Kept<'a> {
    line: &'a str,
    more: usize,
    trouble: bool,
}

impl Kept<'_> {
    /// Input lines it stands for.
    fn lines(&self) -> usize {
        1 + self.more
    }

    /// Bytes it takes when shown, with the line feed after each line.
    fn bytes(&self) -> usize {
        let count = if self.more > 0 {
            similar(self.more).len() + 1
        } else {
            0
        };
        shown_len(self.line) + 1 + count
    }
}

/// What is shown between the first lines and the last.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Part<'a> {
    Kept(Kept<'a>),
    /// This many lines left out.
    Omitted(usize),
}

fn plural(n: usize) -> &'static str {
    if n == 1 { "line" } else { "lines" }
}

fn similar(more: usize) -> String {
    format!("[... {more} more {} like the one above]", plural(more))
}

fn omitted(lines: usize) -> String {
    format!("[... {lines} {} omitted]", plural(lines))
}

/// Shortens `text` by the rules above.
pub fn compress(text: &str) -> Compressed {
    let mut ends = Ends::default();
    let mut lines_in = 0u64;
    if !text.is_empty() {
        let mut run: Option<Run<'_>> = None;
        let mut shape = String::new();
        for line in text.strip_suffix('\n').unwrap_or(text).split('\n') {
            let line = line.strip_suffix('\r').unwrap_or(line);
            lines_in += 1;
            shape_into(line, &mut shape);
            let trouble = reports_trouble(line);
            match &mut run {
                Some(run) if run.trouble == trouble && run.shape == shape => run.add(line),
                _ => {
                    let next = Run::starting(line, std::mem::take(&mut shape), trouble);
                    if let Some(ended) = run.replace(next) {
                        ended.end(&mut ends);
                    }
                }
            }
        }
        if let Some(ended) = run {
            ended.end(&mut ends);
        }
    }

    let mut out = String::new();
    let mut lines_kept = 0u64;
    let mut first = true;
    for part in ends.finish() {
        let mut line_feed = |out: &mut String| {
            if !std::mem::take(&mut first) {
                out.push('\n');
            }
        };
        match part {
            Part::Kept(kept) => {
                lines_kept += 1;
                line_feed(&mut out);
                push_line(&mut out, kept.line);
                if kept.more > 0 {
                    line_feed(&mut out);
                    out.push_str(&similar(kept.more));
                }
            }
            Part::Omitted(lines) => {
                line_feed(&mut out);
                out.push_str(&omitted(lines));
            }
        }
    }
    Compressed {
        text: out,
        lines_in,
        lines_kept,
    }
}

/// Consecutive lines that are alike.
struct Run<'a> {
    first: &'a str,
    shape: String,
    trouble: bool,
    /// How many lines follow the first.
    more: usize,
    /// The room those lines would take if they were shown.
    hidden_bytes: usize,
    /// Those lines, until it is certain that a count stands for them in
    /// less room than they take. From then on they are only counted.
    rest: Vec<&'a str>,
    counted: bool,
}

impl<'a> Run<'a> {
    fn starting(first: &'a str, shape: String, trouble: bool) -> Self {
        Self {
            first,
            shape,
            trouble,
            more: 0,
            hidden_bytes: 0,
            rest: Vec::new(),
            counted: false,
        }
    }

    fn add(&mut self, line: &'a str) {
        self.more += 1;
        self.hidden_bytes += shown_len(line) + 1;
        if self.counted {
            return;
        }
        self.rest.push(line);
        // Each further line adds at least its line feed, and a longer
        // count adds one digit at most, so once a count is the shorter it
        // stays the shorter.
        if self.more + 1 >= MIN_RUN && similar(self.more).len() + 1 < self.hidden_bytes {
            self.counted = true;
            self.rest = Vec::new();
        }
    }

    fn end(self, ends: &mut Ends<'a>) {
        let kept = |line, more| Kept {
            line,
            more,
            trouble: self.trouble,
        };
        if self.counted {
            ends.push(kept(self.first, self.more));
        } else {
            ends.push(kept(self.first, 0));
            for line in self.rest {
                ends.push(kept(line, 0));
            }
        }
    }
}

/// The first lines, the last lines, and what is shown of those between.
#[derive(Default)]
struct Ends<'a> {
    head: Vec<Kept<'a>>,
    middle: Vec<Part<'a>>,
    /// The lines between that are not shown, so far.
    stretch: Stretch<'a>,
    /// The last lines seen so far.
    tail: VecDeque<Kept<'a>>,
}

/// Lines in a row that are left out.
#[derive(Default)]
struct Stretch<'a> {
    lines: usize,
    bytes: usize,
    /// The lines themselves, while they are few enough that showing them
    /// may take less room than saying that they were left out.
    shown: Vec<Kept<'a>>,
}

impl<'a> Ends<'a> {
    fn push(&mut self, kept: Kept<'a>) {
        if self.head.len() < HEAD_LINES {
            self.head.push(kept);
            return;
        }
        self.tail.push_back(kept);
        if self.tail.len() <= TAIL_LINES {
            return;
        }
        // A line that is neither among the first nor the last is shown
        // only if it reports trouble.
        let Some(between) = self.tail.pop_front() else {
            return;
        };
        if between.trouble {
            self.end_stretch();
            self.middle.push(Part::Kept(between));
        } else {
            let stretch = &mut self.stretch;
            stretch.lines += between.lines();
            stretch.bytes += between.bytes();
            if stretch.bytes <= LONGEST_MARKER {
                stretch.shown.push(between);
            } else {
                stretch.shown = Vec::new();
            }
        }
    }

    fn end_stretch(&mut self) {
        let stretch = std::mem::take(&mut self.stretch);
        if stretch.lines == 0 {
            return;
        }
        if stretch.bytes <= LONGEST_MARKER && omitted(stretch.lines).len() + 1 >= stretch.bytes {
            // Saying that these were left out would take more room than
            // they do.
            self.middle
                .extend(stretch.shown.into_iter().map(Part::Kept));
        } else {
            self.middle.push(Part::Omitted(stretch.lines));
        }
    }

    fn finish(mut self) -> impl Iterator<Item = Part<'a>> {
        self.end_stretch();
        self.head
            .into_iter()
            .map(Part::Kept)
            .chain(self.middle)
            .chain(self.tail.into_iter().map(Part::Kept))
    }
}

/// Where the number that starts at `start` ends, and whether it is zero.
/// A number is digits, with a `.`, `,` or `:` between digits read as part
/// of it: a decimal, a version, a time of day.
fn number(bytes: &[u8], start: usize) -> (usize, bool) {
    let mut end = start;
    let mut zero = true;
    loop {
        while end < bytes.len() && bytes[end].is_ascii_digit() {
            zero &= bytes[end] == b'0';
            end += 1;
        }
        let joined = end + 1 < bytes.len()
            && matches!(bytes[end], b'.' | b',' | b':')
            && bytes[end + 1].is_ascii_digit();
        if !joined {
            return (end, zero);
        }
        end += 1;
    }
}

/// Writes into `shape` the line with every number replaced by `#`, or by
/// `0` if the number is zero. Lines that differ only in a counter, a size
/// or a time then compare equal, and a count of none never compares equal
/// to a count of some.
fn shape_into(line: &str, shape: &mut String) {
    shape.clear();
    let bytes = line.as_bytes();
    let mut copied = 0;
    let mut at = 0;
    while at < bytes.len() {
        if !bytes[at].is_ascii_digit() {
            at += 1;
            continue;
        }
        let (end, zero) = number(bytes, at);
        // Digits are ASCII, so both ends are character boundaries.
        shape.push_str(&line[copied..at]);
        shape.push(if zero { '0' } else { '#' });
        copied = end;
        at = end;
    }
    shape.push_str(&line[copied..]);
}

/// Where `word` is in `line`, compared without case. `word` is lower-case
/// ASCII.
fn find_word(line: &[u8], word: &str, from: usize) -> Option<usize> {
    let word = word.as_bytes();
    line.get(from..)?
        .windows(word.len())
        .position(|window| window.eq_ignore_ascii_case(word))
        .map(|at| from + at)
}

/// Whether a line reports trouble: it holds one of [`TROUBLE`], or says a
/// process ended with a status other than zero.
fn reports_trouble(line: &str) -> bool {
    let bytes = line.as_bytes();
    if TROUBLE
        .iter()
        .any(|word| find_word(bytes, word, 0).is_some())
    {
        return true;
    }
    EXITS.iter().any(|phrase| {
        // Every place the phrase stands, not only the first: one line can
        // report how several steps ended.
        let mut from = 0;
        while let Some(at) = find_word(bytes, phrase, from) {
            from = at + phrase.len();
            let status = (from..bytes.len()).find(|i| bytes[*i].is_ascii_digit());
            if status.is_some_and(|start| !number(bytes, start).1) {
                return true;
            }
        }
        false
    })
}

/// Where a line that is too long is cut: the end of what is kept of its
/// start, and the start of what is kept of its end.
fn cuts(line: &str) -> (usize, usize) {
    let mut head = LONG_LINE_HEAD;
    while !line.is_char_boundary(head) {
        head -= 1;
    }
    let mut tail = line.len() - LONG_LINE_TAIL;
    while !line.is_char_boundary(tail) {
        tail += 1;
    }
    (head, tail)
}

fn cut_marker(bytes: usize) -> String {
    format!(" [... {bytes} bytes omitted] ")
}

/// Bytes a line takes when shown: all of it, or what is kept of it.
fn shown_len(line: &str) -> usize {
    if line.len() <= MAX_LINE_BYTES {
        return line.len();
    }
    let (head, tail) = cuts(line);
    head + cut_marker(tail - head).len() + (line.len() - tail)
}

/// Appends a line, or the start and the end of one that is too long.
fn push_line(out: &mut String, line: &str) {
    if line.len() <= MAX_LINE_BYTES {
        out.push_str(line);
        return;
    }
    let (head, tail) = cuts(line);
    out.push_str(&line[..head]);
    let _ = write!(out, "{}", cut_marker(tail - head));
    out.push_str(&line[tail..]);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn numbered(count: usize, each: impl Fn(usize) -> String) -> String {
        (0..count).map(each).collect::<Vec<_>>().join("\n")
    }

    /// Lines that share no shape, so that none collapse.
    fn distinct(i: usize) -> String {
        let word = ["alpha", "beta", "gamma", "delta", "epsilon"][i % 5];
        format!("{word} {}", "x".repeat(i % 7 + 1))
    }

    #[test]
    fn short_text_is_shown_as_it_is() {
        let text = "$ cargo test\n   Compiling core v2\n\ntest result: ok. 12 passed";
        let compressed = compress(text);
        assert_eq!(compressed.text, text);
        assert_eq!((compressed.lines_in, compressed.lines_kept), (4, 4));
        // Line endings are normalized and a final newline adds no line.
        assert_eq!(compress("one\r\ntwo\r\n").text, "one\ntwo");
        assert_eq!(compress("one\r\ntwo\r\n").lines_in, 2);
        assert_eq!(
            compress(""),
            Compressed {
                text: String::new(),
                lines_in: 0,
                lines_kept: 0
            }
        );
        assert_eq!(compress("\n").lines_in, 1);
    }

    #[test]
    fn blank_lines_at_the_start_are_lines_like_any_other() {
        for (text, lines) in [
            ("\nfoo", 2),
            ("\n\nfoo", 3),
            ("\n\n\n\nfoo\nbar", 6),
            ("\n\n", 2),
        ] {
            let compressed = compress(text);
            assert_eq!(compressed.text, text.strip_suffix('\n').unwrap_or(text));
            assert_eq!(
                (compressed.lines_in, compressed.lines_kept),
                (lines, lines),
                "{text:?}"
            );
        }
        assert_eq!(compress("\r\n\r\nfoo").text, "\n\nfoo");
    }

    #[test]
    fn runs_of_lines_alike_but_for_their_numbers_become_a_count() {
        let text = format!(
            "start\n{}\ndone in 4.2s",
            numbered(500, |i| format!(
                "Downloading crate {}/500 (1{i} kB)",
                i + 1
            ))
        );
        let compressed = compress(&text);
        assert_eq!(
            compressed.text,
            "start\nDownloading crate 1/500 (10 kB)\n[... 499 more lines like the one above]\ndone in 4.2s"
        );
        assert_eq!((compressed.lines_in, compressed.lines_kept), (502, 3));
        // Two alike are not worth a marker.
        let two = "Compiling crate 1 of 2\nCompiling crate 2 of 2\ndone";
        assert_eq!(compress(two).text, two);
        assert_eq!(
            compress(
                "Compiling crate 1 of 3\nCompiling crate 2 of 3\nCompiling crate 3 of 3\ndone"
            )
            .text,
            "Compiling crate 1 of 3\n[... 2 more lines like the one above]\ndone"
        );
        // Blank lines are a run like any other.
        assert_eq!(
            compress(&format!("a\n{}b", "\n".repeat(60))).text,
            "a\n\n[... 59 more lines like the one above]\nb"
        );
        // Lines that differ in more than numbers are different lines.
        let named = "ok test_alpha_reads_the_file\nok test_beta_reads_the_file\nok test_gamma_reads_the_file";
        assert_eq!(compress(named).text, named);
        // A time of day, a decimal and a version are each one number.
        assert_eq!(
            compress(&numbered(30, |i| format!(
                "12:00:{i:02} worker v1.{i}.0 took 0.{i}5 s"
            )))
            .text,
            "12:00:00 worker v1.0.0 took 0.05 s\n[... 29 more lines like the one above]"
        );
    }

    #[test]
    fn a_count_of_none_is_never_shown_for_a_count_of_some() {
        // Three lines of one shape. The last is the one that matters.
        for (text, kept) in [
            (
                "job 1 exited with status 0\njob 2 exited with status 0\njob 3 exited with status 137",
                "status 137",
            ),
            (
                "shard 1: 0 failed\nshard 2: 0 failed\nshard 3: 12 failed",
                "12 failed",
            ),
            (
                "lint pass 1: 0 problems in src\nlint pass 2: 0 problems in src\nlint pass 3: 4 problems in src",
                "4 problems",
            ),
        ] {
            assert!(compress(text).text.contains(kept), "{text}");
        }
        // The same in a long run: the zeros collapse and the others stay.
        let mut lines: Vec<String> = (1..=200).map(|i| format!("shard {i}: 0 failed")).collect();
        lines[120] = "shard 121: 7 failed".into();
        assert_eq!(
            compress(&lines.join("\n")).text,
            "shard 1: 0 failed\n[... 119 more lines like the one above]\nshard 121: 7 failed\n\
             shard 122: 0 failed\n[... 78 more lines like the one above]"
        );
        // A line that reports trouble is not counted in with lines that
        // do not, whatever their numbers.
        let ended = "step 1: exit code 00\nstep 2: exit code 00\nstep 3: exit code 07";
        assert!(compress(ended).text.contains("exit code 07"));
    }

    #[test]
    fn a_count_is_written_only_where_it_is_shorter_than_its_lines() {
        // Three short lines take less room than a count of two would.
        assert_eq!(compress("a 1\na 2\na 3\nb").text, "a 1\na 2\na 3\nb");
        assert_eq!(compress("a\n\n\n\n\nb").text, "a\n\n\n\n\nb");
        // Trouble on every other line, and one short line between: each
        // short line is shown, since a marker for it would be longer.
        let text = "error\nx\n".repeat(2_000);
        let compressed = compress(&text);
        assert_eq!(compressed.text, text.trim_end());
        assert_eq!((compressed.lines_in, compressed.lines_kept), (4_000, 4_000));
        // With enough between, the marker is the shorter and is written.
        let wide = format!("error\n{}\n", "x".repeat(30)).repeat(200);
        let compressed = compress(&wide);
        assert!(compressed.text.len() < wide.len());
        assert!(
            compressed
                .text
                .contains("error\n[... 1 line omitted]\nerror")
        );
    }

    #[test]
    fn the_start_the_end_and_the_trouble_between_them_are_kept() {
        let mut lines: Vec<String> = (0..300).map(distinct).collect();
        lines[100] = "thread 'main' panicked at src/lib.rs:7".into();
        lines[101] = "note: run with RUST_BACKTRACE=1".into();
        lines[200] = "ERROR: connection refused".into();
        lines[210] = "process exited with code 0".into();
        lines[220] = "Process exited with status 101".into();
        let compressed = compress(&lines.join("\n"));
        let out: Vec<&str> = compressed.text.lines().collect();

        assert_eq!(&out[..HEAD_LINES], &lines[..HEAD_LINES]);
        assert_eq!(&out[out.len() - TAIL_LINES..], &lines[300 - TAIL_LINES..]);
        assert_eq!(
            &out[HEAD_LINES..out.len() - TAIL_LINES],
            [
                "[... 60 lines omitted]",
                "thread 'main' panicked at src/lib.rs:7",
                "[... 99 lines omitted]",
                "ERROR: connection refused",
                "[... 19 lines omitted]",
                "Process exited with status 101",
                "[... 39 lines omitted]",
            ]
        );
        assert_eq!(compressed.lines_in, 300);
        assert_eq!(compressed.lines_kept, 83);
        // Every input line is kept or counted.
        assert_eq!(60 + 99 + 19 + 39 + 83, 300);
    }

    #[test]
    fn a_count_goes_where_its_line_goes() {
        // 50 distinct lines, a run of 1,000 in the middle, 50 more. The
        // run is left out whole: its first line and its count.
        let mut lines: Vec<String> = (0..50).map(distinct).collect();
        lines.extend((1..=1_000).map(|i| format!("progress {i}%")));
        lines.extend((0..50).map(|i| distinct(i + 3)));
        let compressed = compress(&lines.join("\n"));
        let out: Vec<&str> = compressed.text.lines().collect();
        assert_eq!(out.len(), HEAD_LINES + 1 + TAIL_LINES);
        // Ten of the first fifty, the whole run, and ten of the last fifty.
        assert_eq!(out[HEAD_LINES], "[... 1020 lines omitted]");
        assert_eq!(compressed.lines_in, 1_100);
        assert_eq!(compressed.lines_kept, 80);

        // A run that reports trouble is kept with its count, and the
        // count is of lines like it, not of lines left out.
        let mut lines: Vec<String> = (0..50).map(distinct).collect();
        lines.extend((1..=500).map(|i| format!("ERROR: write to replica {i} failed")));
        lines.extend((0..50).map(|i| distinct(i + 3)));
        let compressed = compress(&lines.join("\n"));
        let out: Vec<&str> = compressed.text.lines().collect();
        assert_eq!(
            &out[HEAD_LINES..out.len() - TAIL_LINES],
            [
                "[... 10 lines omitted]",
                "ERROR: write to replica 1 failed",
                "[... 499 more lines like the one above]",
                "[... 10 lines omitted]",
            ]
        );

        // A run that starts on the last of the first lines keeps its count
        // there, and one that starts on the first of the last lines brings
        // its line with it.
        let mut lines: Vec<String> = (0..HEAD_LINES - 1).map(distinct).collect();
        lines.extend((1..=500).map(|i| format!("progress {i}%")));
        lines.extend((0..60).map(|i| distinct(i + 3)));
        let compressed = compress(&lines.join("\n"));
        let out: Vec<&str> = compressed.text.lines().collect();
        assert_eq!(
            &out[HEAD_LINES - 1..HEAD_LINES + 2],
            [
                "progress 1%",
                "[... 499 more lines like the one above]",
                "[... 20 lines omitted]",
            ]
        );
        let mut lines: Vec<String> = (0..60).map(distinct).collect();
        lines.extend((1..=500).map(|i| format!("progress {i}%")));
        lines.extend((0..TAIL_LINES - 1).map(|i| distinct(i + 3)));
        let compressed = compress(&lines.join("\n"));
        let out: Vec<&str> = compressed.text.lines().collect();
        assert_eq!(
            &out[HEAD_LINES..HEAD_LINES + 3],
            [
                "[... 20 lines omitted]",
                "progress 1%",
                "[... 499 more lines like the one above]",
            ]
        );
        // No marker ever speaks of "the one above" a marker.
        for text in [&compressed.text] {
            let shown: Vec<&str> = text.lines().collect();
            for pair in shown.windows(2) {
                assert!(
                    !(pair[0].starts_with("[... ") && pair[1].contains("like the one above")),
                    "{pair:?}"
                );
            }
        }
    }

    #[test]
    fn a_very_long_line_keeps_its_start_and_its_end() {
        let line = format!("{{\"content\": \"{}\"}}", "\u{65e5}\u{672c}".repeat(2_000));
        let compressed = compress(&line);
        assert!(
            compressed.text.len() < MAX_LINE_BYTES,
            "{}",
            compressed.text.len()
        );
        assert!(compressed.text.starts_with("{\"content\": \"\u{65e5}"));
        assert!(compressed.text.ends_with("\u{672c}\"}"));
        assert!(compressed.text.contains(" bytes omitted] "));
        assert_eq!(compressed.lines_kept, 1);
        // A line at the limit is kept whole.
        let whole = "x".repeat(MAX_LINE_BYTES);
        assert_eq!(compress(&whole).text, whole);
        // The count in the marker is of the bytes left out, wherever a
        // character of one to four bytes falls on the cuts.
        for character in ["a", "\u{e9}", "\u{65e5}", "\u{1F600}"] {
            for pad in 0..4 {
                for count in [250, 251, 333, 334, 400, 1_000] {
                    let line = format!("{}{}", "x".repeat(pad), character.repeat(count));
                    let shown = compress(&line).text;
                    if line.len() <= MAX_LINE_BYTES {
                        assert_eq!(shown, line);
                        continue;
                    }
                    let (head, rest) = shown.split_once(" [... ").unwrap();
                    let (bytes, tail) = rest.split_once(" bytes omitted] ").unwrap();
                    let bytes: usize = bytes.parse().unwrap();
                    assert_eq!(head.len() + bytes + tail.len(), line.len());
                    assert!(line.starts_with(head) && line.ends_with(tail));
                    assert_eq!(shown.len(), shown_len(&line));
                }
            }
        }
    }

    /// A small generator with a fixed seed, so that a failure repeats.
    struct Generator(u64);

    impl Generator {
        fn next(&mut self, below: usize) -> usize {
            self.0 = self
                .0
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            ((self.0 >> 33) as usize) % below
        }
    }

    /// Whether `shown` is `original` with its middle left out. The line
    /// itself may hold text that reads like a marker, so every place one
    /// could start is tried.
    fn is_shortened(original: &str, shown: &str) -> bool {
        let (open, close) = (" [... ", " bytes omitted] ");
        // Every place, including one that overlaps the place before it.
        let starts = (0..shown.len())
            .filter(|at| shown.is_char_boundary(*at) && shown[*at..].starts_with(open));
        starts.into_iter().any(|at| {
            let (head, rest) = (&shown[..at], &shown[at + open.len()..]);
            rest.split_once(close).is_some_and(|(bytes, tail)| {
                bytes.parse::<usize>().is_ok_and(|bytes| {
                    original.starts_with(head)
                        && original.ends_with(tail)
                        && head.len() + bytes + tail.len() == original.len()
                })
            })
        })
    }

    #[test]
    fn every_line_is_shown_or_counted_and_the_result_is_never_longer() {
        let pieces = [
            "ok",
            "error",
            "exit code 0",
            "exit code 3",
            "x",
            "",
            "0",
            "1",
            "22",
            "0.0",
            // Text that reads like a marker and is none. No line made of
            // these pieces is a marker word for word, which is what lets
            // the check below tell the two apart.
            "[... 3 lines]",
            "[more lines like the one above]",
            "\r",
            "warning: w",
            "a b c",
            "\u{65e5}\u{672c}",
            " [... 9 bytes] ",
        ];
        let mut generator = Generator(42);
        for round in 0..2_000 {
            let mut text = String::new();
            for _ in 0..generator.next(300) {
                let piece = pieces[generator.next(pieces.len())];
                let repeat = if generator.next(50) == 0 { 600 } else { 1 };
                for _ in 0..repeat {
                    text.push_str(piece);
                }
                if generator.next(4) != 0 {
                    text.push_str(if generator.next(5) == 0 { "\r\n" } else { "\n" });
                }
            }
            let compressed = compress(&text);
            assert_eq!(compressed, compress(&text), "round {round}");
            assert!(compressed.text.len() <= text.len(), "round {round}");

            // The lines of the input, as the compressor counts them.
            let input: Vec<&str> = if text.is_empty() {
                Vec::new()
            } else {
                text.strip_suffix('\n')
                    .unwrap_or(&text)
                    .split('\n')
                    .map(|line| line.strip_suffix('\r').unwrap_or(line))
                    .collect()
            };
            assert_eq!(compressed.lines_in as usize, input.len(), "round {round}");
            let shown: Vec<&str> = if input.is_empty() {
                Vec::new()
            } else {
                compressed.text.split('\n').collect()
            };
            // Each line of the result is the next line of the input, whole
            // or shortened, or a marker that counts the lines it stands for.
            let (mut at, mut kept) = (0usize, 0u64);
            for line in shown {
                let is_next = input.get(at).is_some_and(|next| {
                    *next == line || (next.len() > MAX_LINE_BYTES && is_shortened(next, line))
                });
                let counted = line
                    .strip_prefix("[... ")
                    .filter(|rest| {
                        rest.ends_with(" like the one above]") || rest.ends_with(" omitted]")
                    })
                    .and_then(|rest| rest.split(' ').next()?.parse::<usize>().ok());
                match (is_next, counted) {
                    (true, _) => {
                        at += 1;
                        kept += 1;
                    }
                    (false, Some(lines)) => at += lines,
                    (false, None) => {
                        panic!("round {round}: {line:?} is not line {at} of the input")
                    }
                }
            }
            assert_eq!(at, input.len(), "round {round}");
            assert_eq!(kept, compressed.lines_kept, "round {round}");
        }
    }

    #[test]
    fn the_result_is_the_same_every_time_and_only_ever_shorter() {
        let mut lines: Vec<String> = (0..2_000)
            .map(|i| match i % 9 {
                0 => format!("step {i} of 2000"),
                1 => format!("warning: unused variable `v{i}`"),
                _ => distinct(i),
            })
            .collect();
        lines.push("z".repeat(5_000));
        let text = lines.join("\n");
        let first = compress(&text);
        assert_eq!(first, compress(&text));
        assert!(first.text.len() < text.len());
        // Every line of the result is a marker or comes from the input,
        // in the order of the input.
        let mut input = lines.iter();
        for line in first.text.lines() {
            if line.starts_with("[... ") && line.ends_with(']') {
                continue;
            }
            let from = line.split(" [... ").next().unwrap();
            assert!(
                input.any(|original| original.starts_with(from)),
                "{line} is not from the input, or is out of order"
            );
        }
    }

    #[test]
    fn trouble_is_recognized_by_word_and_by_exit_status() {
        for line in [
            "error[E0308]: mismatched types",
            "FAILED tests/test_api.py::test_login",
            "Traceback (most recent call last):",
            "java.lang.NullPointerException",
            "WARNING: deprecated flag",
            "fatal: not a git repository",
            "command exited with code 2",
            "exit status: 137",
            // Every place a line says how something ended is read.
            "step build: exit code 0; step test: exit code 2",
            "Exit Code 0, then EXITED WITH 9",
        ] {
            assert!(reports_trouble(line), "{line}");
        }
        for line in [
            "test result: ok. 12 passed",
            "exit code 0",
            "Process exited with status 0 after 3 s",
            "step build: exit code 0; step test: exit code 0",
            "Compiling serde v1.0.219",
        ] {
            assert!(!reports_trouble(line), "{line}");
        }
        // A line of many steps is kept from the middle for the one that
        // went wrong.
        let mut lines: Vec<String> = (0..100).map(distinct).collect();
        lines[50] = "step build: exit code 0; step test: exit code 2".into();
        assert!(compress(&lines.join("\n")).text.contains("exit code 2"));
    }

    #[test]
    fn a_large_text_is_read_once_and_held_as_what_is_shown() {
        // Four million empty lines are one line and a count.
        let blank = "\n".repeat(4 << 20);
        let compressed = compress(&blank);
        assert_eq!(
            compressed.text,
            "\n[... 4194303 more lines like the one above]"
        );
        assert_eq!(compressed.lines_in, 4 << 20);
        // Half a million short lines that never collapse leave the first
        // and the last and one count.
        let mut busy = String::with_capacity(4 << 20);
        for i in 0..500_000usize {
            busy.push_str(["alpha", "beta", "gamma"][i % 3]);
            busy.push_str(&"x"[..i % 2]);
            busy.push('\n');
        }
        let compressed = compress(&busy);
        assert_eq!(compressed.lines_kept as usize, HEAD_LINES + TAIL_LINES);
        assert!(compressed.text.contains("[... 499920 lines omitted]"));
    }
}

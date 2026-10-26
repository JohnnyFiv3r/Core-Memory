//! Reads SourceEvents from JSON Lines: one event object per line.
//!
//! Blank lines are skipped. A line that is not a valid SourceEvent is
//! reported with its line number and reading continues; the caller decides
//! what to do with it.

use std::io::{self, BufRead};

use core_memory_contract::SourceEvent;

/// Default cap on one line, in bytes. Longer lines are skipped and reported.
pub const DEFAULT_MAX_LINE_BYTES: usize = 70 << 20;

#[derive(Debug)]
pub enum SourceItem {
    Event { line: u64, event: Box<SourceEvent> },
    Unreadable { line: u64, problem: String },
}

/// An iterator over the events in a JSON Lines stream.
#[derive(Debug)]
pub struct JsonlSource<R> {
    reader: R,
    line: u64,
    max_line_bytes: usize,
    buf: Vec<u8>,
}

impl<R: BufRead> JsonlSource<R> {
    pub fn new(reader: R) -> Self {
        Self::with_max_line_bytes(reader, DEFAULT_MAX_LINE_BYTES)
    }

    pub fn with_max_line_bytes(reader: R, max_line_bytes: usize) -> Self {
        Self {
            reader,
            line: 0,
            max_line_bytes,
            buf: Vec::new(),
        }
    }

    /// Reads one line into `buf` without growing past the cap. Returns
    /// `None` at end of input, `Some(false)` when the line was too long and
    /// skipped.
    fn read_line(&mut self) -> io::Result<Option<bool>> {
        self.buf.clear();
        let mut too_long = false;
        let mut read_any = false;
        loop {
            let available = self.reader.fill_buf()?;
            if available.is_empty() {
                return Ok(read_any.then_some(!too_long));
            }
            read_any = true;
            let (part, done) = match available.iter().position(|b| *b == b'\n') {
                Some(i) => (&available[..i], i + 1),
                None => (available, available.len()),
            };
            if !too_long {
                if self.buf.len() + part.len() > self.max_line_bytes {
                    too_long = true;
                    self.buf.clear();
                } else {
                    self.buf.extend_from_slice(part);
                }
            }
            let ended = done > part.len();
            self.reader.consume(done);
            if ended {
                return Ok(Some(!too_long));
            }
        }
    }
}

impl<R: BufRead> Iterator for JsonlSource<R> {
    type Item = io::Result<SourceItem>;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            let fits = match self.read_line() {
                Ok(None) => return None,
                Ok(Some(fits)) => fits,
                Err(e) => return Some(Err(e)),
            };
            self.line += 1;
            let line = self.line;
            if !fits {
                return Some(Ok(SourceItem::Unreadable {
                    line,
                    problem: format!("line exceeds {} bytes", self.max_line_bytes),
                }));
            }
            let text = match std::str::from_utf8(&self.buf) {
                Ok(t) => t.trim_end_matches('\r'),
                Err(e) => {
                    return Some(Ok(SourceItem::Unreadable {
                        line,
                        problem: format!("not UTF-8: {e}"),
                    }));
                }
            };
            if text.trim().is_empty() {
                continue;
            }
            return Some(Ok(match serde_json::from_str::<SourceEvent>(text) {
                Ok(event) => SourceItem::Event {
                    line,
                    event: Box::new(event),
                },
                Err(e) => SourceItem::Unreadable {
                    line,
                    problem: e.to_string(),
                },
            }));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const EVENT: &str = r#"{"scope":"a","source_ns":"n","external_id":"1","profile_id":"core.plain_text","media_type":"text/plain","content":{"text":"hi"},"parser_contract_version":"1"}"#;

    fn items(input: &str, cap: usize) -> Vec<SourceItem> {
        JsonlSource::with_max_line_bytes(input.as_bytes(), cap)
            .collect::<io::Result<_>>()
            .unwrap()
    }

    #[test]
    fn reads_events_skipping_blank_lines() {
        let input = format!("{EVENT}\n\n  \r\n{EVENT}\r\n");
        let got = items(&input, 1 << 20);
        let lines: Vec<u64> = got
            .iter()
            .map(|i| match i {
                SourceItem::Event { line, .. } => *line,
                SourceItem::Unreadable { line, problem } => panic!("line {line}: {problem}"),
            })
            .collect();
        assert_eq!(lines, vec![1, 4]);
    }

    #[test]
    fn reports_bad_lines_and_keeps_going() {
        let input = format!("{{not json}}\n{EVENT}");
        let got = items(&input, 1 << 20);
        assert!(matches!(got[0], SourceItem::Unreadable { line: 1, .. }));
        assert!(matches!(got[1], SourceItem::Event { line: 2, .. }));
    }

    #[test]
    fn skips_lines_over_the_cap_without_buffering_them() {
        let long = "x".repeat(10_000);
        let input = format!("{long}\n{EVENT}\n");
        let reader = io::BufReader::with_capacity(64, input.as_bytes());
        let got: Vec<_> = JsonlSource::with_max_line_bytes(reader, 1_000)
            .collect::<io::Result<_>>()
            .unwrap();
        assert!(
            matches!(&got[0], SourceItem::Unreadable { line: 1, problem } if problem.contains("exceeds"))
        );
        assert!(matches!(got[1], SourceItem::Event { line: 2, .. }));
    }

    #[test]
    fn last_line_without_newline_is_read() {
        let got = items(EVENT, 1 << 20);
        assert_eq!(got.len(), 1);
    }
}

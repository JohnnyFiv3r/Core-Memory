/// memoriesQL's identifier pattern: `^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$`.
///
/// Used for profile IDs, type keys, rule IDs and identifier kinds so that
/// names configured here register cleanly in memoriesQL.
pub fn is_identifier(s: &str) -> bool {
    let bytes = s.as_bytes();
    if bytes.is_empty() || bytes.len() > 128 || !bytes[0].is_ascii_lowercase() {
        return false;
    }
    let mut after_separator = false;
    for &b in &bytes[1..] {
        match b {
            b'a'..=b'z' | b'0'..=b'9' => after_separator = false,
            b'.' | b'_' | b'-' if !after_separator => after_separator = true,
            _ => return false,
        }
    }
    !after_separator
}

#[cfg(test)]
mod tests {
    use super::is_identifier;

    #[test]
    fn matches_the_memoriesql_pattern() {
        for ok in ["a", "decision", "core.chat_transcript", "type-2", "a.b_c-d"] {
            assert!(is_identifier(ok), "{ok}");
        }
        for bad in ["", "Decision", "1a", "a..b", "a.", "a b", "_a", "a/b"] {
            assert!(!is_identifier(bad), "{bad}");
        }
    }
}

//! Deterministic facets (FAC-01, FAC-02).
//!
//! Every extractor is versioned. A change to an extractor's output, including
//! one caused by upgrading a dependency it uses, must bump its revision.

use std::collections::BTreeMap;
use std::sync::LazyLock;

use core_memory_contract::{ChunkFacets, ContentKind, IdentifierMatch, Lengths, TextSpan};
use regex::Regex;

pub const LENGTHS_REV: u32 = 1;
/// Uses `whatlang`. Bump when upgrading it.
pub const LANGUAGE_REV: u32 = 1;
pub const URLS_REV: u32 = 1;
pub const IDENTIFIERS_REV: u32 = 1;

/// Most matches one list facet keeps per chunk (ENG-02).
pub const MAX_MATCHES_PER_FACET: usize = 256;

static URL: LazyLock<Regex> = LazyLock::new(|| {
    #[allow(clippy::unwrap_used)]
    Regex::new(r#"https?://[^\s<>"'`\x00-\x1f\x7f]+"#).unwrap()
});

/// Computes the facets of one chunk.
pub fn chunk_facets(text: &str, kind: ContentKind, identifiers: &[(String, Regex)]) -> ChunkFacets {
    let mut extractors = BTreeMap::new();
    let mut truncated = false;

    extractors.insert("lengths".to_string(), LENGTHS_REV);
    let lengths = Lengths {
        bytes: text.len() as u64,
        code_points: text.chars().count() as u64,
        lines: if text.is_empty() {
            0
        } else {
            text.matches('\n').count() as u64 + u64::from(!text.ends_with('\n'))
        },
    };

    let language = match kind {
        ContentKind::Text => {
            extractors.insert("language".to_string(), LANGUAGE_REV);
            whatlang::detect(text)
                .filter(whatlang::Info::is_reliable)
                .map(|info| info.lang().code().to_string())
        }
        ContentKind::Json => None,
    };

    extractors.insert("urls".to_string(), URLS_REV);
    let mut urls = Vec::new();
    for m in URL.find_iter(text) {
        if urls.len() == MAX_MATCHES_PER_FACET {
            truncated = true;
            break;
        }
        let value = trim_url(m.as_str());
        urls.push(TextSpan {
            value: value.to_string(),
            byte_start: m.start() as u64,
            byte_end: (m.start() + value.len()) as u64,
        });
    }

    let mut found = Vec::new();
    if !identifiers.is_empty() {
        extractors.insert("identifiers".to_string(), IDENTIFIERS_REV);
    }
    'kinds: for (kind, re) in identifiers {
        for m in re.find_iter(text) {
            if m.as_str().is_empty() {
                continue;
            }
            if found.len() == MAX_MATCHES_PER_FACET {
                truncated = true;
                break 'kinds;
            }
            found.push(IdentifierMatch {
                kind: kind.clone(),
                value: m.as_str().to_string(),
                byte_start: m.start() as u64,
                byte_end: m.end() as u64,
            });
        }
    }
    found.sort_by_key(|m| (m.byte_start, m.byte_end));

    ChunkFacets {
        lengths,
        language,
        urls,
        identifiers: found,
        truncated,
        extractors,
    }
}

/// Drops trailing punctuation that usually ends a sentence rather than a
/// URL, and closing brackets that have no opening partner in the URL.
fn trim_url(mut url: &str) -> &str {
    loop {
        let Some(last) = url.chars().last() else {
            return url;
        };
        let unbalanced = |open: char, close: char| {
            last == close && url.matches(close).count() > url.matches(open).count()
        };
        if matches!(last, '.' | ',' | ';' | ':' | '!' | '?')
            || unbalanced('(', ')')
            || unbalanced('[', ']')
            || unbalanced('{', '}')
        {
            url = &url[..url.len() - last.len_utf8()];
        } else {
            return url;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lengths_count_bytes_code_points_and_lines() {
        let f = chunk_facets("héllo\nworld", ContentKind::Text, &[]);
        assert_eq!(
            f.lengths,
            Lengths {
                bytes: 12,
                code_points: 11,
                lines: 2
            }
        );
        assert_eq!(chunk_facets("a\n", ContentKind::Text, &[]).lengths.lines, 1);
    }

    #[test]
    fn urls_drop_sentence_punctuation() {
        let text = "See https://example.com/a_(b). Also (https://x.io/y).";
        let f = chunk_facets(text, ContentKind::Text, &[]);
        let values: Vec<_> = f.urls.iter().map(|u| u.value.as_str()).collect();
        assert_eq!(values, vec!["https://example.com/a_(b)", "https://x.io/y"]);
        for u in &f.urls {
            assert_eq!(&text[u.byte_start as usize..u.byte_end as usize], u.value);
        }
    }

    #[test]
    fn identifiers_use_profile_patterns() {
        let pats = vec![("jira".to_string(), Regex::new(r"\b[A-Z]+-\d+\b").unwrap())];
        let f = chunk_facets("Fixes OPS-12 and OPS-7.", ContentKind::Text, &pats);
        let values: Vec<_> = f.identifiers.iter().map(|m| m.value.as_str()).collect();
        assert_eq!(values, vec!["OPS-12", "OPS-7"]);
        assert_eq!(f.extractors["identifiers"], IDENTIFIERS_REV);
    }

    #[test]
    fn language_is_reported_only_when_reliable() {
        let english = "The quarterly review meeting confirmed that we will ship the new billing system next Friday.";
        assert_eq!(
            chunk_facets(english, ContentKind::Text, &[])
                .language
                .as_deref(),
            Some("eng")
        );
        assert_eq!(chunk_facets("ok", ContentKind::Text, &[]).language, None);
        assert_eq!(chunk_facets(english, ContentKind::Json, &[]).language, None);
    }

    #[test]
    fn lists_are_capped() {
        let text = "https://a.io ".repeat(MAX_MATCHES_PER_FACET + 5);
        let f = chunk_facets(&text, ContentKind::Text, &[]);
        assert_eq!(f.urls.len(), MAX_MATCHES_PER_FACET);
        assert!(f.truncated);
    }
}

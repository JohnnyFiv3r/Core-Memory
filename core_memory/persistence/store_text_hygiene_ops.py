from __future__ import annotations

import hashlib
import re

from core_memory.config import load_query_expansions


def tokenize_for_store(text: str) -> set[str]:
    """Tokenize text into searchable store terms."""
    return {
        token.lower()
        for token in (text or "").replace("_", " ").replace("-", " ").split()
        if len(token) >= 3
    }


def is_memory_intent_for_store(text: str) -> bool:
    """Detect if a query asks for remembered context."""
    q = (text or "").lower()
    cues = [
        "remember",
        "what did we decide",
        "earlier",
        "last time",
        "previous",
        "why did we",
        "recall",
        "history",
        "find memory",
    ]
    return any(c in q for c in cues)


def expand_query_tokens_for_store(text: str, base_tokens: set[str], max_extra: int = 24) -> set[str]:
    """Bounded synonym expansion for deterministic store retrieval."""
    q = (text or "").lower()
    expanded = set(base_tokens)

    config = load_query_expansions(None)
    phrase_map = config.get("phrase_map", {})
    token_map = config.get("token_map", {})

    extras: list[str] = []
    for phrase, words in phrase_map.items():
        if phrase in q:
            extras.extend(list(words))

    for token in list(base_tokens):
        for word in token_map.get(token, set()):
            extras.append(word)

    for word in extras:
        if len(expanded) >= len(base_tokens) + max(0, int(max_extra)):
            break
        expanded.add(word)

    return expanded


def redact_text_for_store(text: str) -> str:
    """Conservative secret redaction for high-confidence credential patterns."""
    if not text:
        return text

    patterns = [
        (r"github_pat_[A-Za-z0-9_]{20,}", "github_pat"),
        (r"ghp_[A-Za-z0-9]{20,}", "github_pat_classic"),
        (r"x-access-token:[^\s@]{12,}", "x_access_token"),
        (r"AKIA[0-9A-Z]{16}", "aws_access_key_id"),
        (r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b", "bearer_token"),
    ]

    redacted = text
    for pattern, kind in patterns:
        def repl(match):
            digest = hashlib.sha256(match.group(0).encode("utf-8")).hexdigest()[:10]
            return f"[REDACTED_SECRET:{kind}:{digest}]"

        redacted = re.sub(pattern, repl, redacted)

    return redacted


def sanitize_bead_content_for_store(bead: dict) -> dict:
    """Apply content redaction to a bead's text fields before persistence."""
    bead["title"] = redact_text_for_store(bead.get("title", ""))
    bead["detail"] = redact_text_for_store(bead.get("detail", ""))
    bead["summary"] = [redact_text_for_store(str(s)) for s in (bead.get("summary") or [])]
    bead["because"] = [redact_text_for_store(str(s)) for s in (bead.get("because") or [])]
    return bead


def extract_constraints_for_store(text: str) -> list[str]:
    """Deterministic, conservative constraint extraction for store writes."""
    raw = (text or "").strip()
    if not raw:
        return []

    segments = [s.strip() for s in re.split(r"[\n.;]+", raw) if s.strip()]
    cue_re = re.compile(r"\b(must(?:\s+not)?|never|do\s+not|avoid|requires?)\b", re.IGNORECASE)

    out: list[str] = []
    seen = set()
    for segment in segments:
        normalized_segment = re.sub(r"`[^`]*`", " ", segment)
        normalized_segment = re.sub(r"\[\[.*?\]\]", " ", normalized_segment)
        normalized_segment = re.sub(r"\s+", " ", normalized_segment).strip(" -:\t")
        if not normalized_segment:
            continue
        if len(normalized_segment) < 12 or len(normalized_segment) > 180:
            continue
        if not cue_re.search(normalized_segment):
            continue

        lowered = normalized_segment.lower()
        banned = ["http://", "https://", "core_memory/", "--", "commit", "bead-"]
        if any(item in lowered for item in banned):
            continue

        tokens = re.findall(r"[a-z0-9_\-]+", lowered)
        if len(tokens) < 3 or len(tokens) > 20:
            continue
        normalized = " ".join(tokens)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)

    return out[:8]


__all__ = [
    "tokenize_for_store",
    "is_memory_intent_for_store",
    "expand_query_tokens_for_store",
    "redact_text_for_store",
    "sanitize_bead_content_for_store",
    "extract_constraints_for_store",
]

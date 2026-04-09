from __future__ import annotations

from typing import Any

from core_memory.policy import hygiene
from core_memory.retrieval import query_norm


def tokenize_for_store(store: Any, text: str) -> set[str]:
    return query_norm._tokenize(text)


def is_memory_intent_for_store(store: Any, text: str) -> bool:
    return query_norm._is_memory_intent(text)


def expand_query_tokens_for_store(store: Any, text: str, base_tokens: set[str], max_extra: int = 24) -> set[str]:
    return query_norm._expand_query_tokens(text, base_tokens, max_extra)


def redact_text_for_store(store: Any, text: str) -> str:
    return hygiene._redact_text(text)


def sanitize_bead_content_for_store(store: Any, bead: dict) -> dict:
    return hygiene.sanitize_bead_content(bead)


def extract_constraints_for_store(store: Any, text: str) -> list[str]:
    return hygiene.extract_constraints(text)


__all__ = [
    "tokenize_for_store",
    "is_memory_intent_for_store",
    "expand_query_tokens_for_store",
    "redact_text_for_store",
    "sanitize_bead_content_for_store",
    "extract_constraints_for_store",
]

from __future__ import annotations

def tokenize_for_store(text: str) -> set[str]:
    from core_memory.retrieval import query_norm  # noqa: PLC0415
    return query_norm._tokenize(text)


def is_memory_intent_for_store(text: str) -> bool:
    from core_memory.retrieval import query_norm  # noqa: PLC0415
    return query_norm._is_memory_intent(text)


def expand_query_tokens_for_store(text: str, base_tokens: set[str], max_extra: int = 24) -> set[str]:
    from core_memory.retrieval import query_norm  # noqa: PLC0415
    return query_norm._expand_query_tokens(text, base_tokens, max_extra)


def redact_text_for_store(text: str) -> str:
    from core_memory.policy import hygiene  # noqa: PLC0415
    return hygiene._redact_text(text)


def sanitize_bead_content_for_store(bead: dict) -> dict:
    from core_memory.policy import hygiene  # noqa: PLC0415
    return hygiene.sanitize_bead_content(bead)


def extract_constraints_for_store(text: str) -> list[str]:
    from core_memory.policy import hygiene  # noqa: PLC0415
    return hygiene.extract_constraints(text)


__all__ = [
    "tokenize_for_store",
    "is_memory_intent_for_store",
    "expand_query_tokens_for_store",
    "redact_text_for_store",
    "sanitize_bead_content_for_store",
    "extract_constraints_for_store",
]

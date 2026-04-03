from __future__ import annotations

from core_memory.retrieval.pipeline import memory_search_typed

SEARCH_RESULT_SCHEMA_VERSION = "memory_search_result.v1"


def search_typed(submission: dict, root: str = ".", explain: bool = True) -> dict:
    """Tool endpoint: run typed memory search with deterministic snapping and retrieval."""
    out = memory_search_typed(root=root, submission=submission, explain=bool(explain))
    if isinstance(out, dict):
        out.setdefault("schema_version", SEARCH_RESULT_SCHEMA_VERSION)
        out.setdefault("contract", "memory_search")
    return out

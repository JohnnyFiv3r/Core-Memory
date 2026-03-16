from __future__ import annotations

from core_memory.retrieval.pipeline import memory_get_search_form, memory_search_typed
from core_memory.retrieval.search_form import SEARCH_FORM_SCHEMA_VERSION

SEARCH_RESULT_SCHEMA_VERSION = "memory_search_result.v1"


def get_search_form(root: str = ".") -> dict:
    """Tool endpoint: return typed memory-search form schema + current catalog."""
    out = memory_get_search_form(root)
    if isinstance(out, dict):
        out.setdefault("schema_version", SEARCH_FORM_SCHEMA_VERSION)
    return out


def search_typed(submission: dict, root: str = ".", explain: bool = True) -> dict:
    """Tool endpoint: run typed memory search with deterministic snapping and retrieval."""
    out = memory_search_typed(root=root, submission=submission, explain=bool(explain))
    if isinstance(out, dict):
        out.setdefault("schema_version", SEARCH_RESULT_SCHEMA_VERSION)
        out.setdefault("contract", "typed_search")
    return out

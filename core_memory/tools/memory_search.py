from __future__ import annotations

from core_memory.memory_skill import memory_get_search_form, memory_search_typed


def get_search_form(root: str = "./memory") -> dict:
    """Tool endpoint: return typed memory-search form schema + current catalog."""
    return memory_get_search_form(root)


def search_typed(submission: dict, root: str = "./memory", explain: bool = True) -> dict:
    """Tool endpoint: run typed memory search with deterministic snapping and retrieval."""
    return memory_search_typed(root=root, submission=submission, explain=bool(explain))

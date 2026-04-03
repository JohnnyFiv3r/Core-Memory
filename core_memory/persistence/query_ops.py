"""Query operations extracted from MemoryStore.

Handles retrieval, constraint checking, and context-aware recall.
Methods here accept a `store` parameter (MemoryStore instance) for shared state.
"""
from __future__ import annotations

from typing import Any, Optional


def retrieve_with_context(
    store: Any,
    *,
    query_text: str = "",
    context_tags: Optional[list[str]] = None,
    limit: int = 20,
    strict_first: bool = True,
    deep_recall: bool = False,
    max_uncompact_per_turn: int = 2,
    auto_memory_intent: bool = True,
) -> dict:
    """Context-aware retrieval with strict->fallback matching + bounded deep recall.

    Delegates to the store's existing implementation. This module serves as
    the extraction target for v2.0 decomposition — callers should import
    from here for forward compatibility.
    """
    return store.retrieve_with_context(
        query_text=query_text,
        context_tags=context_tags,
        limit=limit,
        strict_first=strict_first,
        deep_recall=deep_recall,
        max_uncompact_per_turn=max_uncompact_per_turn,
        auto_memory_intent=auto_memory_intent,
    )


def active_constraints(store: Any, limit: int = 100) -> list[dict]:
    """Get active decision/goal constraints."""
    return store.active_constraints(limit=limit)


def check_plan_constraints(store: Any, plan: str, limit: int = 20) -> dict:
    """Check plan against active constraints."""
    return store.check_plan_constraints(plan, limit=limit)

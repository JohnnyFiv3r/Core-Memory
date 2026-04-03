"""Write operations extracted from MemoryStore.

Handles bead creation, compaction, promotion, and consolidation.
Methods here accept a `store` parameter (MemoryStore instance) for shared state.
"""
from __future__ import annotations

from typing import Any, Optional


def add_bead(store: Any, **kwargs: Any) -> str:
    """Add a bead via the store. Delegates to store.add_bead().

    This module serves as the extraction target for v2.0 decomposition.
    """
    return store.add_bead(**kwargs)


def consolidate(store: Any, session_id: str = "default") -> dict:
    """Consolidate a session. Delegates to store.consolidate()."""
    return store.consolidate(session_id=session_id)


def promote(store: Any, bead_id: str, promotion_reason: Optional[str] = None) -> bool:
    """Promote a bead. Delegates to store.promote()."""
    return store.promote(bead_id, promotion_reason=promotion_reason)


def uncompact(store: Any, bead_id: str) -> dict:
    """Uncompact a bead. Delegates to store.uncompact()."""
    return store.uncompact(bead_id)

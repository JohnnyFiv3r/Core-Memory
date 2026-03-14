from __future__ import annotations

from typing import Any

from core_memory.persistence.store import MemoryStore


def run_session_decision_pass(*, root: str, session_id: str, visible_bead_ids: list[str] | None = None, turn_id: str = "") -> dict[str, Any]:
    """Runtime-owned per-turn decision pass entrypoint.

    Persistence is delegated to MemoryStore, but runtime owns invocation contract.
    """
    return MemoryStore(root=root).decide_session_promotion_states(
        session_id=session_id,
        visible_bead_ids=list(visible_bead_ids or []),
        turn_id=turn_id,
    )

from __future__ import annotations

from typing import Any

from core_memory.persistence.store import MemoryStore


def run_session_decision_pass(
    *,
    root: str,
    session_id: str,
    visible_bead_ids: list[str] | None = None,
    turn_id: str = "",
    updates: dict[str, Any] | None = None,
    authorship: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce promotion shadow advice and apply only authored reviews."""
    store = MemoryStore(root=root)
    shadow = store.decide_session_promotion_states(
        session_id=session_id,
        visible_bead_ids=list(visible_bead_ids or []),
        turn_id=turn_id,
    )
    from core_memory.persistence.promotion_service import apply_agent_promotion_reviews_for_store

    applied = apply_agent_promotion_reviews_for_store(
        store,
        reviewed_beads=list((updates or {}).get("reviewed_beads") or []),
        session_id=session_id,
        turn_id=turn_id,
        authorship=authorship,
    )
    return {**shadow, "agent_reviews": applied}

from __future__ import annotations

from typing import Any

from core_memory.association.crawler_contract import apply_crawler_updates


def run_association_pass(
    *,
    root: str,
    session_id: str,
    updates: dict[str, Any],
    visible_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Runtime-owned association pass entrypoint.

    Keeps runtime as orchestration owner while delegating association policy logic.
    """
    return apply_crawler_updates(
        root=root,
        session_id=session_id,
        updates=dict(updates or {}),
        visible_bead_ids=list(visible_bead_ids or []),
    )

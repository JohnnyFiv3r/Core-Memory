from __future__ import annotations

from typing import Any


def current_promotion_state(bead: dict[str, Any]) -> str:
    if bool((bead or {}).get("promoted")):
        return "promoted"
    if bool((bead or {}).get("promotion_candidate")):
        return "candidate"
    # Legacy: fall back to status field for beads written before flag migration
    status = str((bead or {}).get("status") or "").strip().lower()
    state = str((bead or {}).get("promotion_state") or "").strip().lower()
    if state in {"promoted", "candidate"}:
        return state
    if status in {"promoted", "candidate"}:
        return status
    return "null"


def is_promotion_locked(bead: dict[str, Any]) -> bool:
    # Check new boolean flag, legacy promotion_locked flag, and legacy status/state fields.
    return (
        bool((bead or {}).get("promotion_locked"))
        or bool((bead or {}).get("promoted"))
        or current_promotion_state(bead) == "promoted"
    )


def validate_transition(*, bead: dict[str, Any], decision: str) -> tuple[bool, str | None]:
    d = str(decision or "").strip().lower()
    if is_promotion_locked(bead) and d in {"keep_candidate", "archive"}:
        return False, "promotion_locked_terminal"
    return True, None


def classify_signal(*, bead: dict[str, Any]) -> str:
    if is_promotion_locked(bead):
        return "promoted"
    because = bead.get("because") or []
    detail = str(bead.get("detail") or "").strip()
    has_evidence = bool(str(bead.get("evidence") or "").strip() or (bead.get("supporting_facts") or []))
    has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))
    btype = str(bead.get("type") or "").strip().lower()

    if btype in {"decision", "lesson", "precedent", "design_principle"} and because and (detail or has_evidence or has_link):
        return "promoted"
    if detail or has_evidence or has_link or because:
        return "candidate"
    return "null"

"""Shared goal-state filters for Dreamer detectors.

A single definition of "active, non-terminal goal" used by goal decay, goal
discovery, and future-projection convergence — so they all exclude resolved /
promoted / rejected / superseded goals identically (honoring every promotion
encoding via current_promotion_state).
"""
from __future__ import annotations

from typing import Any

from core_memory.policy.promotion_contract import current_promotion_state

_INACTIVE_GOAL_STATUSES = {"superseded", "archived", "resolved"}


def is_active_goal(bead: dict[str, Any]) -> bool:
    """True iff ``bead`` is a goal that is still an open objective — not
    superseded/archived, not resolved, not promoted, not rejected."""
    if not isinstance(bead, dict):
        return False
    if str(bead.get("type") or "").strip().lower() != "goal":
        return False
    if str(bead.get("status") or "").strip().lower() in _INACTIVE_GOAL_STATUSES:
        return False
    if str(bead.get("goal_status") or "").strip().lower() == "resolved":
        return False
    if str(bead.get("promotion_state") or "").strip().lower() == "resolved":
        return False
    if current_promotion_state(bead) == "promoted":
        return False
    if str(bead.get("approval_status") or "").strip().lower() == "rejected":
        return False
    return True


def count_goal_beads(beads: dict[str, Any]) -> int:
    return sum(1 for b in beads.values() if isinstance(b, dict) and str(b.get("type") or "").strip().lower() == "goal")


__all__ = ["is_active_goal", "count_goal_beads"]

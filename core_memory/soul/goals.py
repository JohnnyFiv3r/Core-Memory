"""SOUL goal hierarchy (PRD: docs/PRD/soul-files.md §5.2, §6, §13.3).

The agent-facing governance surface over the goal hierarchy, backed by
authoritative Goal Beads and the Goal Lifecycle v2 state machine
(``persistence/goal_lifecycle_v2.py``). The six operations of §13.3 map onto the
lifecycle:

- ``propose``  → mint a Goal Bead in ``candidate`` (an authoritative human/agent
  goal declaration, §6.1).
- ``approve``  → ``candidate → endorsed`` (human approval authorizes pursuit).
- ``reject``   → ``candidate → abandoned`` (recorded as a rejection).
- ``complete`` → ``→ completed``.
- ``abandon``  → ``→ abandoned``.
- ``decay``    → ``→ decaying``.

Goal Beads stay authoritative (§6.2): Dreamer inference never reaches this
surface — it only proposes ``goal_candidate`` rows that a human endorses
elsewhere. State lives on the Goal Bead (``goal_status``); these functions resolve
a ``goal_id`` to its bead and drive validated transitions.
"""
from __future__ import annotations

import uuid
from typing import Any

from core_memory.persistence.goal_lifecycle_v2 import (
    current_goal_status,
    transition_goal_state_for_store,
)
from core_memory.persistence.store import MemoryStore

_GOAL_SESSION = "soul-goals"


def _goal_session(subject: str) -> str:
    return f"{_GOAL_SESSION}:{str(subject or 'self')}"


def _resolve_goal_bead_id(store: Any, *, goal_id: str = "", bead_id: str = "", subject: str = "self") -> str:
    """Resolve a goal to its Goal Bead id.

    An explicit ``bead_id`` is unambiguous and wins. Otherwise a ``goal_id`` is
    resolved **within the subject's goal session** (``soul-goals:<subject>``) —
    NOT against the subject-blind heads cache — so a goal id reused across
    subjects can never resolve to (and mutate) the wrong subject's bead. The
    latest matching bead is returned. Returns "" if not found.
    """
    bid = str(bead_id or "").strip()
    if bid:
        return bid
    gid = str(goal_id or "").strip()
    if not gid:
        return ""
    session = _goal_session(subject)
    index = store._read_json(store.beads_dir / "index.json")
    matches = [
        (str(cand_id), str(b.get("created_at") or ""))
        for cand_id, b in (index.get("beads") or {}).items()
        if isinstance(b, dict)
        and str(b.get("type") or "").strip().lower() == "goal"
        and str(b.get("goal_id") or "").strip() == gid
        and str(b.get("session_id") or "").strip() == session
    ]
    if not matches:
        return ""
    matches.sort(key=lambda m: m[1])
    return matches[-1][0]


def propose_goal(
    root: str,
    *,
    title: str,
    statement: str = "",
    goal_id: str | None = None,
    success_criteria: list[str] | None = None,
    subject: str = "self",
    actor: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Declare a new goal as a ``candidate`` Goal Bead (§6.1).

    ``subject`` scopes the goal session so multiple subjects don't collide.
    """
    t = str(title or "").strip()
    if not t:
        return {"ok": False, "error": "missing_title"}
    gid = str(goal_id or "").strip() or f"goal-{uuid.uuid4().hex[:12]}"
    store = MemoryStore(root=root)
    bead_id = store.add_bead(
        type="goal",
        title=t,
        summary=[str(statement or t)],
        because=[str(reason)] if reason else [],
        goal_id=gid,
        goal_status="candidate",
        success_criteria=list(success_criteria or []),
        session_id=_goal_session(subject),
        actor=str(actor or ""),
    )
    return {"ok": True, "goal_id": gid, "bead_id": bead_id, "status": "candidate", "subject": subject}


def _transition_goal(
    root: str,
    *,
    to_state: str,
    goal_id: str = "",
    bead_id: str = "",
    subject: str = "self",
    actor: str = "",
    reason: str = "",
) -> dict[str, Any]:
    store = MemoryStore(root=root)
    resolved = _resolve_goal_bead_id(store, goal_id=goal_id, bead_id=bead_id, subject=subject)
    if not resolved:
        return {"ok": False, "error": "goal_not_found", "goal_id": goal_id, "bead_id": bead_id, "subject": subject}
    return transition_goal_state_for_store(
        store, goal_bead_id=resolved, to_state=to_state, reason=reason, actor=actor,
    )


def approve_goal(root: str, *, goal_id: str = "", bead_id: str = "", subject: str = "self", actor: str = "", reason: str = "") -> dict[str, Any]:
    """Human approval: ``candidate → endorsed`` (authorizes pursuit)."""
    return _transition_goal(root, to_state="endorsed", goal_id=goal_id, bead_id=bead_id, subject=subject, actor=actor, reason=reason)


def reject_goal(root: str, *, goal_id: str = "", bead_id: str = "", subject: str = "self", actor: str = "", reason: str = "") -> dict[str, Any]:
    """Human rejection: ``candidate → abandoned`` (recorded as a rejection)."""
    return _transition_goal(
        root, to_state="abandoned", goal_id=goal_id, bead_id=bead_id, subject=subject, actor=actor,
        reason=f"rejected: {reason}" if reason else "rejected",
    )


def complete_goal(root: str, *, goal_id: str = "", bead_id: str = "", subject: str = "self", actor: str = "", reason: str = "") -> dict[str, Any]:
    """Mark a goal ``completed``."""
    return _transition_goal(root, to_state="completed", goal_id=goal_id, bead_id=bead_id, subject=subject, actor=actor, reason=reason)


def abandon_goal(root: str, *, goal_id: str = "", bead_id: str = "", subject: str = "self", actor: str = "", reason: str = "") -> dict[str, Any]:
    """Mark a goal ``abandoned``."""
    return _transition_goal(root, to_state="abandoned", goal_id=goal_id, bead_id=bead_id, subject=subject, actor=actor, reason=reason)


def decay_goal(root: str, *, goal_id: str = "", bead_id: str = "", subject: str = "self", actor: str = "", reason: str = "") -> dict[str, Any]:
    """Mark a goal ``decaying`` (dormant but revivable)."""
    return _transition_goal(root, to_state="decaying", goal_id=goal_id, bead_id=bead_id, subject=subject, actor=actor, reason=reason)


__all__ = [
    "propose_goal",
    "approve_goal",
    "reject_goal",
    "complete_goal",
    "abandon_goal",
    "decay_goal",
]

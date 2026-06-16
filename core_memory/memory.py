from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from core_memory.schema.turn import Turn, normalize_turns, turns_from_shortcut


@dataclass
class MemorySession:
    memory: "Memory"
    session_id: str

    def __enter__(self) -> "MemorySession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def capture(self, turns: list[Turn | dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("session_id", self.session_id)
        return self.memory.capture(turns, **kwargs)


class Memory:
    """Small public facade for Core Memory's canonical capture surface."""

    def __init__(self, root: str = ".", *, self_id: str | None = None):
        self.root = str(root or ".")
        self.self_id = self_id

    def session(self, session_id: str) -> MemorySession:
        return MemorySession(self, str(session_id or ""))

    def capture(
        self,
        turns: list[Turn | dict[str, Any]] | None = None,
        *,
        user: str | None = None,
        assistant: str | None = None,
        as_user: str | None = None,
        as_assistant: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from core_memory.runtime.engine import process_turn_finalized

        shortcut_used = user is not None or assistant is not None or as_user is not None or as_assistant is not None
        if turns is not None and shortcut_used:
            raise ValueError("capture accepts either turn list or user/assistant shortcut, not both")
        if turns is None:
            if not shortcut_used:
                raise ValueError("capture requires at least one turn")
            normalized = turns_from_shortcut(
                user=str(user or ""),
                assistant=str(assistant or ""),
                as_user=as_user,
                as_assistant=as_assistant,
            )
        else:
            normalized = normalize_turns(turns)

        sid = str(session_id or kwargs.pop("session_id", "") or "default")
        tid = str(turn_id or kwargs.pop("turn_id", "") or f"turn-{uuid.uuid4().hex[:12]}")
        return process_turn_finalized(root=self.root, session_id=sid, turn_id=tid, turns=normalized, **kwargs)

    def maintain(self, action: str, **kwargs: Any) -> dict[str, Any]:
        from core_memory.management import maintain

        return maintain(root=self.root, action=action, **kwargs)

    def remove_bead(self, bead_id: str, *, reason: str, **kwargs: Any) -> dict[str, Any]:
        from core_memory.management import remove_bead

        return remove_bead(root=self.root, bead_id=bead_id, reason=reason, **kwargs)

    def remove_beads(self, bead_ids: list[str], *, reason: str, **kwargs: Any) -> dict[str, Any]:
        from core_memory.management import remove_beads

        return remove_beads(root=self.root, bead_ids=bead_ids, reason=reason, **kwargs)

    def remove_source(self, source: dict[str, Any], *, reason: str = "source removed", **kwargs: Any) -> dict[str, Any]:
        from core_memory.management import remove_source

        return remove_source(root=self.root, source=source, reason=reason, **kwargs)


def confirm_bead(root: str, bead_id: str, note: str = "") -> dict[str, Any]:
    """Record user confirmation of a bead.

    Confirmation is a governance act: authority becomes `user_confirmed` and
    the confidence class is raised to A (canonical / operationally trusted).
    Content is never edited — beads remain immutable records.
    """
    from core_memory.persistence.store import MemoryStore

    store = MemoryStore(root=root)
    ok = store.confirm(bead_id, note=note)
    return {
        "ok": bool(ok),
        "bead_id": str(bead_id),
        "authority": "user_confirmed" if ok else None,
        "confidence_class": "A" if ok else None,
        "error": None if ok else "bead_not_found",
    }


def request_approval(root: str, bead_id: str, requested_by: str = "", note: str = "") -> dict[str, Any]:
    """Flag a bead as awaiting human review (approval_status=pending)."""
    from core_memory.persistence.store import MemoryStore

    ok = MemoryStore(root=root).request_approval(bead_id, requested_by=requested_by, note=note)
    return {
        "ok": bool(ok),
        "bead_id": str(bead_id),
        "approval_status": "pending" if ok else None,
        "error": None if ok else "bead_not_found",
    }


def approve_bead(root: str, bead_id: str, approver: str = "", note: str = "") -> dict[str, Any]:
    """Approve a bead under review: grants confidence class A, records approver.

    The richer, review-workflow form of `confirm_bead` — content is never
    edited; approval is a governance act on an immutable record.
    """
    from core_memory.persistence.store import MemoryStore

    ok = MemoryStore(root=root).approve(bead_id, approver=approver, note=note)
    return {
        "ok": bool(ok),
        "bead_id": str(bead_id),
        "approval_status": "approved" if ok else None,
        "confidence_class": "A" if ok else None,
        "error": None if ok else "bead_not_found",
    }


def reject_bead(root: str, bead_id: str, approver: str = "", reason: str = "") -> dict[str, Any]:
    """Reject a bead under review: excluded from current-truth retrieval,
    retained in the index for audit."""
    from core_memory.persistence.store import MemoryStore

    ok = MemoryStore(root=root).reject(bead_id, approver=approver, reason=reason)
    return {
        "ok": bool(ok),
        "bead_id": str(bead_id),
        "approval_status": "rejected" if ok else None,
        "error": None if ok else "bead_not_found",
    }


def list_pending_approvals(root: str, limit: int = 100) -> dict[str, Any]:
    """List beads awaiting human review (approval_status=pending)."""
    from core_memory.persistence.store import MemoryStore

    rows = MemoryStore(root=root).pending_approvals(limit=limit)
    return {"ok": True, "count": len(rows), "pending": rows}


def capture(
    turns: list[Turn | dict[str, Any]] | None = None,
    *,
    root: str = ".",
    user: str | None = None,
    assistant: str | None = None,
    as_user: str | None = None,
    as_assistant: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Top-level convenience wrapper for `Memory(root).capture(...)`."""
    return Memory(root).capture(
        turns,
        user=user,
        assistant=assistant,
        as_user=as_user,
        as_assistant=as_assistant,
        session_id=session_id,
        turn_id=turn_id,
        **kwargs,
    )

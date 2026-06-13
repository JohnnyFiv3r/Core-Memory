"""Human-in-the-loop approval workflow for beads.

A unified review gate over the bead store: a bead can be flagged `pending`,
then `approved` (a human signs off — grants confidence class A) or `rejected`
(a human deems it not memory-worthy — excluded from current-truth retrieval,
retained for audit). Beads are immutable records; approval changes governance
state and lifecycle status, never content.

This generalizes the lighter `confirm` surface: `approve` is `confirm` plus
review-workflow tracking (approver identity, pending→approved transition,
and the symmetric reject path).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core_memory.persistence import events
from core_memory.persistence.io_utils import store_lock
from core_memory.persistence.store_lifecycle_ops import (
    _append_bead_snapshot,
    raise_confidence_class_for_bead,
)
from core_memory.retrieval.lifecycle import mark_semantic_dirty
from core_memory.schema.normalization import normalize_grounding


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_bead_approval_for_store(store: Any, *, bead_id: str, requested_by: str = "", note: str = "") -> bool:
    """Flag a bead as awaiting human review (approval_status=pending).

    Pending beads remain retrievable at their current confidence class — the
    flag is a review signal, not a hard retrieval gate. Rejection removes;
    approval elevates.
    """
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(bead_id)
        if not isinstance(bead, dict):
            return False

        bead.update({"approval_status": "pending", "approval_note": str(note or "")})
        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        _append_bead_snapshot(store, bead)
        events.append_event(
            root=store.root,
            session_id=str(bead.get("session_id") or "") or None,
            event_type=events.EVENT_BEAD_APPROVAL_REQUESTED,
            payload={"bead_id": bead_id, "requested_by": str(requested_by or ""), "note": str(note or "")},
            use_lock=False,
        )
    return True


def approve_bead_for_store(store: Any, *, bead_id: str, approver: str = "", note: str = "") -> bool:
    """Approve a bead: a human signed off. Grants confidence class A and records
    the approver. Lifts a speculative bead out of speculative so A is consistent
    with the grounding ceiling."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(bead_id)
        if not isinstance(bead, dict):
            return False

        update = {
            "approval_status": "approved",
            "approved_by": str(approver or ""),
            "approved_at": _now(),
            "approval_note": str(note or ""),
            "authority": "user_confirmed",
        }
        if normalize_grounding(bead.get("grounding")) == "speculative":
            update["grounding"] = "inferred"
        bead.update(update)
        raise_confidence_class_for_bead(bead, "A")
        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        _append_bead_snapshot(store, bead)
        events.append_event(
            root=store.root,
            session_id=str(bead.get("session_id") or "") or None,
            event_type=events.EVENT_BEAD_APPROVED,
            payload={"bead_id": bead_id, "approver": str(approver or ""), "note": str(note or "")},
            use_lock=False,
        )
        mark_semantic_dirty(store.root, reason="approve")
    return True


def reject_bead_for_store(store: Any, *, bead_id: str, approver: str = "", reason: str = "") -> bool:
    """Reject a bead: a human deemed it not memory-worthy. Excluded from
    current-truth retrieval (status archived + approval_status rejected),
    retained in the index for audit. Content is never edited."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(bead_id)
        if not isinstance(bead, dict):
            return False

        bead.update({
            "approval_status": "rejected",
            "approved_by": str(approver or ""),
            "approved_at": _now(),
            "approval_note": str(reason or ""),
            "status": "archived",
        })
        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        _append_bead_snapshot(store, bead)
        events.append_event(
            root=store.root,
            session_id=str(bead.get("session_id") or "") or None,
            event_type=events.EVENT_BEAD_REJECTED,
            payload={"bead_id": bead_id, "approver": str(approver or ""), "reason": str(reason or "")},
            use_lock=False,
        )
        mark_semantic_dirty(store.root, reason="reject")
    return True


def list_pending_approvals_for_store(store: Any, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return beads awaiting review (approval_status=pending), newest first."""
    index = store._read_json(store.beads_dir / "index.json")
    rows = [
        {
            "bead_id": str(bead.get("id") or bid),
            "type": str(bead.get("type") or ""),
            "title": str(bead.get("title") or ""),
            "confidence_class": str(bead.get("confidence_class") or ""),
            "grounding": str(bead.get("grounding") or ""),
            "created_at": str(bead.get("created_at") or ""),
            "session_id": str(bead.get("session_id") or ""),
            "approval_note": str(bead.get("approval_note") or ""),
        }
        for bid, bead in (index.get("beads") or {}).items()
        if isinstance(bead, dict) and str(bead.get("approval_status") or "").lower() == "pending"
    ]
    rows.sort(key=lambda r: (r.get("created_at") or "", r.get("bead_id") or ""), reverse=True)
    return rows[: max(0, int(limit))]


__all__ = [
    "approve_bead_for_store",
    "list_pending_approvals_for_store",
    "reject_bead_for_store",
    "request_bead_approval_for_store",
]

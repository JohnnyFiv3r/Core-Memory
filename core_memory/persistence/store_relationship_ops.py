from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from core_memory.persistence import events
from core_memory.persistence.io_utils import store_lock
from core_memory.retrieval.lifecycle import mark_semantic_dirty, mark_trace_dirty


def promote_for_store(store: Any, bead_id: str, promotion_reason: Optional[str] = None) -> bool:
    """Promote a bead to long-term memory with quality gates."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")

        if bead_id not in index["beads"]:
            return False

        bead = index["beads"][bead_id]
        btype = str(bead.get("type") or "").lower()
        because = bead.get("because") or []
        detail = (bead.get("detail") or "").strip()
        has_evidence = store._has_evidence(bead)

        if btype in {"decision", "lesson", "outcome", "precedent"}:
            if btype == "decision" and not (because and (has_evidence or detail)):
                return False
            if btype == "lesson" and not because:
                return False
            if btype == "outcome":
                result = str(bead.get("result") or "").strip().lower()
                has_link = bool(str(bead.get("linked_bead_id") or "").strip()) or bool(bead.get("links"))
                if result not in {"resolved", "failed", "partial", "confirmed"}:
                    return False
                if not (has_link or has_evidence):
                    return False
            if btype == "precedent":
                if not (str(bead.get("condition") or "").strip() and str(bead.get("action") or "").strip()):
                    return False

        bead["status"] = "default"
        bead["promotion_state"] = "promoted"
        bead["promotion_locked"] = True
        bead["promoted_at"] = datetime.now(timezone.utc).isoformat()
        bead["promotion_reason"] = (promotion_reason or bead.get("promotion_reason") or "policy_auto_promote").strip()

        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        events.event_bead_promoted(store.root, bead_id, use_lock=False)
        mark_semantic_dirty(store.root, reason="promote")

        return True


def link_for_store(
    store: Any,
    *,
    source_id: str,
    target_id: str,
    relationship: str,
    explanation: str = "",
    confidence: float = 0.8,
) -> str:
    """Create a link between two beads."""
    assoc_id = f"assoc-{uuid.uuid4().hex[:12].upper()}"

    assoc = {
        "id": assoc_id,
        "type": "association",
        "source_bead": source_id,
        "target_bead": target_id,
        "relationship": relationship,
        "explanation": explanation,
        "confidence": float(confidence),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        index["associations"].append(assoc)
        index["stats"]["total_associations"] += 1
        store._write_json(store.beads_dir / "index.json", index)

        events.event_association_created(store.root, assoc, use_lock=False)
        mark_trace_dirty(store.root, reason="link")

        return assoc_id


def recall_for_store(store: Any, bead_id: str) -> bool:
    """Record a recall (strengthens association, myelination)."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")

        if bead_id not in index["beads"]:
            return False

        bead = index["beads"][bead_id]
        bead["recall_count"] = bead.get("recall_count", 0) + 1
        bead["last_recalled"] = datetime.now(timezone.utc).isoformat()

        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        events.event_bead_recalled(store.root, bead_id, use_lock=False)

        for assoc in index.get("associations", []):
            if assoc.get("source_bead") == bead_id or assoc.get("target_bead") == bead_id:
                events.event_edge_traversed(
                    store.root,
                    edge_id=assoc.get("id", ""),
                    source_bead=assoc.get("source_bead"),
                    target_bead=assoc.get("target_bead"),
                    use_lock=False,
                )

    store.track_bead_recalled(1)
    return True


def rebuild_index_for_store(store: Any) -> dict:
    return events.rebuild_index(store.root)


def stats_for_store(store: Any) -> dict:
    index = store._read_json(store.beads_dir / "index.json")

    by_type = {}
    by_status = {}
    for bead in index.get("beads", {}).values():
        t = bead.get("type", "unknown")
        s = bead.get("status", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "total_beads": len(index.get("beads", {})),
        "total_associations": len(index.get("associations", [])),
        "by_type": by_type,
        "by_status": by_status,
    }


__all__ = [
    "promote_for_store",
    "link_for_store",
    "recall_for_store",
    "rebuild_index_for_store",
    "stats_for_store",
]

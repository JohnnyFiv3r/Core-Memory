from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core_memory.persistence import events
from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.retrieval.lifecycle import mark_semantic_dirty
from core_memory.schema.normalization import (
    confidence_class_rank,
    normalize_confidence_class,
    normalize_grounding,
)


def close_store_for_store(store: Any) -> None:
    close_fn = getattr(store._backend, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def _append_bead_snapshot(store: Any, bead: dict) -> None:
    """Append the full merged bead state to its session archive.

    The visible-corpus builder merges session lines over the index projection,
    so post-write lifecycle changes must land on both surfaces or the session
    overlay silently reverts them. A FULL snapshot (never a partial delta) is
    required: events.rebuild_index() treats each session line as a complete
    bead, so a partial line would replace the original record on rebuild.
    """
    session_id = str(bead.get("session_id") or "").strip()
    if not session_id:
        return
    append_jsonl(store.beads_dir / f"session-{session_id}.jsonl", dict(bead))


def mark_bead_superseded_for_store(store: Any, *, bead_id: str, successor_id: str) -> bool:
    """Mark a bead superseded by a successor. Beads are immutable records;
    supersession closes the old version's validity window and points at the
    new one — it never rewrites content."""
    now = datetime.now(timezone.utc).isoformat()
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(bead_id)
        if not isinstance(bead, dict):
            return False

        superseded_by = [str(x) for x in (bead.get("superseded_by") or []) if str(x).strip()]
        if successor_id not in superseded_by:
            superseded_by.append(successor_id)

        bead.update({
            "status": "superseded",
            "superseded_by": superseded_by,
            "effective_to": str(bead.get("effective_to") or "") or now,
        })
        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        _append_bead_snapshot(store, bead)
        events.append_event(
            root=store.root,
            session_id=str(bead.get("session_id") or "") or None,
            event_type=events.EVENT_BEAD_SUPERSEDED,
            payload={"bead_id": bead_id, "successor_bead_id": successor_id},
            use_lock=False,
        )
        mark_semantic_dirty(store.root, reason="supersede")
    return True


def confirm_bead_for_store(store: Any, *, bead_id: str, note: str = "") -> bool:
    """Record user confirmation: authority becomes user_confirmed and the
    confidence class is raised to A (canonical / operationally trusted)."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        bead = (index.get("beads") or {}).get(bead_id)
        if not isinstance(bead, dict):
            return False

        update = {
            "authority": "user_confirmed",
            "confidence_class": "A",
        }
        # A user-confirmed bead is no longer speculative — the human has
        # grounded it. Lift the grounding so class A is consistent with the
        # speculative ceiling (which otherwise caps speculative at B).
        if normalize_grounding(bead.get("grounding")) == "speculative":
            update["grounding"] = "inferred"
        bead.update(update)
        index["beads"][bead_id] = bead
        store._write_json(store.beads_dir / "index.json", index)

        _append_bead_snapshot(store, bead)
        events.append_event(
            root=store.root,
            session_id=str(bead.get("session_id") or "") or None,
            event_type=events.EVENT_BEAD_CONFIRMED,
            payload={"bead_id": bead_id, "note": str(note or "")},
            use_lock=False,
        )
        mark_semantic_dirty(store.root, reason="confirm")

    # Best-effort myelination reward over the confirmed bead's supporting edges.
    try:
        from core_memory.persistence.myelination_rewards import reward_for_bead_decision

        reward_for_bead_decision(
            store.root,
            bead_id=bead_id,
            polarity="positive",
            source_type="human_approval",
            source_event_id=str(bead_id),
            reason="bead confirmed",
        )
    except Exception:
        pass
    return True


def raise_confidence_class_for_bead(bead: dict, floor: str) -> bool:
    """Raise a bead dict's confidence class to at least `floor`. Returns True
    when the class changed. Monotonic — never lowers an existing class."""
    current = normalize_confidence_class(bead.get("confidence_class"))
    target = normalize_confidence_class(floor)
    if confidence_class_rank(target) > confidence_class_rank(current):
        bead["confidence_class"] = target
        return True
    return False


__all__ = [
    "close_store_for_store",
    "confirm_bead_for_store",
    "mark_bead_superseded_for_store",
    "raise_confidence_class_for_bead",
]

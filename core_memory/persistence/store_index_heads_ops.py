from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def read_heads_for_store(store: Any) -> dict:
    heads_file = store.beads_dir / "heads.json"
    if not heads_file.exists():
        return {"topics": {}, "goals": {}, "updated_at": datetime.now(timezone.utc).isoformat()}
    return store._read_json(heads_file)


def write_heads_for_store(store: Any, heads: dict) -> None:
    heads["updated_at"] = datetime.now(timezone.utc).isoformat()
    store._write_json(store.beads_dir / "heads.json", heads)


def update_heads_for_bead_for_store(store: Any, heads: dict, bead: dict) -> dict:
    topic_id = (bead.get("topic_id") or "").strip() if isinstance(bead.get("topic_id"), str) else ""
    goal_id = (bead.get("goal_id") or "").strip() if isinstance(bead.get("goal_id"), str) else ""
    bead_id = bead.get("id")
    if topic_id and bead_id:
        heads.setdefault("topics", {})[topic_id] = {
            "bead_id": bead_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    if goal_id and bead_id:
        heads.setdefault("goals", {})[goal_id] = {
            "bead_id": bead_id,
            "goal_status": bead.get("goal_status") or bead.get("status") or "default",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    return heads


def update_index_for_store(store: Any, bead: dict) -> None:
    index_file = store.beads_dir / "index.json"
    index = store._read_json(index_file)
    index["beads"][bead["id"]] = bead
    index["stats"]["total_beads"] = len(index["beads"])
    store._write_json(index_file, index)


__all__ = [
    "read_heads_for_store",
    "write_heads_for_store",
    "update_heads_for_bead_for_store",
    "update_index_for_store",
]

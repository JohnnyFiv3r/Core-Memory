from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.persistence.store import MemoryStore
from core_memory.runtime.turn_archive import find_turn_record, rebuild_all_indexes


def rebuild_turn_indexes(*, root: str) -> dict[str, Any]:
    return rebuild_all_indexes(root=Path(root))


def backfill_bead_session_ids(*, root: str) -> dict[str, Any]:
    """Backfill missing bead session_id values using source_turn_ids.

    Resolution order:
      1) explicit session_id if present
      2) infer from first resolvable source_turn_id
      3) fallback to "unknown"
    """
    store = MemoryStore(root=root)
    idx_path = store.beads_dir / "index.json"
    idx = store._read_json(idx_path)
    beads = idx.get("beads") or {}

    updated = 0
    unknown = 0
    for bid, bead in beads.items():
        if not isinstance(bead, dict):
            continue
        current = str(bead.get("session_id") or "").strip()
        if current:
            continue

        resolved = ""
        for tid in (bead.get("source_turn_ids") or []):
            t = str(tid or "").strip()
            if not t:
                continue
            row = find_turn_record(root=Path(root), turn_id=t, session_id=None)
            if isinstance(row, dict):
                resolved = str(row.get("session_id") or "").strip()
                if resolved:
                    break

        if not resolved:
            resolved = "unknown"
            unknown += 1

        bead["session_id"] = resolved
        beads[bid] = bead
        updated += 1

    idx["beads"] = beads
    store._write_json(idx_path, idx)
    return {"ok": True, "updated": updated, "unknown": unknown}


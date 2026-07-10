from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from core_memory.persistence.archive_index import append_archive_snapshot, read_snapshot, rebuild_archive_index
from core_memory.persistence.io_utils import store_lock
from core_memory.schema.promotion import summary_truncation_limit
from core_memory.persistence.semantic_lifecycle import mark_semantic_dirty


def compact_for_store(
    store: Any,
    *,
    session_id: Optional[str] = None,
    promote: bool = False,
    only_bead_ids: Optional[list[str]] = None,
    skip_bead_ids: Optional[list[str]] = None,
    force_archive_all: bool = False,
) -> dict:
    """Core-native compact: archive detail text losslessly and optionally promote."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        compacted = 0
        only = set(only_bead_ids or [])
        skip = set(skip_bead_ids or [])

        for bead_id in sorted(index.get("beads", {}).keys()):
            bead = index["beads"][bead_id]
            if session_id and bead.get("session_id") != session_id:
                continue
            if only and bead_id not in only:
                continue
            if bead_id in skip:
                continue

            bead_type = str(bead.get("type", "")).lower()
            bead_status = str(bead.get("status", "")).lower()
            bead_pstate = str(bead.get("promotion_state") or "").lower()
            is_session_boundary = bead_type in {"session_start", "session_end"}
            is_promoted = bool(bead.get("promoted")) or bead_pstate == "promoted" or bead_status == "promoted"

            if (not force_archive_all) and (bool(bead.get("promotion_candidate")) or bead_pstate == "candidate" or bead_status == "candidate"):
                index["beads"][bead_id] = bead
                continue

            should_archive = force_archive_all or (not is_promoted and not is_session_boundary)
            if should_archive:
                already_archived = str(bead.get("status") or "").lower() == "archived"
                has_ptr = isinstance(bead.get("archive_ptr"), dict) and bool((bead.get("archive_ptr") or {}).get("revision_id"))
                has_detail = bool((bead.get("detail") or "").strip())
                if not (already_archived and has_ptr and not has_detail):
                    revision_id = f"rev-{uuid.uuid4().hex[:12]}"
                    archive = {
                        "bead_id": bead_id,
                        "revision_id": revision_id,
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                        "archived_from_status": bead.get("status"),
                        "snapshot": dict(bead),
                    }
                    append_archive_snapshot(store.root, archive)
                    bead["archive_ptr"] = {"revision_id": revision_id}

                    bead["detail"] = ""
                    bead["summary"] = (bead.get("summary") or [])[:summary_truncation_limit(bead.get("type"))]
                    bead["status"] = "archived"
                    compacted += 1

            index["beads"][bead_id] = bead

        store._write_json(store.beads_dir / "index.json", index)
        mark_semantic_dirty(store.root, reason="compact")
        return {
            "ok": True,
            "compacted": compacted,
            "session": session_id,
            "only_bead_ids": len(only),
            "skip_bead_ids": len(skip),
            "force_archive_all": bool(force_archive_all),
            "promotion_apply_ignored": bool(promote and getattr(store, "auto_promote_on_compact", False)),
        }


def uncompact_for_store(store: Any, bead_id: str) -> dict:
    """Restore compacted bead detail from append-only archive revisions."""
    with store_lock(store.root):
        index = store._read_json(store.beads_dir / "index.json")
        if bead_id not in index.get("beads", {}):
            return {"ok": False, "error": f"Bead not found: {bead_id}"}

        bead = index["beads"][bead_id]
        wanted_rev = (bead.get("archive_ptr") or {}).get("revision_id") if isinstance(bead.get("archive_ptr"), dict) else None

        found = read_snapshot(store.root, str(wanted_rev or "")) if wanted_rev else None

        if not found:
            archive_file = store.beads_dir / "archive.jsonl"
            if not archive_file.exists():
                return {"ok": False, "error": f"Bead not found in archive: {bead_id}"}
            with open(archive_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if row.get("bead_id") != bead_id:
                        continue
                    if wanted_rev and row.get("revision_id") != wanted_rev:
                        continue
                    found = row

            if wanted_rev and not read_snapshot(store.root, str(wanted_rev or "")):
                rebuild_archive_index(store.root)

        if not found:
            return {"ok": False, "error": f"Bead not found in archive: {bead_id}"}

        snapshot = found.get("snapshot") if isinstance(found.get("snapshot"), dict) else None
        if snapshot:
            restored = dict(snapshot)
            restored["status"] = "open" if bead.get("status") == "archived" else bead.get("status")
            restored["uncompacted_at"] = datetime.now(timezone.utc).isoformat()
            index["beads"][bead_id] = restored
        else:
            bead["detail"] = found.get("detail", "")
            if found.get("summary"):
                bead["summary"] = found.get("summary")
            if bead.get("status") == "archived":
                bead["status"] = "open"
            bead["uncompacted_at"] = datetime.now(timezone.utc).isoformat()
            index["beads"][bead_id] = bead

        store._write_json(store.beads_dir / "index.json", index)
        mark_semantic_dirty(store.root, reason="uncompact")
        return {"ok": True, "id": bead_id, "revision_id": found.get("revision_id")}


def myelinate_for_store(store: Any, apply: bool = False) -> dict:
    """Core-native myelination scaffold (deterministic)."""
    index = store._read_json(store.beads_dir / "index.json")
    actions = []
    for bead_id in sorted(index.get("beads", {}).keys()):
        bead = index["beads"][bead_id]
        if bead.get("recall_count", 0) >= 3:
            actions.append({"bead_id": bead_id, "action": "retain"})

    return {
        "dry_run": not apply,
        "total_derived_edges": 0,
        "edges_with_actions": len(actions),
        "actions": actions[:50],
    }


__all__ = ["compact_for_store", "uncompact_for_store", "myelinate_for_store"]

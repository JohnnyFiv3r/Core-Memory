from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core_memory.persistence.io_utils import store_lock
from core_memory.runtime.session_surface import read_session_surface


def rebuild_index_projection_from_sessions_for_store(store: Any) -> dict:
    """Rebuild index projection from session/global JSONL surfaces.

    Authority model: session/global files are source; index is projection cache.
    Associations are preserved from existing index projection.
    """
    with store_lock(store.root):
        index_file = store.beads_dir / "index.json"
        existing = store._read_json(index_file)
        associations = list(existing.get("associations") or [])

        beads = {}
        for p in sorted(store.beads_dir.glob("session-*.jsonl")):
            for row in read_session_surface(store.root, p.stem.replace("session-", "")):
                bid = str((row or {}).get("id") or "")
                if bid:
                    beads[bid] = row

        global_file = store.beads_dir / "global.jsonl"
        if global_file.exists():
            for line in global_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                bid = str((row or {}).get("id") or "")
                if bid:
                    beads[bid] = row

        out = {
            "beads": beads,
            "associations": associations,
            "stats": {
                "total_beads": len(beads),
                "total_associations": len(associations),
                "created_at": str((existing.get("stats") or {}).get("created_at") or datetime.now(timezone.utc).isoformat()),
            },
            "projection": {
                "mode": "session_first_projection_cache",
                "rebuilt_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        store._write_json(index_file, out)
        return {
            "ok": True,
            "mode": "session_first_projection_cache",
            "total_beads": len(beads),
            "total_associations": len(associations),
        }


__all__ = ["rebuild_index_projection_from_sessions_for_store"]

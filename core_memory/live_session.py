from __future__ import annotations

import os
from typing import Any

from .runtime.session_surface import read_session_surface
from .persistence.store import MemoryStore


def _allow_index_fallback() -> bool:
    return str(os.getenv("CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def read_live_session_beads(root: str, session_id: str) -> dict[str, Any]:
    """Session-first live memory read.

    Authority order:
    - Primary: append-only session surface file
    - Optional fallback: index projection (compatibility mode only)

    Compatibility fallback is gated by:
      CORE_MEMORY_LIVE_SESSION_ALLOW_INDEX_FALLBACK=1
    """
    session_rows = read_session_surface(root, session_id)
    if session_rows:
        return {
            "authority": "session_surface",
            "beads": session_rows,
            "count": len(session_rows),
        }

    if not _allow_index_fallback():
        return {
            "authority": "session_surface_empty",
            "beads": [],
            "count": 0,
        }

    # Compatibility fallback only when explicitly enabled
    store = MemoryStore(root)
    idx = store._read_json(store.beads_dir / "index.json")
    beads = [b for b in (idx.get("beads") or {}).values() if str(b.get("session_id") or "") == str(session_id)]
    beads = sorted(beads, key=lambda b: str(b.get("created_at") or ""), reverse=False)
    return {
        "authority": "index_fallback",
        "beads": beads,
        "count": len(beads),
    }

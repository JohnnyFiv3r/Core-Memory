from __future__ import annotations

from typing import Any

from .session_surface import read_session_surface
from .store import MemoryStore


def read_live_session_beads(root: str, session_id: str) -> dict[str, Any]:
    """Session-first live memory read.

    Foundation for P6A authority cutover:
    - Primary: append-only session surface file
    - Fallback: index projection for robustness
    """
    session_rows = read_session_surface(root, session_id)
    if session_rows:
        return {
            "authority": "session_surface",
            "beads": session_rows,
            "count": len(session_rows),
        }

    # Fallback only when session surface has no rows
    store = MemoryStore(root)
    idx = store._read_json(store.beads_dir / "index.json")
    beads = [b for b in (idx.get("beads") or {}).values() if str(b.get("session_id") or "") == str(session_id)]
    beads = sorted(beads, key=lambda b: str(b.get("created_at") or ""), reverse=False)
    return {
        "authority": "index_fallback",
        "beads": beads,
        "count": len(beads),
    }

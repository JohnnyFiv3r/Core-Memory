from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core_memory.persistence.io_utils import store_lock


def init_index_for_store(store: Any) -> None:
    """Initialize index + heads projection files when missing."""
    index_file = store.beads_dir / "index.json"
    heads_file = store.beads_dir / "heads.json"
    with store_lock(store.root):
        if not index_file.exists():
            store._write_json(
                index_file,
                {
                    "beads": {},
                    "associations": [],
                    "entities": {},
                    "entity_aliases": {},
                    "stats": {
                        "total_beads": 0,
                        "total_associations": 0,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "projection": {
                        "mode": "session_first_projection_cache",
                        "rebuilt_at": None,
                    },
                },
            )
        if not heads_file.exists():
            store._write_json(
                heads_file,
                {
                    "topics": {},
                    "goals": {},
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )


__all__ = ["init_index_for_store"]

"""Durable read-side storage for the junction roadmap projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import atomic_write_json, store_lock

JUNCTION_ROADMAP_SCHEMA = "core_memory.junction_roadmap.v1"


def junction_roadmap_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "junction-roadmap.json"


def read_junction_roadmap(
    root: str | Path,
    *,
    include_graph: bool = True,
) -> dict[str, Any]:
    """Read the latest persisted roadmap without rebuilding it inline."""

    path = junction_roadmap_path(root)
    if not path.exists():
        return {
            "ok": True,
            "present": False,
            "schema_version": JUNCTION_ROADMAP_SCHEMA,
            "status": "missing",
            "manifest_path": str(path),
            "roadmap_meta": {
                "vertex_count": 0,
                "edge_count": 0,
                "alternative_count": 0,
            },
            "limitations": ["junction_roadmap_not_built"],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "ok": False,
            "present": False,
            "schema_version": JUNCTION_ROADMAP_SCHEMA,
            "status": "unreadable",
            "manifest_path": str(path),
            "error": "junction_roadmap_unreadable",
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "present": False,
            "schema_version": JUNCTION_ROADMAP_SCHEMA,
            "status": "unreadable",
            "manifest_path": str(path),
            "error": "junction_roadmap_invalid_payload",
        }
    out = {"ok": True, "present": True, **payload, "manifest_path": str(path)}
    if not include_graph:
        out.pop("vertices", None)
        out.pop("edges", None)
    return out


def write_junction_roadmap(root: str | Path, roadmap: dict[str, Any]) -> Path:
    """Atomically replace the projection while holding the store lock."""

    path = junction_roadmap_path(root)
    with store_lock(Path(root)):
        atomic_write_json(path, roadmap)
    return path


__all__ = [
    "JUNCTION_ROADMAP_SCHEMA",
    "junction_roadmap_path",
    "read_junction_roadmap",
    "write_junction_roadmap",
]

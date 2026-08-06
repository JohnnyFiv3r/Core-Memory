"""Retrieval-facing junction roadmap build and read operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.graph.roadmap import (
    ROADMAP_WATERSHED_ATTRIBUTION_SCHEMA,
    build_junction_roadmap,
    roadmap_input_revision,
    roadmap_watershed_attribution,
)
from core_memory.persistence.junction_roadmap import read_junction_roadmap
from core_memory.retrieval.junctions import derive_junction_projection


def refresh_junction_roadmap(root: str | Path, **options: Any) -> dict[str, Any]:
    """Build the persisted roadmap from canonical graph projections."""

    projection = derive_junction_projection(root, include_beads=True)
    return build_junction_roadmap(
        root,
        projection=projection,
        persist=True,
        **options,
    )


def junction_roadmap_status(
    root: str | Path,
    *,
    include_graph: bool = False,
) -> dict[str, Any]:
    """Return the persisted projection or compact metadata-only status."""

    out = read_junction_roadmap(root, include_graph=include_graph)
    if not bool(out.get("present")):
        return out
    current_revision = roadmap_input_revision(root)
    stored_revision = str(out.get("input_revision") or "")
    out["current_input_revision"] = current_revision
    out["stale"] = not stored_revision or stored_revision != current_revision
    if out["stale"]:
        limitations = [str(value) for value in out.get("limitations") or [] if str(value)]
        limitations.append("junction_roadmap_inputs_changed")
        out["limitations"] = sorted(set(limitations))
    return out


def junction_roadmap_attribution(
    root: str | Path,
    *,
    terminal_junction_ids: list[str] | None = None,
    max_depth: int = 4,
    max_junctions: int = 8,
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return read-only upstream influence over the persisted roadmap."""

    roadmap = junction_roadmap_status(root, include_graph=True)
    if not bool(roadmap.get("ok")):
        return {
            "ok": False,
            "present": False,
            "schema_version": ROADMAP_WATERSHED_ATTRIBUTION_SCHEMA,
            "status": roadmap.get("status") or "unavailable",
            "error": roadmap.get("error") or "junction_roadmap_unavailable",
            "limitations": list(roadmap.get("limitations") or []),
        }
    if not bool(roadmap.get("present")):
        return {
            "ok": True,
            "present": False,
            "schema_version": ROADMAP_WATERSHED_ATTRIBUTION_SCHEMA,
            "status": roadmap.get("status") or "missing",
            "root_junctions": [],
            "influence_breakdown": [],
            "paths": [],
            "roadmap_meta": dict(roadmap.get("roadmap_meta") or {}),
            "limitations": list(roadmap.get("limitations") or []),
        }
    out = roadmap_watershed_attribution(
        roadmap,
        terminal_junction_ids=terminal_junction_ids,
        max_depth=max_depth,
        max_junctions=max_junctions,
        allowed_source_ids=allowed_source_ids,
        denied_source_ids=denied_source_ids,
    )
    out["status"] = roadmap.get("status") or out.get("status") or "ready"
    out["stale"] = bool(roadmap.get("stale"))
    out["current_input_revision"] = roadmap.get("current_input_revision")
    out["manifest_path"] = roadmap.get("manifest_path")
    return out


__all__ = ["junction_roadmap_attribution", "junction_roadmap_status", "refresh_junction_roadmap"]

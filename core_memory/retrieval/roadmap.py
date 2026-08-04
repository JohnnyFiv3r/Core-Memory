"""Retrieval-facing junction roadmap build and read operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.graph.roadmap import build_junction_roadmap, roadmap_input_revision
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


__all__ = ["junction_roadmap_status", "refresh_junction_roadmap"]

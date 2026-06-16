"""Dreamer V3 — continuity-geometry projection (PRD §16.1).

A read-only **projection surface**: the data layer behind a memory-explorer
visualization (radial "continuity geometry" — mass / wells / curvature readouts,
per ``docs/reports/emergent-geometry-substrate.md``). It is owned by Dreamer V3
because Dreamer owns the depth measure, and it is **decoupled from the v1
critical path** — SOUL, scientific findings, and reinforcement do not depend on
it. A host builds the geometry view only if it wants a memory-explorer surface.

The manifest exposes exactly the substrate fields a renderer needs, without
forking any formula:

- nodes (beads): ``id``, ``type``, ``status``, ``assembly_depth``
- edges: ``src``, ``dst``, ``rel``, ``strength``, ``provenance``

``assembly_depth`` reuses ``compute_assembly_depth`` over the all-bead
population; edge ``strength`` reuses the myelination edge-bonus map. The manifest
is **built on the Dreamer cadence and served from disk** — never recomputed on
read (§16.1), so the explorer surface stays cheap and the formulas stay
single-sourced.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.schema.normalization import normalize_relation_type

GEOMETRY_SCHEMA = "dreamer_geometry_manifest.v1"
_INACTIVE_ASSOC_STATUSES = {"retracted", "superseded", "inactive"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "dreamer-geometry.json"


def _read_index(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        out = json.loads(p.read_text(encoding="utf-8"))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def build_geometry_manifest(root: str | Path, *, limit: int = 5000) -> dict[str, Any]:
    """Compute the continuity-geometry manifest and persist it to disk.

    Nodes are every bead (id/type/status/assembly_depth); edges are every
    *active* association (src/dst/rel/strength/provenance). Returns the manifest.
    Best-effort: missing depth or myelination data degrades to 0.0, never raises.
    """
    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}

    # Assembly depth across the whole population (reuse Dreamer's depth measure).
    depth_by_bead: dict[str, float] = {}
    try:
        from core_memory.runtime.dreamer.assembly_depth import compute_assembly_depth

        rep = compute_assembly_depth(root, target_kind="*", limit=max(1, int(limit)))
        for r in rep.get("reports") or []:
            depth_by_bead[str(r.get("target_id") or "")] = float(r.get("score") or 0.0)
    except Exception:
        depth_by_bead = {}

    # Edge strength from the myelination edge-bonus map (reuse, don't fork).
    edge_bonus: dict[str, float] = {}
    try:
        from core_memory.runtime.observability.myelination import compute_myelination_bonus_map

        edge_bonus = dict((compute_myelination_bonus_map(root).get("bonus_by_edge_key") or {}))
    except Exception:
        edge_bonus = {}

    nodes: list[dict[str, Any]] = []
    for bid, b in beads.items():
        nodes.append({
            "id": bid,
            "type": str(b.get("type") or ""),
            "status": str(b.get("status") or "active"),
            "assembly_depth": round(float(depth_by_bead.get(bid, 0.0)), 6),
        })
    nodes.sort(key=lambda n: (-float(n["assembly_depth"]), str(n["id"])))

    edges: list[dict[str, Any]] = []
    for assoc in (index.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        if str(assoc.get("status") or "active").strip().lower() in _INACTIVE_ASSOC_STATUSES:
            continue
        s = str(assoc.get("source_bead") or "").strip()
        d = str(assoc.get("target_bead") or "").strip()
        if not s or not d:
            continue
        rel = normalize_relation_type(assoc.get("relationship"))
        strength = float(edge_bonus.get(f"{s}|{rel}|{d}", 0.0))
        edges.append({
            "src": s,
            "dst": d,
            "rel": rel,
            "strength": round(strength, 6),
            "provenance": str(assoc.get("provenance") or assoc.get("source") or "association"),
        })
    edges.sort(key=lambda e: (-float(e["strength"]), str(e["src"]), str(e["dst"])))

    manifest = {
        "schema": GEOMETRY_SCHEMA,
        "generated_at": _now(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }

    p = _manifest_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def read_geometry_manifest(root: str | Path) -> dict[str, Any]:
    """Serve the geometry manifest from disk (never recomputed on read, §16.1).

    Returns the persisted manifest, or an empty manifest with ``present=False``
    when none has been built yet — the host should trigger a Dreamer run.
    """
    p = _manifest_path(root)
    if not p.exists():
        return {
            "ok": True,
            "present": False,
            "schema": GEOMETRY_SCHEMA,
            "node_count": 0,
            "edge_count": 0,
            "nodes": [],
            "edges": [],
            "note": "no geometry manifest yet; run a dreamer-run to build it",
        }
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            return {"ok": True, "present": True, **manifest}
    except Exception:
        pass
    return {
        "ok": False,
        "present": False,
        "error": "geometry_manifest_unreadable",
        "schema": GEOMETRY_SCHEMA,
    }


__all__ = [
    "GEOMETRY_SCHEMA",
    "build_geometry_manifest",
    "read_geometry_manifest",
]

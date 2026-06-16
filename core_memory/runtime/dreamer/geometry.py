"""Dreamer V3 — continuity-geometry projection (PRD §16.1).

A read-only **projection surface**: the data layer behind a memory-explorer
visualization (radial "continuity geometry" — mass / wells / curvature readouts,
per ``docs/reports/emergent-geometry-substrate.md``). It is owned by Dreamer V3
because Dreamer owns the depth measure, and it is **decoupled from the v1
critical path** — SOUL, scientific findings, and reinforcement do not depend on
it. A host builds the geometry view only if it wants a memory-explorer surface.

The v2 manifest exposes substrate fields plus lightweight display metadata,
without forking any formula:

- nodes (beads): ``id``, ``type``, ``status``, ``assembly_depth``,
  ``title``, ``created_at``, ``timestamp``, ``entities``
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

GEOMETRY_SCHEMA_V1 = "dreamer_geometry_manifest.v1"
GEOMETRY_SCHEMA = "dreamer_geometry_manifest.v2"
GEOMETRY_NODE_SHAPE_VERSION_V1 = "geometry_node.v1"
GEOMETRY_NODE_SHAPE_VERSION = "geometry_node.v2"
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


def _node_shape_version_for_manifest(manifest: dict[str, Any]) -> str:
    explicit = str(manifest.get("node_shape_version") or "").strip()
    if explicit:
        return explicit
    schema = str(manifest.get("schema") or "").strip()
    if schema == GEOMETRY_SCHEMA_V1:
        return GEOMETRY_NODE_SHAPE_VERSION_V1
    return GEOMETRY_NODE_SHAPE_VERSION


def build_geometry_manifest(root: str | Path, *, limit: int = 5000) -> dict[str, Any]:
    """Compute the continuity-geometry manifest and persist it to disk.

    Nodes are beads (id/type/status/assembly_depth plus display metadata);
    edges are *active* associations between emitted nodes
    (src/dst/rel/strength/provenance). When a store has more than ``limit``
    beads the manifest is capped to the first ``limit`` (``truncated=True``,
    ``total_bead_count`` reported) — depth scoring and the emitted node set use
    the *same* capped population, so no node ever carries a placeholder depth
    and no edge dangles. Returns the manifest. Best-effort: missing
    myelination data degrades edge strength to 0.0.
    """
    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}

    # Cap nodes and depth scoring on the *same* bead set so every emitted node
    # carries a real depth — never a silent 0.0 for a bead past the cap.
    # compute_assembly_depth selects the first `limit` ids of the same index, so
    # the scored set and this emitted set are identical.
    cap = max(1, int(limit))
    target_ids = list(beads.keys())[:cap]
    target_set = set(target_ids)
    truncated = len(beads) > len(target_ids)

    # Assembly depth across the emitted population (reuse Dreamer's depth measure).
    depth_by_bead: dict[str, float] = {}
    try:
        from core_memory.runtime.dreamer.assembly_depth import compute_assembly_depth

        rep = compute_assembly_depth(root, target_kind="*", limit=cap)
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
    for bid in target_ids:
        b = beads[bid]
        timestamp = str(b.get("created_at") or b.get("updated_at") or b.get("effective_at") or "")
        nodes.append({
            "id": bid,
            "type": str(b.get("type") or ""),
            "status": str(b.get("status") or "active"),
            "assembly_depth": round(float(depth_by_bead.get(bid, 0.0)), 6),
            "title": str(b.get("title") or ""),
            "created_at": timestamp,
            "timestamp": timestamp,
            "entities": [str(x) for x in (b.get("entities") or []) if str(x).strip()] if isinstance(b.get("entities"), list) else [],
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
        # Only edges whose endpoints are both emitted nodes — no dangling refs.
        if s not in target_set or d not in target_set:
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
        "node_shape_version": GEOMETRY_NODE_SHAPE_VERSION,
        "generated_at": _now(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "total_bead_count": len(beads),
        "truncated": truncated,
        "limit": cap,
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
            "node_shape_version": GEOMETRY_NODE_SHAPE_VERSION,
            "node_count": 0,
            "edge_count": 0,
            "nodes": [],
            "edges": [],
            "note": "no geometry manifest yet; run a dreamer-run to build it",
        }
    try:
        manifest = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            out = {"ok": True, "present": True, **manifest}
            out.setdefault("node_shape_version", _node_shape_version_for_manifest(manifest))
            if out.get("schema") == GEOMETRY_SCHEMA_V1:
                out.setdefault("legacy_node_shape", True)
            return out
    except Exception:
        pass
    return {
        "ok": False,
        "present": False,
        "error": "geometry_manifest_unreadable",
        "schema": GEOMETRY_SCHEMA,
        "node_shape_version": GEOMETRY_NODE_SHAPE_VERSION,
    }


__all__ = [
    "GEOMETRY_NODE_SHAPE_VERSION",
    "GEOMETRY_NODE_SHAPE_VERSION_V1",
    "GEOMETRY_SCHEMA",
    "GEOMETRY_SCHEMA_V1",
    "build_geometry_manifest",
    "read_geometry_manifest",
]

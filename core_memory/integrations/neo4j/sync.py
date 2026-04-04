from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .client import Neo4jClient
from .config import Neo4jConfig
from .mapper import bead_to_node, association_to_edge


def neo4j_status(*, config: Neo4jConfig | None = None) -> dict[str, Any]:
    cfg = config or Neo4jConfig.from_env()
    out = Neo4jClient(cfg).status()
    out.setdefault("config", cfg.redacted())
    return out


def sync_to_neo4j(
    root: str,
    *,
    session_id: str | None = None,
    bead_ids: list[str] | None = None,
    prune: bool = False,
    dry_run: bool = False,
    config: Neo4jConfig | None = None,
) -> dict[str, Any]:
    """Projection-only sync surface.

    Slice 1 scaffold behavior:
    - collects a deterministic local projection payload
    - supports dry-run planning output
    - does not yet perform remote upserts (implemented in Slice 3)
    """
    cfg = config or Neo4jConfig.from_env()
    projection = _collect_projection(root=root, session_id=session_id, bead_ids=bead_ids)

    if not cfg.enabled and not dry_run:
        return {
            "ok": False,
            "database": cfg.database,
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "nodes_pruned": 0,
            "edges_pruned": 0,
            "warnings": ["neo4j_disabled"],
            "errors": [
                {
                    "code": "neo4j_disabled",
                    "message": "Set CORE_MEMORY_NEO4J_ENABLED=1 to run Neo4j sync.",
                }
            ],
        }

    if dry_run:
        return {
            "ok": True,
            "mode": "dry_run",
            "database": cfg.database,
            "nodes_planned": len(projection["nodes"]),
            "edges_planned": len(projection["edges"]),
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "nodes_pruned": 0,
            "edges_pruned": 0,
            "warnings": ["neo4j_sync_dry_run_only"],
            "errors": [],
        }

    status = neo4j_status(config=cfg)
    if not status.get("ok"):
        return {
            "ok": False,
            "database": cfg.database,
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "nodes_pruned": 0,
            "edges_pruned": 0,
            "warnings": list(status.get("warnings") or []),
            "errors": [status.get("error") or {"code": "neo4j_unavailable", "message": "neo4j unavailable"}],
        }

    return {
        "ok": False,
        "database": cfg.database,
        "nodes_upserted": 0,
        "edges_upserted": 0,
        "nodes_pruned": 0,
        "edges_pruned": 0,
        "warnings": [],
        "errors": [
            {
                "code": "neo4j_sync_not_implemented",
                "message": "Neo4j upsert execution is implemented in a later slice. Use --dry-run for now.",
            }
        ],
    }


def _collect_projection(root: str, *, session_id: str | None, bead_ids: list[str] | None) -> dict[str, list[dict[str, Any]]]:
    idx_file = Path(root) / ".beads" / "index.json"
    if not idx_file.exists():
        return {"nodes": [], "edges": []}

    try:
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return {"nodes": [], "edges": []}

    beads = {str(k): dict(v) for k, v in ((idx.get("beads") or {}).items()) if isinstance(v, dict)}
    bead_id_filter = {str(x) for x in (bead_ids or []) if str(x).strip()}

    selected: dict[str, dict[str, Any]] = {}
    for bid, bead in beads.items():
        if bead_id_filter and bid not in bead_id_filter:
            continue
        if session_id and str(bead.get("session_id") or "") != str(session_id):
            continue
        row = dict(bead)
        row.setdefault("id", bid)
        selected[bid] = row

    nodes = [bead_to_node(b) for b in selected.values()]

    edges: list[dict[str, Any]] = []
    for assoc in list(idx.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        edge = association_to_edge(assoc)
        src = str(edge.get("start_bead_id") or "")
        dst = str(edge.get("end_bead_id") or "")
        if src in selected and dst in selected:
            edges.append(edge)

    return {"nodes": nodes, "edges": edges}

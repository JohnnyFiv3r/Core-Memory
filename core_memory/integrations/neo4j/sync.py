from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .client import Neo4jClient
from .config import Neo4jConfig
from .mapper import (
    EDGE_MODE_ASSOCIATED,
    NODE_LABEL_MODE_BEAD_PLUS_TYPE,
    association_to_edge,
    bead_to_node,
)


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
    node_label_mode: str | None = None,
    edge_mode: str | None = None,
    config: Neo4jConfig | None = None,
) -> dict[str, Any]:
    """Projection-only sync surface (idempotent upsert path)."""
    cfg = config or Neo4jConfig.from_env()
    node_label_mode_final = _resolve_node_label_mode(node_label_mode=node_label_mode, config=cfg)
    edge_mode_final = _resolve_edge_mode(edge_mode=edge_mode, config=cfg)
    dataset_key = _resolve_dataset_key(root=root, config=cfg)
    projection = _collect_projection(
        root=root,
        session_id=session_id,
        bead_ids=bead_ids,
        node_label_mode=node_label_mode_final,
        edge_mode=edge_mode_final,
    )
    prune_keep_assoc_ids = _collect_prune_keep_assoc_ids(root=root, session_id=session_id, bead_ids=bead_ids)
    projection, dedupe_warnings = _dedupe_projection(projection)

    if not cfg.enabled and not dry_run:
        return {
            "ok": False,
            "database": cfg.database,
            "dataset_key": dataset_key,
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
            "dataset_key": dataset_key,
            "nodes_planned": len(projection["nodes"]),
            "edges_planned": len(projection["edges"]),
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "nodes_pruned": 0,
            "edges_pruned": 0,
            "warnings": ["neo4j_sync_dry_run_only", *dedupe_warnings],
            "errors": [],
        }

    try:
        out = Neo4jClient(cfg).upsert_projection(
            nodes=list(projection.get("nodes") or []),
            edges=list(projection.get("edges") or []),
            prune=bool(prune),
            dataset_key=dataset_key,
            keep_assoc_ids=list(prune_keep_assoc_ids),
            scope={
                "session_id": session_id,
                "bead_ids": [str(x) for x in (bead_ids or []) if str(x).strip()],
                "dataset_key": dataset_key,
                "node_label_mode": node_label_mode_final,
                "edge_mode": edge_mode_final,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "database": cfg.database,
            "dataset_key": dataset_key,
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "nodes_pruned": 0,
            "edges_pruned": 0,
            "warnings": list(dedupe_warnings),
            "errors": [{"code": "neo4j_sync_failed", "message": str(exc)}],
        }

    out.setdefault("database", cfg.database)
    out.setdefault("dataset_key", dataset_key)
    out.setdefault("nodes_upserted", 0)
    out.setdefault("edges_upserted", 0)
    out.setdefault("nodes_pruned", 0)
    out.setdefault("edges_pruned", 0)
    out.setdefault("warnings", [])
    out.setdefault("errors", [])
    out["warnings"] = list(out.get("warnings") or []) + list(dedupe_warnings)
    return out


def _resolve_dataset_key(*, root: str, config: Neo4jConfig) -> str:
    explicit = str(getattr(config, "dataset", "") or "").strip()
    if explicit:
        return explicit
    try:
        resolved = str(Path(root).resolve())
    except Exception:
        resolved = str(Path(root))
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]
    return f"root-{digest}"


def _resolve_node_label_mode(*, node_label_mode: str | None, config: Neo4jConfig) -> str:
    mode = str(node_label_mode or getattr(config, "node_label_mode", NODE_LABEL_MODE_BEAD_PLUS_TYPE) or "").strip().lower()
    if mode not in {"bead_plus_type", "type_only"}:
        return NODE_LABEL_MODE_BEAD_PLUS_TYPE
    return mode


def _resolve_edge_mode(*, edge_mode: str | None, config: Neo4jConfig) -> str:
    mode = str(edge_mode or getattr(config, "edge_mode", EDGE_MODE_ASSOCIATED) or "").strip().lower()
    if mode not in {"associated", "typed"}:
        return EDGE_MODE_ASSOCIATED
    return mode


def _collect_projection(
    root: str,
    *,
    session_id: str | None,
    bead_ids: list[str] | None,
    node_label_mode: str,
    edge_mode: str,
) -> dict[str, list[dict[str, Any]]]:
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

    nodes = [bead_to_node(b, label_mode=node_label_mode) for b in selected.values()]

    edges: list[dict[str, Any]] = []
    for assoc in list(idx.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        edge = association_to_edge(assoc, edge_mode=edge_mode)
        src = str(edge.get("start_bead_id") or "")
        dst = str(edge.get("end_bead_id") or "")
        if src in selected and dst in selected:
            edges.append(edge)

    return {"nodes": nodes, "edges": edges}


def _collect_prune_keep_assoc_ids(root: str, *, session_id: str | None, bead_ids: list[str] | None) -> list[str]:
    idx_file = Path(root) / ".beads" / "index.json"
    if not idx_file.exists():
        return []

    try:
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    beads = {str(k): dict(v) for k, v in ((idx.get("beads") or {}).items()) if isinstance(v, dict)}
    bead_id_filter = {str(x) for x in (bead_ids or []) if str(x).strip()}

    selected_bead_ids: set[str] = set()
    for bid, bead in beads.items():
        if bead_id_filter and bid not in bead_id_filter:
            continue
        if session_id and str(bead.get("session_id") or "") != str(session_id):
            continue
        selected_bead_ids.add(bid)

    if not session_id and not bead_id_filter:
        selected_bead_ids = set(beads.keys())

    keep_ids: list[str] = []
    seen: set[str] = set()
    for assoc in list(idx.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        dst = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        if selected_bead_ids and src not in selected_bead_ids and dst not in selected_bead_ids:
            continue

        edge = association_to_edge(assoc)
        assoc_id = str((edge.get("properties") or {}).get("association_id") or "").strip()
        if not assoc_id or assoc_id in seen:
            continue
        seen.add(assoc_id)
        keep_ids.append(assoc_id)

    return keep_ids


def _dedupe_projection(projection: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    warnings: list[str] = []

    nodes_by_id: dict[str, dict[str, Any]] = {}
    duplicate_nodes = 0
    for node in list(projection.get("nodes") or []):
        props = dict((node or {}).get("properties") or {})
        bead_id = str(props.get("bead_id") or "").strip()
        if not bead_id:
            continue
        if bead_id in nodes_by_id:
            duplicate_nodes += 1
        nodes_by_id[bead_id] = dict(node)

    edges_by_key: dict[str, dict[str, Any]] = {}
    duplicate_edges = 0
    for edge in list(projection.get("edges") or []):
        props = dict((edge or {}).get("properties") or {})
        assoc_id = str(props.get("association_id") or "").strip()
        if not assoc_id:
            assoc_id = str(props.get("dedupe_key") or "").strip()
        if not assoc_id:
            src = str((edge or {}).get("start_bead_id") or "").strip()
            dst = str((edge or {}).get("end_bead_id") or "").strip()
            rel = str(props.get("relationship") or "associated_with").strip().lower()
            assoc_id = f"{src}|{dst}|{rel}"
        if assoc_id in edges_by_key:
            duplicate_edges += 1
        edges_by_key[assoc_id] = dict(edge)

    if duplicate_nodes:
        warnings.append("neo4j_projection_duplicate_nodes_deduped")
    if duplicate_edges:
        warnings.append("neo4j_projection_duplicate_edges_deduped")

    return {
        "nodes": list(nodes_by_id.values()),
        "edges": list(edges_by_key.values()),
    }, warnings

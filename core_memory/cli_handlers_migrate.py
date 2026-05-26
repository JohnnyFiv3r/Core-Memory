from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _iter_all_beads(root: Path) -> list[dict]:
    """Read all beads from index.json. Falls back to session JSONL scan if absent."""
    index_file = root / ".beads" / "index.json"
    if index_file.exists():
        try:
            idx = json.loads(index_file.read_text(encoding="utf-8"))
            return list((idx.get("beads") or {}).values())
        except Exception as exc:
            logger.warning("migrate: failed to read index.json, scanning sessions: %s", exc)

    # Fallback: scan session JSONL files
    beads: list[dict] = []
    for jsonl_file in sorted((root / ".beads").glob("session-*.jsonl")):
        try:
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    bead = json.loads(line)
                    if isinstance(bead, dict) and bead.get("id"):
                        beads.append(bead)
        except Exception as exc:
            logger.warning("migrate: failed to read %s: %s", jsonl_file, exc)
    return beads


def _iter_all_associations(root: Path) -> list[dict]:
    index_file = root / ".beads" / "index.json"
    if index_file.exists():
        try:
            idx = json.loads(index_file.read_text(encoding="utf-8"))
            return list(idx.get("associations") or [])
        except Exception:
            pass
    return []


def _embed_text(bead: dict) -> str:
    title = str(bead.get("title") or "")
    summary = " ".join(str(s) for s in (bead.get("summary") or []))
    facts = " ".join(str(f) for f in (bead.get("retrieval_facts") or []))
    return f"{title}. {summary}. {facts}".strip(". ")


def _bead_payload(bead: dict) -> dict:
    return {
        "bead_id": str(bead.get("id") or ""),
        "type": str(bead.get("type") or ""),
        "session_id": str(bead.get("session_id") or ""),
        "created_at": str(bead.get("created_at") or ""),
        "retrieval_eligible": bool(bead.get("retrieval_eligible", True)),
        "status": str(bead.get("status") or "open"),
        "topics": [str(t) for t in (bead.get("tags") or [])],
        "entities": [str(e) for e in (bead.get("entities") or [])],
        "title": str(bead.get("title") or ""),
        "promoted": bool(bead.get("promotion_state") == "promoted"),
    }


def handle_migrate(args: Any) -> int:
    """Populate Qdrant and/or Kuzu from existing bead store. Idempotent (upsert/MERGE)."""
    root = Path(getattr(args, "root", None) or ".").resolve()
    dry_run = bool(getattr(args, "dry_run", False))
    skip_vectors = bool(getattr(args, "skip_vectors", False))
    skip_graph = bool(getattr(args, "skip_graph", False))

    beads = _iter_all_beads(root)
    associations = _iter_all_associations(root)

    vec_backend_name = os.environ.get("CORE_MEMORY_VECTOR_BACKEND", "qdrant").strip().lower()
    graph_backend_name = os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").strip().lower()

    vec_upserted = 0
    vec_errors: list[str] = []
    if not skip_vectors and vec_backend_name == "qdrant":
        try:
            from core_memory.retrieval.semantic_index import (
                _create_external_backend,
                _paths,
                VECTOR_BACKEND_QDRANT,
            )
            manifest_file, *_ = _paths(root)
            dimension = 1536
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                dimension = int(manifest.get("dimension") or 1536)
            except Exception:
                pass
            vec_backend = _create_external_backend(root=root, backend=VECTOR_BACKEND_QDRANT, dimension=dimension)
            for bead in beads:
                if not bead.get("retrieval_eligible", True):
                    continue
                if bead.get("status") in ("retracted",):
                    continue
                if dry_run:
                    vec_upserted += 1
                    continue
                try:
                    from qdrant_client.models import PointStruct
                    payload = _bead_payload(bead)
                    text = _embed_text(bead)
                    vec_backend._client.upsert(
                        collection_name=vec_backend._collection,
                        points=[PointStruct(id=str(bead["id"]), vector={}, payload={**payload, "_text": text})],
                    )
                    vec_upserted += 1
                except Exception as exc:
                    vec_errors.append(f"bead:{bead.get('id')}:{exc}")
        except Exception as exc:
            vec_errors.append(f"vector_backend_init:{exc}")

    graph_nodes = 0
    graph_edges = 0
    graph_errors: list[str] = []
    if not skip_graph and graph_backend_name not in ("none", ""):
        try:
            from core_memory.persistence.graph.factory import create_graph_backend
            graph = create_graph_backend(root)
            if dry_run:
                graph_nodes = len(beads)
                graph_edges = len(associations)
            else:
                result = graph.sync_from_storage(beads, associations)
                graph_nodes = int(result.get("synced_beads") or 0)
                graph_edges = int(result.get("synced_associations") or 0)
                graph_errors = list(result.get("errors") or [])
        except Exception as exc:
            graph_errors.append(f"graph_backend_init:{exc}")

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "root": str(root),
        "total_beads": len(beads),
        "total_associations": len(associations),
        "vector": {
            "backend": vec_backend_name,
            "skipped": skip_vectors,
            "upserted": vec_upserted,
            "errors": vec_errors,
        },
        "graph": {
            "backend": graph_backend_name,
            "skipped": skip_graph,
            "nodes": graph_nodes,
            "edges": graph_edges,
            "errors": graph_errors,
        },
    }
    print(json.dumps(report, indent=2))
    return 1 if (vec_errors or graph_errors) else 0

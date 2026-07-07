from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core_memory.persistence.graph.factory import create_graph_backend
from core_memory.persistence.sync_targets import create_sync_targets

_log = logging.getLogger(__name__)


def _embed_text(bead: dict) -> str:
    from core_memory.schema.bead_projection import build_retrieval_text

    return build_retrieval_text(bead)


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


def _mirror_bead_to_backends(root: Any, bead: dict) -> None:
    """Best-effort mirror to vector, graph, and sync targets."""

    root_path = Path(root)

    from core_memory.retrieval.semantic_index import VECTOR_BACKEND_QDRANT, _configured_vector_backend

    if _configured_vector_backend() == VECTOR_BACKEND_QDRANT and bead.get("retrieval_eligible", True):
        try:
            from core_memory.retrieval.semantic_index import (
                _auto_configure_embedding_provider_from_keys,
                _create_external_backend,
                _default_embedding_model,
                _embed_vectors,
                _qdrant_external_embeddings_enabled,
                _vector_dim,
                _vector_rows,
            )

            payload = _bead_payload(bead)
            text = _embed_text(bead)
            bead_id = str(bead.get("id") or "")
            if _qdrant_external_embeddings_enabled():
                provider = (_auto_configure_embedding_provider_from_keys() or "gemini").strip().lower()
                model = (os.environ.get("CORE_MEMORY_EMBEDDINGS_MODEL") or _default_embedding_model(provider)).strip()
                vecs = _embed_vectors(texts=[text], provider=provider, model=model, hash_dim=256)
                dim = _vector_dim(vecs, fallback=256)
                vec_backend = _create_external_backend(root=root_path, backend=VECTOR_BACKEND_QDRANT, dimension=dim)
                embs = _vector_rows(vecs)
                if embs:
                    vec_backend.upsert(bead_id=bead_id, embedding=embs[0], metadata=payload)
            else:
                vec_backend = _create_external_backend(root=root_path, backend=VECTOR_BACKEND_QDRANT, dimension=0)
                vec_backend.upsert_texts(bead_ids=[bead_id], texts=[text], metadatas=[payload])
        except Exception as exc:  # noqa: BLE001
            _log.warning("qdrant upsert failed for bead %s: %s", bead.get("id"), exc)

    if os.environ.get("CORE_MEMORY_GRAPH_BACKEND", "kuzu").strip().lower() not in ("none", ""):
        try:
            graph = create_graph_backend(root_path)
            graph.on_bead_written(bead)
        except Exception as exc:  # noqa: BLE001
            _log.warning("graph on_bead_written failed for bead %s: %s", bead.get("id"), exc)

    for target in create_sync_targets():
        try:
            target.on_bead_written(bead)
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync target %s on_bead_written failed: %s", getattr(target, "name", "?"), exc)


def _enqueue_association_coverage(
    *,
    root: Any,
    bead_id: str,
    trigger: str,
    source: str,
    session_id: str,
) -> None:
    try:
        from core_memory.runtime.associations.coverage import on_bead_committed

        on_bead_committed(
            root,
            bead_id,
            trigger=trigger,
            source=source,
            run_inline=False,
            session_id=session_id,
            enqueue=True,
        )
    except Exception:
        pass


def run_bead_commit_side_effects(
    *,
    root: Any,
    bead: dict,
    bead_id: str,
    association_coverage_enabled: bool = True,
    association_coverage_trigger: str = "bead_committed",
    association_coverage_source: str = "memory_store",
    session_id: str = "",
) -> dict[str, Any]:
    """Run best-effort side effects after durable bead storage completes."""

    _mirror_bead_to_backends(root, bead)

    if association_coverage_enabled:
        _enqueue_association_coverage(
            root=root,
            bead_id=str(bead_id or ""),
            trigger=str(association_coverage_trigger or "bead_committed"),
            source=str(association_coverage_source or "memory_store"),
            session_id=str(session_id or ""),
        )

    return {"ok": True}


__all__ = ["run_bead_commit_side_effects"]

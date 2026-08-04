"""Retrieval-layer orchestration for claims-first junction projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from core_memory.graph.junctions import (
    derive_junction_projection as _derive_graph_junction_projection,
)
from core_memory.graph.junctions import (
    resolve_junction_matches as _resolve_graph_junction_matches,
)
from core_memory.retrieval.semantic_index import load_cached_bead_embeddings


def derive_junction_projection(
    root: str | Path,
    *,
    embeddings: Mapping[str, Sequence[float]] | None = None,
    include_beads: bool = True,
) -> dict[str, Any]:
    """Derive junctions using vectors already owned by the semantic index."""

    root_path = Path(root)
    vector_source = "provided"
    vectors = embeddings
    if vectors is None:
        vector_source = "cached_semantic_index"
        vectors = load_cached_bead_embeddings(root_path)
    return _derive_graph_junction_projection(
        root_path,
        embeddings=vectors,
        embedding_source=vector_source,
        include_beads=include_beads,
    )


def resolve_junction_matches(
    root: str | Path,
    left_bead_id: str,
    right_bead_id: str,
    *,
    projection: dict[str, Any] | None = None,
    embeddings: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, Any]:
    """Resolve a pair after loading the canonical cached-vector projection."""

    derived = projection or derive_junction_projection(root, embeddings=embeddings, include_beads=True)
    return _resolve_graph_junction_matches(
        root,
        left_bead_id,
        right_bead_id,
        projection=derived,
    )


__all__ = ["derive_junction_projection", "resolve_junction_matches"]

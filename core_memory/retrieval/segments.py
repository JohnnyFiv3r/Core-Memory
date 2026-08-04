"""Retrieval-layer orchestration for bounded causal segment search."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from core_memory.graph.root_cause import segment_between as _segment_between
from core_memory.graph.root_cause import segment_frontier_between as _segment_frontier_between
from core_memory.retrieval.junctions import derive_junction_projection


def segment_between(
    root: str | Path,
    anchor_a: str,
    anchor_b: str,
    *,
    max_len: int = 6,
    direction: str = "upstream",
    temporal_frame: str = "auto",
    relation_families: list[str] | None = None,
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
    embeddings: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, Any] | None:
    """Return the best observed segment using the cached junction projection."""

    projection = derive_junction_projection(root, embeddings=embeddings, include_beads=True)
    return _segment_between(
        Path(root),
        anchor_a,
        anchor_b,
        max_len=max_len,
        direction=direction,
        temporal_frame=temporal_frame,
        relation_families=relation_families,
        allowed_source_ids=allowed_source_ids,
        denied_source_ids=denied_source_ids,
        projection=projection,
    )


def segment_frontier_between(
    root: str | Path,
    anchor_a: str,
    anchor_b: str,
    *,
    max_len: int = 6,
    direction: str = "upstream",
    temporal_frame: str = "current_truth",
    relation_families: list[str] | None = None,
    allowed_source_ids: list[str] | None = None,
    denied_source_ids: list[str] | None = None,
    max_expansions: int = 5_000,
    max_partitions: int = 32,
    max_results_per_partition: int = 2,
    embeddings: Mapping[str, Sequence[float]] | None = None,
) -> dict[str, Any]:
    """Return the complete bounded frontier using cached junction identities."""

    projection = derive_junction_projection(root, embeddings=embeddings, include_beads=True)
    return _segment_frontier_between(
        Path(root),
        anchor_a,
        anchor_b,
        max_len=max_len,
        direction=direction,
        temporal_frame=temporal_frame,
        relation_families=relation_families,
        allowed_source_ids=allowed_source_ids,
        denied_source_ids=denied_source_ids,
        max_expansions=max_expansions,
        max_partitions=max_partitions,
        max_results_per_partition=max_results_per_partition,
        projection=projection,
    )


__all__ = ["segment_between", "segment_frontier_between"]

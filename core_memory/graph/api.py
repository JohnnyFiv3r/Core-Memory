"""Compatibility facade for graph operations.

Authoritative implementations live in split modules:
- core_memory.graph.structural
- core_memory.graph.traversal
- core_memory.graph.semantic

This module preserves legacy import paths (`core_memory.graph.api.*`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _api_impl
from .structural import (
    add_structural_edge,
    backfill_causal_links,
    backfill_structural_edges,
    infer_structural_edges,
    sync_structural_pipeline,
)
from .traversal import causal_traverse_bidirectional, causal_traverse_chains
from .semantic import (
    add_semantic_edge,
    update_semantic_edge,
    deactivate_semantic_edge,
    decay_semantic_edges,
    reinforce_semantic_edges,
)

STRUCTURAL_RELS = _api_impl.STRUCTURAL_RELS


def build_graph(root: Path, *, write_snapshot: bool = True, semantic_active_k: int = 50) -> dict[str, Any]:
    return _api_impl.build_graph(root, write_snapshot=write_snapshot, semantic_active_k=semantic_active_k)


def graph_stats(root: Path) -> dict[str, Any]:
    return _api_impl.graph_stats(root)


def causal_traverse(
    root: Path,
    anchor_ids: list[str],
    max_depth: int = 4,
    max_chains: int = 50,
    semantic_expansion_hops: int = 1,
    semantic_w_min: float = 0.35,
) -> dict[str, Any]:
    """Legacy entrypoint for rich causal traversal chains."""
    return causal_traverse_chains(
        root,
        anchor_ids=anchor_ids,
        max_depth=max_depth,
        max_chains=max_chains,
        semantic_expansion_hops=semantic_expansion_hops,
        semantic_w_min=semantic_w_min,
    )

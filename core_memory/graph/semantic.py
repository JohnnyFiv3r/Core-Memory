"""Graph semantic operations.

Public split-module surface backed by the shared internal graph implementation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _api_impl


def add_semantic_edge(
    root: Path,
    *,
    src_id: str,
    dst_id: str,
    rel: str,
    w: float,
    created_by: str = "system",
    evidence: list[dict] | None = None,
) -> dict[str, Any]:
    return _api_impl.add_semantic_edge(
        root,
        src_id=src_id,
        dst_id=dst_id,
        rel=rel,
        w=w,
        created_by=created_by,
        evidence=evidence,
    )


def update_semantic_edge(
    root: Path,
    *,
    edge_id: str,
    w: float,
    reinforcement_count: int,
    last_reinforced_at: str | None = None,
) -> dict[str, Any]:
    return _api_impl.update_semantic_edge(
        root,
        edge_id=edge_id,
        w=w,
        reinforcement_count=reinforcement_count,
        last_reinforced_at=last_reinforced_at,
    )


def deactivate_semantic_edge(root: Path, *, edge_id: str, reason: str = "decayed_below_threshold") -> dict[str, Any]:
    return _api_impl.deactivate_semantic_edge(root, edge_id=edge_id, reason=reason)


def decay_semantic_edges(
    root: Path,
    *,
    w_drop: float = 0.08,
    half_life_days: float = 14.0,
) -> dict[str, Any]:
    return _api_impl.decay_semantic_edges(root, w_drop=w_drop, half_life_days=half_life_days)


def reinforce_semantic_edges(root: Path, edge_ids: list[str], alpha: float = 0.15) -> dict[str, Any]:
    return _api_impl.reinforce_semantic_edges(root, edge_ids=edge_ids, alpha=alpha)

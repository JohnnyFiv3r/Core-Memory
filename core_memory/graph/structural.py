"""Graph structural operations.

Public split-module surface backed by the shared internal graph implementation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _api_impl


def add_structural_edge(
    root: Path,
    *,
    src_id: str,
    dst_id: str,
    rel: str,
    created_by: str = "system",
    evidence: list[dict] | None = None,
) -> dict[str, Any]:
    return _api_impl.add_structural_edge(
        root,
        src_id=src_id,
        dst_id=dst_id,
        rel=rel,
        created_by=created_by,
        evidence=evidence,
    )


def backfill_causal_links(
    root: Path,
    *,
    apply: bool = False,
    max_per_target: int = 3,
    min_overlap: int = 2,
    require_shared_turn: bool = True,
    include_bead_ids: list[str] | None = None,
) -> dict[str, Any]:
    return _api_impl.backfill_causal_links(
        root,
        apply=apply,
        max_per_target=max_per_target,
        min_overlap=min_overlap,
        require_shared_turn=require_shared_turn,
        include_bead_ids=include_bead_ids,
    )


def sync_structural_pipeline(root: Path, *, apply: bool = False, strict: bool = False) -> dict[str, Any]:
    return _api_impl.sync_structural_pipeline(root, apply=apply, strict=strict)


def backfill_structural_edges(root: Path) -> dict[str, Any]:
    return _api_impl.backfill_structural_edges(root)


def infer_structural_edges(root: Path, *, min_confidence: float = 0.9, apply: bool = False) -> dict[str, Any]:
    return _api_impl.infer_structural_edges(root, min_confidence=min_confidence, apply=apply)

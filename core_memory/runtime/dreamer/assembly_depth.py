"""Dreamer V3 — Assembly Depth compatibility wrapper (PRD §12)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.soul.assembly_depth import (
    ASSEMBLY_DEPTH_SCHEMA,
    DEFAULT_ANTI_WEIGHTS,
    DEFAULT_FACTOR_WEIGHTS,
    compute_assembly_depth as _compute_assembly_depth,
)


def _live_edge_bonus(root: str | Path) -> dict[str, float]:
    try:
        from core_memory.runtime.observability.myelination import compute_myelination_bonus_map

        return dict((compute_myelination_bonus_map(root).get("bonus_by_edge_key") or {}))
    except Exception:
        return {}


def compute_assembly_depth(
    root: str | Path,
    *,
    target_kind: str = "goal",
    limit: int = 200,
) -> dict[str, Any]:
    """Compute Assembly Depth with Dreamer/runtime live myelination bonuses."""
    return _compute_assembly_depth(
        root,
        target_kind=target_kind,
        limit=limit,
        edge_bonus=_live_edge_bonus(root),
    )


__all__ = [
    "ASSEMBLY_DEPTH_SCHEMA",
    "DEFAULT_FACTOR_WEIGHTS",
    "DEFAULT_ANTI_WEIGHTS",
    "compute_assembly_depth",
]

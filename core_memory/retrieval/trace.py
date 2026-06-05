from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory.retrieval.pipeline.canonical import trace_request as _trace_request


def trace_request(
    *,
    root: str | Path,
    query: str = "",
    anchor_ids: list[str] | None = None,
    k: int = 10,
    intent: str = "causal",
    hydration: dict[str, Any] | None = None,
    submission: dict[str, Any] | None = None,
    max_depth: int | None = None,
    max_chains: int | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for causal trace retrieval.

    The canonical implementation lives in ``core_memory.retrieval.pipeline``;
    this module preserves the shorter import path used by downstream demo and
    integration code.
    """

    return _trace_request(
        root=root,
        query=query,
        anchor_ids=anchor_ids,
        k=int(k),
        intent=intent,
        hydration=hydration,
        submission=submission,
        max_depth=max_depth,
        max_chains=max_chains,
    )

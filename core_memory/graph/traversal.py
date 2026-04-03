"""
Graph traversal and causal query helpers.

Split from graph.py per Codex Phase 5 readability refactor.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _paths(root: Path) -> tuple[Path, Path, Path]:
    """Return (beads_dir, events_dir, edges_file)."""
    beads_dir = root / ".beads"
    events_dir = beads_dir / "events"
    edges_file = events_dir / "graph-edges.jsonl"
    return beads_dir, events_dir, edges_file


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recency_factor(ts: str, half_life_days: float = 30.0) -> float:
    """Compute recency factor (1.0 at creation, decays over time)."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        return 2 ** (-age_days / half_life_days)
    except Exception:
        return 0.5


def _build_adjacency(beads: dict[str, Any], associations: list[dict]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build forward and reverse adjacency dicts from beads + associations.

    Returns:
        (forward_adj, reverse_adj) where forward_adj[src] = [dst, ...] and
        reverse_adj[dst] = [src, ...]
    """
    forward: dict[str, list[str]] = defaultdict(list)
    reverse: dict[str, list[str]] = defaultdict(list)

    # From bead links
    for bead_id, bead in beads.items():
        links = bead.get("links") or {}
        for rel, targets in links.items():
            if isinstance(targets, list):
                for tid in targets:
                    if tid:
                        forward[bead_id].append(tid)
                        reverse[tid].append(bead_id)

    # From associations
    for assoc in associations:
        src = assoc.get("source_bead") or ""
        tgt = assoc.get("target_bead") or ""
        if src and tgt:
            forward[src].append(tgt)
            reverse[tgt].append(src)

    return dict(forward), dict(reverse)


def causal_traverse(
    root: Path,
    *,
    start_bead_ids: list[str],
    direction: str = "forward",
    max_depth: int = 3,
    include_types: list[str] | None = None,
) -> dict[str, Any]:
    """Traverse causal graph from starting beads.

    Args:
        root: Memory root path
        start_bead_ids: Starting bead IDs
        direction: "forward" (follows/caused_by) or "backward" (led_to/causes)
        max_depth: Max traversal depth
        include_types: Optional filter by bead types
    """
    from ..persistence.store import MemoryStore

    memory = MemoryStore(root=str(root))
    index = memory._read_json(memory.beads_dir / "index.json")
    beads = index.get("beads") or {}
    associations = index.get("associations") or []

    forward_adj, reverse_adj = _build_adjacency(beads, associations)
    adj = forward_adj if direction == "forward" else reverse_adj

    visited: set[str] = set()
    queue = [(bid, 0) for bid in start_bead_ids]
    results: list[dict] = []

    while queue:
        current_id, depth = queue.pop(0)

        if current_id in visited or depth > max_depth:
            continue
        visited.add(current_id)

        bead = beads.get(current_id)
        if not bead:
            continue

        if include_types and bead.get("type") not in include_types:
            continue

        results.append({
            "bead_id": current_id,
            "depth": depth,
            "type": bead.get("type"),
            "title": bead.get("title"),
            "status": bead.get("status"),
        })

        # Traverse neighbors using pre-built adjacency (O(degree) instead of O(N))
        for neighbor_id in adj.get(current_id, []):
            if neighbor_id not in visited:
                queue.append((neighbor_id, depth + 1))

    return {
        "ok": True,
        "start_beads": start_bead_ids,
        "direction": direction,
        "max_depth": max_depth,
        "visited": len(visited),
        "results": results,
    }


def causal_traverse_bidirectional(
    root: Path,
    *,
    start_bead_ids: list[str],
    max_depth: int = 2,
) -> dict[str, Any]:
    """Traverse both forward and backward from starting beads."""
    forward = causal_traverse(
        root,
        start_bead_ids=start_bead_ids,
        direction="forward",
        max_depth=max_depth,
    )
    backward = causal_traverse(
        root,
        start_bead_ids=start_bead_ids,
        direction="backward",
        max_depth=max_depth,
    )

    # Merge results, taking min depth
    merged: dict[str, dict] = {}

    for r in forward.get("results", []):
        bid = r["bead_id"]
        if bid not in merged or r["depth"] < merged[bid]["depth"]:
            merged[bid] = {**r, "direction": "forward"}

    for r in backward.get("results", []):
        bid = r["bead_id"]
        if bid not in merged or r["depth"] < merged[bid]["depth"]:
            merged[bid] = {**r, "direction": "backward"}

    return {
        "ok": True,
        "start_beads": start_bead_ids,
        "max_depth": max_depth,
        "visited": len(merged),
        "results": list(merged.values()),
    }

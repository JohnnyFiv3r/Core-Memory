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

    # Precompute active association pairs so stale association_sync links are ignored.
    active_assoc_pairs: set[tuple[str, str]] = set()
    for assoc in associations:
        if not isinstance(assoc, dict):
            continue
        status = str(assoc.get("status") or "active").strip().lower() or "active"
        if status in {"retracted", "superseded", "inactive"}:
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        if src and tgt:
            active_assoc_pairs.add((src, tgt))

    # From bead links (support legacy dict form and list-of-dicts form).
    for bead_id, bead in beads.items():
        links = bead.get("links") or {}
        if isinstance(links, dict):
            for _rel, targets in links.items():
                if isinstance(targets, list):
                    for tid in targets:
                        tid_s = str(tid or "").strip()
                        if tid_s:
                            forward[bead_id].append(tid_s)
                            reverse[tid_s].append(bead_id)
                else:
                    tid_s = str(targets or "").strip()
                    if tid_s:
                        forward[bead_id].append(tid_s)
                        reverse[tid_s].append(bead_id)
        elif isinstance(links, list):
            for row in links:
                if not isinstance(row, dict):
                    continue
                tid_s = str(row.get("bead_id") or "").strip()
                if not tid_s:
                    continue
                src_tag = str(row.get("source") or "").strip().lower()
                if src_tag == "association_sync" and (bead_id, tid_s) not in active_assoc_pairs:
                    # stale sync link from an inactive association; skip traversal
                    continue
                forward[bead_id].append(tid_s)
                reverse[tid_s].append(bead_id)

    # From associations
    for assoc in associations:
        if not isinstance(assoc, dict):
            continue
        status = str(assoc.get("status") or "active").strip().lower() or "active"
        if status in {"retracted", "superseded", "inactive"}:
            continue
        src = assoc.get("source_bead") or assoc.get("source_bead_id") or ""
        tgt = assoc.get("target_bead") or assoc.get("target_bead_id") or ""
        if src and tgt:
            forward[src].append(tgt)
            reverse[tgt].append(src)

    return dict(forward), dict(reverse)


def filter_chains_to_active_edges(root: Path, chains: list[dict]) -> list[dict]:
    """Apply the active-association view to graph-backend traversal chains.

    The canonical index is the source of truth for association status; graph
    backends can lag behind it (e.g. a retraction edits ``index.json`` without
    a backend resync). Each chain is truncated at its first edge whose
    (src, dst) pair has no active association in the index — mirroring how the
    Python traversal would never have walked past it — and chains left with no
    edges are dropped. Edges are matched in both orientations because
    traversal may walk an association in reverse.
    """
    if not chains:
        return []
    try:
        index = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return list(chains)

    active_pairs: set[tuple[str, str]] = set()
    for assoc in (index.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        status = str(assoc.get("status") or "active").strip().lower() or "active"
        if status in {"retracted", "superseded", "inactive"}:
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        if src and tgt:
            active_pairs.add((src, tgt))
            active_pairs.add((tgt, src))

    filtered: list[dict] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        edges = list(chain.get("edges") or [])
        keep = 0
        for e in edges:
            src = str((e or {}).get("src") or "")
            dst = str((e or {}).get("dst") or (e or {}).get("tgt") or "")
            if not src or not dst or (src, dst) not in active_pairs:
                break
            keep += 1
        if keep == 0:
            continue
        if keep == len(edges):
            filtered.append(chain)
            continue
        truncated = dict(chain)
        truncated["edges"] = edges[:keep]
        for seq_key in ("nodes", "beads"):
            seq = list(truncated.get(seq_key) or [])
            if len(seq) > keep + 1:
                truncated[seq_key] = seq[: keep + 1]
        path = list(truncated.get("path") or [])
        if len(path) > keep + 1:
            truncated["path"] = path[: keep + 1]
        filtered.append(truncated)
    return filtered


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
        direction: "forward" (follows/causes) or "backward" (leads_to/causes)
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


def causal_traverse_chains(
    root: Path,
    *,
    anchor_ids: list[str],
    max_depth: int = 4,
    max_chains: int = 50,
    semantic_expansion_hops: int = 1,
    semantic_w_min: float = 0.35,
) -> dict[str, Any]:
    """Canonical rich causal traversal used by retrieval surfaces.

    This delegates to the shared internal graph implementation and keeps the
    chain-scoring output contract stable while `graph.api` stays a compatibility
    facade.
    """

    from . import core as _api_impl

    return _api_impl.causal_traverse(
        root,
        anchor_ids=anchor_ids,
        max_depth=max_depth,
        max_chains=max_chains,
        semantic_expansion_hops=semantic_expansion_hops,
        semantic_w_min=semantic_w_min,
    )

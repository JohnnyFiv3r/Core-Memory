"""Read-only tension signal detection for SOUL continuity summaries.

These helpers inspect the bead graph for candidate tensions. They do not enqueue
Dreamer candidates or mutate SOUL state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.schema.normalization import normalize_relation_type

_INACTIVE_ASSOC_STATUSES = {"retracted", "superseded", "inactive"}
_INACTIVE_BEAD_STATUSES = {"superseded", "archived"}


def _read_index(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_active_goal(bead: dict[str, Any]) -> bool:
    if str(bead.get("type") or "").strip().lower() != "goal":
        return False
    if str(bead.get("status") or "").strip().lower() in _INACTIVE_BEAD_STATUSES:
        return False
    if str(bead.get("approval_status") or "").strip().lower() == "rejected":
        return False
    return True


def _goal_conflict_key(a: str, b: str) -> str:
    lo, hi = sorted([a, b])
    return f"tension:goal_conflict:{lo}|{hi}"


def detect_goal_conflicts(
    root: str | Path,
    *,
    depth_by_goal: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Return active goal pairs joined by an active ``contradicts`` edge."""
    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}

    goals = {bid: b for bid, b in beads.items() if _is_active_goal(b)}
    if len(goals) < 2:
        return []

    depth_by_goal = dict(depth_by_goal or {})
    seen_pairs: set[str] = set()
    out: list[dict[str, Any]] = []
    for assoc in (index.get("associations") or []):
        if not isinstance(assoc, dict):
            continue
        if str(assoc.get("status") or "active").strip().lower() in _INACTIVE_ASSOC_STATUSES:
            continue
        if normalize_relation_type(assoc.get("relationship")) != "contradicts":
            continue
        s = str(assoc.get("source_bead") or "").strip()
        d = str(assoc.get("target_bead") or "").strip()
        if s not in goals or d not in goals or s == d:
            continue
        key = _goal_conflict_key(s, d)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        title_s = str(goals[s].get("title") or s)
        title_d = str(goals[d].get("title") or d)
        out.append({
            "tension_key": key,
            "tension_kind": "goal_conflict",
            "conflict_bead_a": s,
            "conflict_bead_b": d,
            "conflict_relationship": "contradicts",
            "statement": f"Goals conflict: '{title_s}' vs '{title_d}'.",
            "confidence": float(assoc.get("confidence") or 0.5),
            "assembly_depth": {s: depth_by_goal.get(s, 0.0), d: depth_by_goal.get(d, 0.0)},
        })
    return out


__all__ = ["detect_goal_conflicts"]

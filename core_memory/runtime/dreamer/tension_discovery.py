"""Dreamer V3 — tension discovery (PRD §13).

A tension is a persistent conflict between goals, constraints, values, behaviors,
or storyline continuations. The storyline projection already computes some
tensions (competing overlays, claim-slot conflicts); V3 extends detection with
new families. This slice adds **goal-conflict** detection: two active goals
linked by an active ``contradicts`` edge.

Dreamer emits ``tension_candidate`` rows into the candidate queue — they are not
endorsed tensions until accepted into SOUL or materialized through an approved
flow. Tension candidates deliberately do **not** carry
``source_bead_id``/``relationship`` fields, so they stay out of the myelination
reward path (accepting a tension is not a reward source, PRD §11.1); they use
``conflict_bead_a``/``conflict_bead_b`` instead.

Each candidate carries the Assembly Depth (§12) of its conflicting goals, so a
conflict between two historically irreducible goals reads as more significant
than one between two shallow ones.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.schema.normalization import normalize_relation_type

_INACTIVE_ASSOC_STATUSES = {"retracted", "superseded", "inactive"}
_INACTIVE_BEAD_STATUSES = {"superseded", "archived"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def detect_goal_conflicts(root: str | Path) -> list[dict[str, Any]]:
    """Return goal-conflict detections: active goal pairs joined by an active
    ``contradicts`` edge, each annotated with both goals' Assembly Depth."""
    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}

    goals = {bid: b for bid, b in beads.items() if _is_active_goal(b)}
    if len(goals) < 2:
        return []

    # Assembly depth for goals (best-effort), keyed by target_id. Cover the full
    # goal population (all goal beads, not just the active subset): a truncated
    # limit drops goals and distorts the percentile normalization, and ineligible
    # goals may precede active ones in index order.
    depth_by_goal: dict[str, float] = {}
    try:
        from core_memory.runtime.dreamer.assembly_depth import compute_assembly_depth

        total_goals = max(1, sum(1 for b in beads.values() if str(b.get("type") or "").strip().lower() == "goal"))
        reports = compute_assembly_depth(root, target_kind="goal", limit=total_goals).get("reports") or []
        for rep in reports:
            depth_by_goal[str(rep.get("target_id"))] = float(rep.get("score") or 0.0)
    except Exception:
        depth_by_goal = {}

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


def enqueue_goal_conflict_candidates(
    root: str | Path,
    *,
    run_id: str | None = None,
    source: str = "dreamer_tension",
) -> dict[str, Any]:
    """Emit ``tension_candidate`` rows for new goal conflicts.

    Dedup: a conflict (by ``tension_key``) is skipped while a pending or accepted
    tension candidate already covers it. Idempotent across runs.
    """
    from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates

    detections = detect_goal_conflicts(root)
    if not detections:
        return {"ok": True, "detected": 0, "enqueued": 0}

    rows = _read_candidates(root)
    blocked: set[str] = set()
    for r in rows:
        if str(r.get("hypothesis_type") or "") != "tension_candidate":
            continue
        if str(r.get("status") or "") in {"pending", "accepted"}:
            blocked.add(str(r.get("tension_key") or ""))

    now = _now()
    rid = str(run_id or f"tension-{uuid.uuid4().hex[:8]}")
    enqueued = 0
    for det in detections:
        if det["tension_key"] in blocked:
            continue
        rows.append({
            "id": f"dc-{uuid.uuid4().hex[:12]}",
            "created_at": now,
            "status": "pending",
            "hypothesis_type": "tension_candidate",
            "proposal_family": "tension",
            "benchmark_tags": ["tension", "goal_conflict"],
            "tension_kind": det["tension_kind"],
            "tension_key": det["tension_key"],
            "statement": det["statement"],
            "rationale": det["statement"],
            "expected_decision_impact": (
                "Accepting surfaces a persistent tension for SOUL consideration; "
                "nothing is materialized in the graph."
            ),
            "conflict_bead_a": det["conflict_bead_a"],
            "conflict_bead_b": det["conflict_bead_b"],
            "conflict_relationship": det["conflict_relationship"],
            "supporting_bead_ids": [det["conflict_bead_a"], det["conflict_bead_b"]],
            "assembly_depth": det["assembly_depth"],
            "confidence": det["confidence"],
            "novelty": 0.0,
            "grounding": 1.0,
            "run_metadata": {"run_id": rid, "source": source},
        })
        enqueued += 1

    if enqueued:
        _write_candidates(root, rows)
    return {"ok": True, "detected": len(detections), "enqueued": enqueued, "run_id": rid}


__all__ = ["detect_goal_conflicts", "enqueue_goal_conflict_candidates"]

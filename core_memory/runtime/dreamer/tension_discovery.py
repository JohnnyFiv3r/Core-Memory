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

from core_memory.soul.tension_signals import detect_goal_conflicts as _detect_goal_conflicts


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


def _goal_depth_map(root: str | Path) -> dict[str, float]:
    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}
    try:
        from core_memory.runtime.dreamer import assembly_depth as ad

        total_goals = max(1, sum(1 for b in beads.values() if str(b.get("type") or "").strip().lower() == "goal"))
        reports = ad.compute_assembly_depth(root, target_kind="goal", limit=total_goals).get("reports") or []
        return {str(rep.get("target_id")): float(rep.get("score") or 0.0) for rep in reports}
    except Exception:
        return {}


def detect_goal_conflicts(root: str | Path) -> list[dict[str, Any]]:
    """Return goal-conflict detections annotated with goal Assembly Depth."""
    return _detect_goal_conflicts(root, depth_by_goal=_goal_depth_map(root))


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

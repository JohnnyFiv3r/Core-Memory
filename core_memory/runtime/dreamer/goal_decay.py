"""Dreamer V3 — goal decay warnings (PRD Phase 2 / SOUL §12).

Dreamer observes that an active goal appears dormant and emits a
``goal_decay_warning`` candidate. It never decays or abandons the goal itself —
goal decay is a goal-lifecycle / SOUL governance decision; Dreamer only surfaces
the evidence (PRD §21 of the Myelination PRD; SOUL §12).

A goal is flagged dormant when it is decay-eligible (active, not resolved or
promoted) and shows distributed *absence* of traction:
  - never referenced (recall_count == 0),
  - aged past a floor (so fresh goals aren't flagged just for being new),
  - low Assembly Depth (weakly supported / local / transient).

Age is a *floor*, not the primary signal — depth + no-recall carry the judgment,
consistent with "persistence is not age".
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.runtime.dreamer.goal_filters import count_goal_beads as _count_goal_beads, is_active_goal
from core_memory.runtime.observability.retrieval_feedback import _parse_iso

_INACTIVE_BEAD_STATUSES = {"superseded", "archived", "resolved"}


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


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return int(default)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _is_decay_eligible_goal(bead: dict[str, Any]) -> bool:
    """Active, non-terminal goal — the shared definition used across detectors."""
    return is_active_goal(bead)


def _age_days(created_at: str, now: datetime) -> float:
    dt = _parse_iso(created_at)
    if dt is None:
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def detect_goal_decay(root: str | Path) -> list[dict[str, Any]]:
    """Return dormant-goal detections (decay-eligible goals with no traction)."""
    stale_days = _int_env("CORE_MEMORY_GOAL_DECAY_STALE_DAYS", 30)
    depth_max = _float_env("CORE_MEMORY_GOAL_DECAY_DEPTH_MAX", 0.34)

    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}
    goals = {bid: b for bid, b in beads.items() if _is_decay_eligible_goal(b)}
    if not goals:
        return []

    # Score the FULL goal population (compute_assembly_depth truncates the first
    # `limit` goal beads, and ineligible goals may precede eligible ones), so an
    # eligible goal can't be omitted and default to depth 0.0 (false decay).
    depth_by_goal: dict[str, float] = {}
    try:
        from core_memory.runtime.dreamer.assembly_depth import compute_assembly_depth

        total_goals = max(1, _count_goal_beads(beads))
        reports = compute_assembly_depth(root, target_kind="goal", limit=total_goals).get("reports") or []
        for rep in reports:
            depth_by_goal[str(rep.get("target_id"))] = float(rep.get("score") or 0.0)
    except Exception:
        depth_by_goal = {}

    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for gid, b in goals.items():
        if int(b.get("recall_count") or 0) > 0:
            continue
        age = _age_days(str(b.get("created_at") or ""), now)
        if age < float(stale_days):
            continue
        depth = float(depth_by_goal.get(gid, 0.0))
        if depth >= depth_max:
            continue
        title = str(b.get("title") or gid)
        out.append({
            "goal_bead_id": gid,
            "statement": f"Goal '{title}' appears dormant: never referenced, {int(age)}d old, low assembly depth ({depth:.2f}).",
            "assembly_depth": depth,
            "age_days": round(age, 2),
            "recall_count": 0,
        })
    return out


def enqueue_goal_decay_warnings(
    root: str | Path,
    *,
    run_id: str | None = None,
    source: str = "dreamer_goal_decay",
) -> dict[str, Any]:
    """Emit ``goal_decay_warning`` rows for newly-dormant goals.

    Dedup by goal id while a pending/accepted warning already covers it.
    Idempotent across runs; surfaces for SOUL / human review only — Dreamer never
    decays the goal itself.
    """
    from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates

    detections = detect_goal_decay(root)
    if not detections:
        return {"ok": True, "detected": 0, "enqueued": 0}

    rows = _read_candidates(root)
    blocked: set[str] = set()
    for r in rows:
        if str(r.get("hypothesis_type") or "") != "goal_decay_warning":
            continue
        if str(r.get("status") or "") in {"pending", "accepted"}:
            blocked.add(str(r.get("goal_bead_id") or ""))

    now = _now()
    rid = str(run_id or f"goaldecay-{uuid.uuid4().hex[:8]}")
    enqueued = 0
    for det in detections:
        if det["goal_bead_id"] in blocked:
            continue
        rows.append({
            "id": f"dc-{uuid.uuid4().hex[:12]}",
            "created_at": now,
            "status": "pending",
            "hypothesis_type": "goal_decay_warning",
            "proposal_family": "goal",
            "benchmark_tags": ["goal", "decay"],
            "goal_bead_id": det["goal_bead_id"],
            "statement": det["statement"],
            "rationale": det["statement"],
            "expected_decision_impact": (
                "Accepting flags the goal for SOUL/goal-lifecycle decay review; "
                "Dreamer never decays the goal itself."
            ),
            "supporting_bead_ids": [det["goal_bead_id"]],
            "assembly_depth": det["assembly_depth"],
            "age_days": det["age_days"],
            "novelty": 0.0,
            "grounding": 1.0,
            "run_metadata": {"run_id": rid, "source": source},
        })
        enqueued += 1

    if enqueued:
        _write_candidates(root, rows)
    return {"ok": True, "detected": len(detections), "enqueued": enqueued, "run_id": rid}


__all__ = ["detect_goal_decay", "enqueue_goal_decay_warnings"]

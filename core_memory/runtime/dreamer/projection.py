"""Dreamer V3 — future projection (PRD §16–§24, Phase 3).

Dreamer extends storylines into *fuzzy possible continuations* — an array of
``future_vector``s per storyline, not a single prediction. A future vector is a
structured anticipatory hypothesis: *if this storyline continues without
intervention, where does it appear to point?* Projections are advisory — they may
influence how endorsed goals are pursued but **never** create goals, beads,
claims, or overlays (§22). They are stored outside grounded evidence in
``.beads/events/dreamer-projections.jsonl``.

v1 scope: deterministic, structural projection (no LLM statement refinement yet).
Per active storyline it emits a *continuation* vector plus one *tension-resolution*
vector per open tension, each scored by:
  - narrative_strength (§19) — how naturally the continuation follows (continuity,
    evidence/grounding quality, vector kind), penalized by unresolved tensions;
  - attractor_strength (§20) — convergence of other storylines/goals onto the
    projected theme.
Both are v1 heuristics (factor lists in the PRD, formulas defined here), with the
§19.1/§20.1 edge-case guards: superseded evidence excluded, distinct structures
only (no duplicate convergence), speculative grounding down-weighted.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.runtime.dreamer.goal_filters import is_active_goal

FUTURE_PROJECTION_SCHEMA = "future_projection.v1"
FUTURE_VECTOR_SCHEMA = "future_vector.v1"

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


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _tokens(*values: Any) -> set[str]:
    out: set[str] = set()
    for v in values:
        items = v if isinstance(v, (list, tuple, set)) else [v]
        for item in items:
            for w in re.split(r"[^a-z0-9]+", str(item or "").lower()):
                if len(w) >= 4:
                    out.add(w)
    return out


def _projections_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "dreamer-projections.jsonl"


def _quality_fractions(bead_ids: list[str], beads: dict[str, dict]) -> tuple[float, float]:
    """(evidence_quality, grounding_quality) over a backbone's beads:
    fraction with confidence_class in {B,A}, fraction not speculative.
    Superseded/archived beads are excluded."""
    live = [beads[b] for b in bead_ids if b in beads
            and str(beads[b].get("status") or "").lower() not in _INACTIVE_BEAD_STATUSES]
    if not live:
        return 0.0, 0.0
    n = float(len(live))
    ev = sum(1 for b in live if str(b.get("confidence_class") or "C") in {"B", "A"}) / n
    gr = sum(1 for b in live if str(b.get("grounding") or "") != "speculative") / n
    return ev, gr


def _narrative_strength(*, is_continuation: bool, length: int, ev_quality: float,
                        grounding_quality: float, has_tensions: bool) -> float:
    """v1 narrative strength (§19): weighted normalized factors, penalized by
    unresolved tensions. Continuation reads as fewer explanatory jumps than a
    tension-resolution fork."""
    components = {
        "vector_kind": 1.0 if is_continuation else 0.5,
        "continuity": min(1.0, float(max(0, length)) / 5.0),
        "evidence_quality": ev_quality,
        "grounding_quality": grounding_quality,
    }
    weights = {"vector_kind": 0.35, "continuity": 0.20, "evidence_quality": 0.25, "grounding_quality": 0.20}
    score = sum(weights[k] * components[k] for k in weights) / sum(weights.values())
    penalty = 0.15 if has_tensions else 0.0
    return round(_clamp01(score - penalty), 6)


def _attractor_strength(theme: set[str], storyline_id: str, exclude_goal_ids: set[str],
                        all_storylines: list[dict], goal_themes: list[tuple[str, set[str]]]) -> tuple[float, list[str]]:
    """v1 attractor strength (§20): distinct other storylines + active goals whose
    theme tokens overlap, normalized. Distinct structures only (no duplicate
    convergence); the storyline itself and its own backbone goal(s) are excluded
    — a goal-backed storyline must not count its own goal bead as convergence."""
    norm = max(1.0, _float_env("CORE_MEMORY_PROJECTION_CONVERGENCE_NORM", 5.0))
    converging: list[str] = []
    for s in all_storylines:
        sid = str(s.get("id") or "")
        if sid == storyline_id:
            continue
        s_theme = _tokens(((s.get("backbone") or {}).get("label")), )
        if theme & s_theme:
            converging.append(f"storyline:{sid}")
    for gid, g_theme in goal_themes:
        if gid in exclude_goal_ids:
            continue
        if theme & g_theme:
            converging.append(f"goal:{gid}")
    converging = sorted(set(converging))
    return round(_clamp01(len(converging) / norm), 6), converging


def _vector(*, storyline_id: str, kind: str, current_state: str, projected_state: str,
            statement: str, supporting_beads: list[str], supporting_tensions: list[Any],
            narrative_strength: float, attractor_strength: float, converging: list[str],
            has_tensions: bool, revision_triggers: list[str]) -> dict[str, Any]:
    return {
        "schema": FUTURE_VECTOR_SCHEMA,
        "id": f"fv-{uuid.uuid4().hex[:12]}",
        "source_storyline_id": storyline_id,
        "kind": kind,
        "current_state": current_state,
        "projected_state": projected_state,
        "narrative_statement": statement,
        "supporting_tensions": supporting_tensions,
        "supporting_beads": supporting_beads,
        "supporting_converging": converging,
        "narrative_strength": narrative_strength,
        "attractor_strength": attractor_strength,
        "intervention_sensitivity": "high" if has_tensions else "low",
        "expected_revision_triggers": revision_triggers,
        "confidence": narrative_strength,
        "uncertainty_notes": (["v1 structural projection — no LLM statement refinement"]),
    }


def compute_future_projections(
    root: str | Path,
    *,
    run_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Build future projections for all active storylines. Persists each
    ``future_projection.v1`` to dreamer-projections.jsonl when ``persist``."""
    from core_memory.graph.storylines import derive_storylines

    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}

    storylines = list((derive_storylines(root).get("storylines") or []))

    # Active-goal themes for attractor convergence — only open objectives count
    # (resolved/promoted/rejected/superseded goals are excluded; shared helper).
    goal_themes: list[tuple[str, set[str]]] = []
    for bid, b in beads.items():
        if is_active_goal(b):
            goal_themes.append((bid, _tokens(b.get("title"), b.get("entities"), b.get("topics"))))

    rid = str(run_id or f"proj-{uuid.uuid4().hex[:8]}")
    projections: list[dict[str, Any]] = []
    for s in storylines:
        backbone = dict(s.get("backbone") or {})
        bead_ids = [str(x) for x in (backbone.get("bead_ids") or [])]
        # Cite live support only: superseded/archived backbone beads are stale
        # evidence and must not appear in supporting_beads or scoring (§19.1).
        live_bead_ids = [
            b for b in bead_ids
            if b in beads and str(beads[b].get("status") or "").lower() not in _INACTIVE_BEAD_STATUSES
        ]
        if not live_bead_ids:
            continue
        sid = str(s.get("id") or "")
        tensions = list(s.get("tensions") or [])
        has_tensions = bool(tensions)
        length = len(live_bead_ids)
        label = str(backbone.get("label") or sid)
        last_bead = beads.get(live_bead_ids[-1], {})
        current_state = str(last_bead.get("title") or label)
        ev_q, gr_q = _quality_fractions(live_bead_ids, beads)
        theme = _tokens(label, last_bead.get("entities"), last_bead.get("topics"))
        # Exclude the storyline's own backbone members (a goal-backed storyline
        # keeps its goal bead as a backbone member) from goal convergence.
        att, converging = _attractor_strength(theme, sid, set(bead_ids), storylines, goal_themes)

        vectors: list[dict[str, Any]] = []
        # Continuation: storyline continues without intervention.
        vectors.append(_vector(
            storyline_id=sid, kind="continuation",
            current_state=current_state,
            projected_state=f"'{label}' continues its current trajectory.",
            statement=f"If '{label}' continues without intervention, it extends its current direction.",
            supporting_beads=live_bead_ids, supporting_tensions=[],
            narrative_strength=_narrative_strength(
                is_continuation=True, length=length, ev_quality=ev_q,
                grounding_quality=gr_q, has_tensions=has_tensions),
            attractor_strength=att, converging=converging, has_tensions=has_tensions,
            revision_triggers=["new backbone evidence", "tension resolution"],
        ))
        # One resolution vector per open tension (a fork — an explanatory jump).
        for t in tensions:
            tkind = str(t.get("kind") or "tension")
            vectors.append(_vector(
                storyline_id=sid, kind="tension_resolution",
                current_state=current_state,
                projected_state=f"'{label}' resolves its {tkind}.",
                statement=f"If the {tkind} on '{label}' resolves, the storyline forks toward that resolution.",
                supporting_beads=live_bead_ids, supporting_tensions=[t],
                narrative_strength=_narrative_strength(
                    is_continuation=False, length=length, ev_quality=ev_q,
                    grounding_quality=gr_q, has_tensions=has_tensions),
                attractor_strength=att, converging=converging, has_tensions=has_tensions,
                revision_triggers=[f"{tkind} resolved", "human decision"],
            ))

        most_likely = max(vectors, key=lambda v: (v["narrative_strength"], v["id"]))
        projections.append({
            "schema": FUTURE_PROJECTION_SCHEMA,
            "id": f"fp-{uuid.uuid4().hex[:12]}",
            "created_at": _now(),
            "run_id": rid,
            "source_storyline_id": sid,
            "source_backbone_id": sid,
            "future_vectors": vectors,
            "narratively_most_likely_vector_id": most_likely["id"],
            "projection_summary": f"{len(vectors)} possible continuation(s) for '{label}'.",
            "uncertainty_notes": ["advisory projection; never creates goals or grounded evidence"],
            "governance": {
                "may_influence_goal_pursuit": True,
                "may_create_goals": False,
                "may_create_beads": False,
                "may_create_claims": False,
                "may_create_overlays_without_acceptance": False,
            },
        })

    if persist and projections:
        from core_memory.persistence.io_utils import append_jsonl, store_lock

        path = _projections_path(root)
        with store_lock(Path(root)):
            for proj in projections:
                append_jsonl(path, proj)

    return {"ok": True, "run_id": rid, "projection_count": len(projections), "projections": projections}


def read_future_projections(root: str | Path, *, limit: int = 200) -> list[dict[str, Any]]:
    p = _projections_path(root)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and row.get("schema") == FUTURE_PROJECTION_SCHEMA:
                rows.append(row)
    return rows[-max(1, int(limit)):]


__all__ = [
    "FUTURE_PROJECTION_SCHEMA",
    "FUTURE_VECTOR_SCHEMA",
    "compute_future_projections",
    "read_future_projections",
]

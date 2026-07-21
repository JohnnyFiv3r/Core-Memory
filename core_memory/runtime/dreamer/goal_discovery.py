"""Dreamer V3 — goal discovery (PRD §14).

Dreamer may propose ``goal_candidate`` rows when repeated behavior suggests a
*latent goal* — but it can never make a goal authoritative (only a human/SOUL
decision can; §9, §10). This is the upstream of the Dreamer V4 target-state idea:
observed behavior → implied direction → goal candidate → human-endorsed goal.

Signal: a theme token (entity or topic) that recurs across **behavior** beads
(decisions/outcomes) with *distributed recurrence* — appearing in enough beads
across enough distinct sessions (PRD §11: persistence is distributed recurrence,
not age or a single-session loop) — and that no existing active goal already
covers. Candidates are hypotheses for review; the decide flow is the guardrail.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BEHAVIOR_TYPES = {"decision", "outcome"}
_INACTIVE_BEAD_STATUSES = {"superseded", "archived"}
_STOP_TOKENS = {"", "the", "and", "for", "with", "this", "that", "core", "memory"}


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


def _norm_token(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _is_active(bead: dict[str, Any]) -> bool:
    return str(bead.get("status") or "").strip().lower() not in _INACTIVE_BEAD_STATUSES \
        and str(bead.get("approval_status") or "").strip().lower() != "rejected"


def _theme_tokens(bead: dict[str, Any]) -> set[str]:
    """Curated theme tokens for a bead: entities + topics (not raw tags, which
    carry structural noise like source_system/bead-type)."""
    out: set[str] = set()
    for v in list(bead.get("entities") or []) + list(bead.get("topics") or []):
        t = _norm_token(v)
        if t and t not in _STOP_TOKENS and len(t) >= 3:
            out.add(t)
    return out


def _title_tokens(title: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", str(title or "").lower()) if len(w) >= 4}


def _existing_goal_themes(beads: dict[str, dict]) -> set[str]:
    """Theme tokens already covered by an active goal (entities + topics + title
    words) — so we never propose a goal that already exists."""
    themes: set[str] = set()
    for b in beads.values():
        if str(b.get("type") or "").strip().lower() != "goal" or not _is_active(b):
            continue
        themes |= _theme_tokens(b)
        themes |= _title_tokens(str(b.get("title") or ""))
    return themes


def detect_latent_goals(root: str | Path) -> list[dict[str, Any]]:
    """Return latent-goal detections: theme tokens recurring across behavior
    beads with distributed recurrence, not already covered by a goal."""
    min_occ = max(2, _int_env("CORE_MEMORY_GOAL_DISCOVERY_MIN_OCCURRENCES", 3))
    min_sessions = max(2, _int_env("CORE_MEMORY_GOAL_DISCOVERY_MIN_SESSIONS", 2))

    index = _read_index(root)
    beads = {str(k): v for k, v in (index.get("beads") or {}).items() if isinstance(v, dict)}
    for bid, b in beads.items():
        b.setdefault("id", bid)

    goal_themes = _existing_goal_themes(beads)

    theme_beads: dict[str, set[str]] = {}
    theme_sessions: dict[str, set[str]] = {}
    for bid, b in beads.items():
        if str(b.get("type") or "").strip().lower() not in _BEHAVIOR_TYPES or not _is_active(b):
            continue
        sess = str(b.get("session_id") or "")
        for token in _theme_tokens(b):
            if token in goal_themes:
                continue  # an active goal already covers this theme
            theme_beads.setdefault(token, set()).add(bid)
            theme_sessions.setdefault(token, set()).add(sess)

    out: list[dict[str, Any]] = []
    for token, bead_ids in theme_beads.items():
        sessions = theme_sessions.get(token, set())
        if len(bead_ids) < min_occ or len(sessions) < min_sessions:
            continue
        out.append({
            "theme": token,
            "statement": (
                f"Repeated behavior involving '{token}' across {len(sessions)} sessions "
                f"({len(bead_ids)} decisions/outcomes) suggests a latent goal."
            ),
            "supporting_bead_ids": sorted(bead_ids),
            "occurrence_count": len(bead_ids),
            "session_count": len(sessions),
        })
    out.sort(key=lambda d: (-d["session_count"], -d["occurrence_count"], d["theme"]))
    return out


def enqueue_latent_goal_candidates(
    root: str | Path,
    *,
    run_id: str | None = None,
    source: str = "dreamer_goal_discovery",
) -> dict[str, Any]:
    """Emit ``goal_candidate`` rows for new latent goals. Deduped by theme while a
    pending/accepted candidate covers it. Idempotent; candidates are hypotheses —
    they never create authoritative Goal Beads (§10)."""
    from core_memory.runtime.dreamer.candidates import _read_candidates, _write_candidates

    detections = detect_latent_goals(root)
    if not detections:
        return {"ok": True, "detected": 0, "enqueued": 0}

    rows = _read_candidates(root)
    blocked: set[str] = set()
    for r in rows:
        if str(r.get("hypothesis_type") or "") != "goal_candidate":
            continue
        if str(r.get("status") or "") in {"pending", "accepted"}:
            blocked.add(str(r.get("goal_theme") or ""))

    now = _now()
    rid = str(run_id or f"goaldisc-{uuid.uuid4().hex[:8]}")
    enqueued = 0
    for det in detections:
        if det["theme"] in blocked:
            continue
        rows.append({
            "id": f"dc-{uuid.uuid4().hex[:12]}",
            "created_at": now,
            "status": "pending",
            "hypothesis_type": "goal_candidate",
            "proposal_family": "goal",
            "benchmark_tags": ["goal", "discovery"],
            "goal_theme": det["theme"],
            # Deterministic baseline name; the candidate-refinement pass may
            # replace it with a reviewed, human-readable title before decide.
            "title": f"Recurring focus: {det['theme']}",
            "statement": det["statement"],
            "rationale": det["statement"],
            "expected_decision_impact": (
                "Accepting surfaces a latent goal for human/SOUL endorsement; "
                "Dreamer never creates an authoritative Goal Bead."
            ),
            "supporting_bead_ids": det["supporting_bead_ids"],
            "occurrence_count": det["occurrence_count"],
            "session_count": det["session_count"],
            "novelty": 0.0,
            "grounding": 1.0,
            "run_metadata": {"run_id": rid, "source": source},
        })
        enqueued += 1

    if enqueued:
        _write_candidates(root, rows)
    return {"ok": True, "detected": len(detections), "enqueued": enqueued, "run_id": rid}


__all__ = ["detect_latent_goals", "enqueue_latent_goal_candidates"]

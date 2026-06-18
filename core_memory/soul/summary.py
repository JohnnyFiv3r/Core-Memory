"""Read-only SOUL continuity summary.

This module builds product-neutral measurement packets over existing SOUL,
Dreamer, worldline/storyline, myelination, and candidate surfaces. The summary
is deliberately read-only: measurements are not evidence, do not mutate SOUL,
and do not alter graph truth.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.graph.storylines import derive_storylines
from core_memory.graph.worldlines import derive_worldlines
from core_memory.runtime.dreamer.assembly_depth import compute_assembly_depth
from core_memory.runtime.dreamer.tension_discovery import detect_goal_conflicts
from core_memory.soul.goals import list_goals
from core_memory.soul.store import current_soul_entries

SOUL_SUMMARY_SCHEMA = "soul_summary.v1"

_ENDORSED_GOAL_STATES = {"endorsed", "active"}
_TERMINAL_TENSION_STATUSES = {"resolved", "superseded", "inactive", "retracted"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_index(root: str | Path) -> dict[str, Any]:
    p = Path(root) / ".beads" / "index.json"
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_dreamer_candidates(root: str | Path) -> list[dict[str, Any]]:
    p = Path(root) / ".beads" / "events" / "dreamer-candidates.json"
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
    except Exception:
        pass
    return []


def _parse_time(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _span_days(span: dict[str, Any]) -> float | None:
    start = _parse_time(span.get("from"))
    end = _parse_time(span.get("to"))
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds() / 86400.0)


def _p90(values: list[float]) -> float | None:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 6)
    idx = min(len(vals) - 1, int(0.9 * (len(vals) - 1)))
    return round(vals[idx], 6)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _goal_horizon_days(bead: dict[str, Any]) -> float | None:
    for field in (
        "target_horizon_days",
        "time_horizon_days",
        "horizon_days",
        "target_state_horizon_days",
    ):
        raw = bead.get(field)
        if raw is None or raw == "":
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val >= 0:
            return round(val, 6)

    target_at = None
    for field in ("target_at", "target_date", "due_at", "due_date"):
        target_at = _parse_time(bead.get(field))
        if target_at:
            break
    if not target_at:
        return None
    created = _parse_time(bead.get("created_at")) or datetime.now(timezone.utc)
    return round(max(0.0, (target_at - created).total_seconds() / 86400.0), 6)


def _sessions_for_beads(beads: dict[str, Any], bead_ids: list[str]) -> list[str]:
    sessions: set[str] = set()
    for bid in bead_ids:
        row = beads.get(str(bid))
        if isinstance(row, dict):
            sess = str(row.get("session_id") or "").strip()
            if sess:
                sessions.add(sess)
    return sorted(sessions)


def _assembly_depth_value(value: Any) -> float:
    if isinstance(value, dict):
        vals = []
        for raw in value.values():
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        if vals:
            return round(sum(vals) / len(vals), 6)
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


def _status_from_limitations(limitations: list[str], *, unavailable: bool = False) -> str:
    if unavailable:
        return "unavailable"
    return "partial" if limitations else "complete"


def _build_light_cone(root: str | Path, subject: str, index: dict[str, Any]) -> dict[str, Any]:
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    limitations: list[str] = []
    breakdown: list[dict[str, Any]] = []

    goals_payload = list_goals(str(root), subject=subject, include_terminal=False)
    goals = list(goals_payload.get("goals") or []) if goals_payload.get("ok") else []
    endorsed_goals = [g for g in goals if str(g.get("state") or "") in _ENDORSED_GOAL_STATES]

    if not endorsed_goals:
        limitations.append("no_endorsed_or_active_goals")

    scopes: set[str] = set()
    horizons: list[float] = []
    for goal in endorsed_goals:
        bid = str(goal.get("bead_id") or "")
        bead = beads.get(bid) if isinstance(beads.get(bid), dict) else {}
        scope = str(
            bead.get("subject_scope")
            or bead.get("goal_subject_scope")
            or bead.get("scope")
            or "project"
        )
        if scope:
            scopes.add(scope)
        horizon = _goal_horizon_days(bead)
        if horizon is not None:
            horizons.append(horizon)
        breakdown.append(
            {
                "kind": "goal",
                "goal_id": str(goal.get("goal_id") or ""),
                "bead_id": bid,
                "title": str(goal.get("title") or ""),
                "state": str(goal.get("state") or ""),
                "subject_scope": scope,
                "target_horizon_days": horizon,
            }
        )

    if endorsed_goals and not horizons:
        limitations.append("endorsed_goal_horizons_unavailable")

    try:
        worldlines = list(derive_worldlines(root).get("worldlines") or [])
    except Exception:
        worldlines = []
        limitations.append("worldline_projection_unavailable")

    spans: list[float] = []
    for wl in worldlines:
        span = _span_days(wl.get("span") or {})
        if span is not None:
            spans.append(span)
            if len(breakdown) < 25:
                breakdown.append(
                    {
                        "kind": "worldline",
                        "worldline_id": str(wl.get("id") or ""),
                        "worldline_kind": str(wl.get("kind") or ""),
                        "label": str(wl.get("label") or ""),
                        "span_days": span,
                        "bead_ids": list(wl.get("bead_ids") or []),
                    }
                )
    if worldlines and not spans:
        limitations.append("worldline_span_timestamps_unavailable")

    try:
        total_goals = max(
            1,
            sum(
                1
                for bead in beads.values()
                if isinstance(bead, dict)
                and str(bead.get("type") or "").strip().lower() == "goal"
            ),
        )
        reports = list(
            compute_assembly_depth(root, target_kind="goal", limit=total_goals).get("reports")
            or []
        )
    except Exception:
        reports = []
        limitations.append("goal_assembly_depth_unavailable")

    binding_values: list[float] = []
    for rep in reports:
        tid = str(rep.get("target_id") or "")
        if tid not in {str(g.get("bead_id") or "") for g in endorsed_goals}:
            continue
        depth = float(rep.get("score") or 0.0)
        raw = ((rep.get("components") or {}).get("factors_raw") or {})
        causal = float(raw.get("causal_dependency_count") or 0.0)
        myelinated = float(raw.get("myelinated_path_support") or 0.0)
        survival = float(raw.get("supersession_survival") or 0.0)
        mass = depth * (1.0 + causal + myelinated + survival)
        binding_values.append(mass)
    binding_mass = round(sum(binding_values), 6) if binding_values else 0.0
    if endorsed_goals and not binding_values:
        limitations.append("endorsed_goal_binding_mass_unavailable")
    limitations.append("non_bead_assembly_depth_unavailable")

    horizon = _p90(horizons)
    worldline_span = _p90(spans)
    spatial = len(scopes)
    light_cone_index = _clamp01(
        (min(1.0, spatial / 3.0) * 0.34)
        + (min(1.0, float(horizon or 0.0) / 365.0) * 0.33)
        + (min(1.0, binding_mass / 10.0) * 0.33)
    )

    return {
        "status": _status_from_limitations(limitations),
        "light_cone_index": round(light_cone_index, 6),
        "spatial_scope_count": spatial,
        "temporal_horizon_days_p90": horizon,
        "worldline_span_days_p90": worldline_span,
        "binding_mass": binding_mass,
        "breakdown": breakdown,
        "limitations": sorted(set(limitations)),
    }


def _candidate_base(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": str(row.get("id") or ""),
        "status": str(row.get("status") or ""),
        "hypothesis_type": str(row.get("hypothesis_type") or ""),
        "statement": str(row.get("statement") or row.get("rationale") or ""),
        "supporting_bead_ids": list(row.get("supporting_bead_ids") or []),
        "created_at": str(row.get("created_at") or ""),
        "grounding": row.get("grounding"),
        "confidence": row.get("confidence"),
    }


def _build_divergence(root: str | Path, subject: str) -> dict[str, Any]:
    candidates = [
        row
        for row in _read_dreamer_candidates(root)
        if str(row.get("subject") or subject) == subject
        and str(row.get("status") or "") in {"pending", "accepted", "deferred"}
    ]
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    for row in candidates:
        ht = str(row.get("hypothesis_type") or "")
        if ht == "value_candidate":
            positive.append(
                {
                    **_candidate_base(row),
                    "value_theme": str(row.get("value_theme") or ""),
                    "occurrence_count": int(row.get("occurrence_count") or 0),
                    "session_count": int(row.get("session_count") or 0),
                }
            )
        elif ht == "identity_divergence_candidate":
            negative.append(
                {
                    **_candidate_base(row),
                    "identity_entry_key": str(row.get("identity_entry_key") or ""),
                    "source_revision_id": str(row.get("source_revision_id") or ""),
                }
            )

    raw_weight = sum(max(1, int(p.get("session_count") or 1)) for p in positive)
    raw_weight += len(negative)
    limitations: list[str] = []
    try:
        current_soul_entries(root, file_name="IDENTITY.md", subject=subject)
    except Exception:
        limitations.append("identity_entries_unavailable")

    return {
        "status": _status_from_limitations(limitations),
        "divergence_index": round(_clamp01(raw_weight / 10.0), 6),
        "positive_observed_not_endorsed": positive,
        "negative_endorsed_not_observed": negative,
        "limitations": limitations,
    }


def _tension_status_from_entry(entry_key: str, entry: dict[str, Any]) -> str:
    text = f"{entry_key} {entry.get('content') or ''}".lower()
    if any(token in text for token in ("resolved", "superseded", "inactive", "retracted")):
        return "resolved"
    if "candidate" in text:
        return "candidate"
    return "active"


def _build_tensions(root: str | Path, subject: str, index: dict[str, Any]) -> dict[str, Any]:
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    tensions: list[dict[str, Any]] = []
    limitations: list[str] = []

    try:
        entries = current_soul_entries(root, file_name="TENSIONS.md", subject=subject).get("entries") or {}
    except Exception:
        entries = {}
        limitations.append("tension_entries_unavailable")

    for key, entry in entries.items():
        status = _tension_status_from_entry(str(key), entry or {})
        tensions.append(
            {
                "id": f"soul:{key}",
                "source": "soul_file",
                "status": status,
                "kind": "soul_tension",
                "title": str(key),
                "statement": str((entry or {}).get("content") or ""),
                "recurrence_count": 1,
                "sessions": [],
                "periods_spanned": 0,
                "assembly_depth": 0.0,
                "persistence_qualified": False,
                "related_goals": [],
                "related_worldlines": [],
                "related_identity_entries": [],
                "evidence_refs": [
                    {"type": "soul_revision", "id": str((entry or {}).get("revision_id") or "")}
                ],
            }
        )

    for row in _read_dreamer_candidates(root):
        if str(row.get("hypothesis_type") or "") != "tension_candidate":
            continue
        bead_ids = [str(b) for b in (row.get("supporting_bead_ids") or []) if str(b)]
        sessions = _sessions_for_beads(beads, bead_ids)
        depth = _assembly_depth_value(row.get("assembly_depth"))
        persistence = len(sessions) >= 2
        tensions.append(
            {
                "id": str(row.get("id") or ""),
                "source": "dreamer_candidate",
                "status": "candidate" if str(row.get("status") or "") == "pending" else str(row.get("status") or ""),
                "kind": str(row.get("tension_kind") or row.get("proposal_family") or "tension"),
                "title": str(row.get("tension_key") or row.get("id") or ""),
                "statement": str(row.get("statement") or row.get("rationale") or ""),
                "recurrence_count": len(bead_ids),
                "sessions": sessions,
                "periods_spanned": len(sessions),
                "assembly_depth": depth,
                "persistence_qualified": persistence,
                "related_goals": [
                    str(b)
                    for b in (row.get("conflict_bead_a"), row.get("conflict_bead_b"))
                    if str(b or "")
                ],
                "related_worldlines": [],
                "related_identity_entries": [],
                "evidence_refs": [{"type": "bead", "id": bid} for bid in bead_ids],
            }
        )

    try:
        storylines = list(derive_storylines(root).get("storylines") or [])
    except Exception:
        storylines = []
        limitations.append("storyline_projection_unavailable")

    for storyline in storylines:
        backbone = storyline.get("backbone") or {}
        bead_ids = [str(b) for b in (backbone.get("bead_ids") or []) if str(b)]
        sessions = _sessions_for_beads(beads, bead_ids)
        for idx, tension in enumerate(storyline.get("tensions") or []):
            tensions.append(
                {
                    "id": f"storyline:{storyline.get('id')}:{idx}",
                    "source": "storyline_projection",
                    "status": "active",
                    "kind": str(tension.get("kind") or "storyline_tension"),
                    "title": str(tension.get("kind") or "storyline tension"),
                    "statement": str(tension.get("detail") or ""),
                    "recurrence_count": len(bead_ids),
                    "sessions": sessions,
                    "periods_spanned": len(sessions),
                    "assembly_depth": 0.0,
                    "persistence_qualified": len(sessions) >= 2,
                    "related_goals": [],
                    "related_worldlines": [str(backbone.get("id") or "")],
                    "related_identity_entries": [],
                    "evidence_refs": [{"type": "bead", "id": bid} for bid in bead_ids],
                }
            )

    try:
        for det in detect_goal_conflicts(root):
            bead_ids = [
                str(b)
                for b in (det.get("conflict_bead_a"), det.get("conflict_bead_b"))
                if str(b or "")
            ]
            sessions = _sessions_for_beads(beads, bead_ids)
            tensions.append(
                {
                    "id": str(det.get("tension_key") or ""),
                    "source": "goal_conflict_detection",
                    "status": "candidate",
                    "kind": str(det.get("tension_kind") or "goal_conflict"),
                    "title": str(det.get("tension_key") or ""),
                    "statement": str(det.get("statement") or ""),
                    "recurrence_count": len(bead_ids),
                    "sessions": sessions,
                    "periods_spanned": len(sessions),
                    "assembly_depth": _assembly_depth_value(det.get("assembly_depth")),
                    "persistence_qualified": len(sessions) >= 2,
                    "related_goals": bead_ids,
                    "related_worldlines": [],
                    "related_identity_entries": [],
                    "evidence_refs": [{"type": "bead", "id": bid} for bid in bead_ids],
                }
            )
    except Exception:
        limitations.append("goal_conflict_detection_unavailable")

    deduped: dict[str, dict[str, Any]] = {}
    for tension in tensions:
        key = "|".join(
            [
                str(tension.get("source") or ""),
                str(tension.get("id") or tension.get("title") or ""),
            ]
        )
        deduped.setdefault(key, tension)
    tension_rows = list(deduped.values())

    active_rows = [
        t
        for t in tension_rows
        if str(t.get("status") or "").lower() not in _TERMINAL_TENSION_STATUSES
    ]
    active_load = round(
        sum(max(0.1, float(t.get("assembly_depth") or 0.0)) for t in active_rows),
        6,
    )
    persistence_count = sum(1 for t in active_rows if bool(t.get("persistence_qualified")))
    if tension_rows:
        limitations.append("tension_churn_history_unavailable")

    return {
        "status": _status_from_limitations(limitations),
        "active_load": active_load,
        "persistence_qualified_count": persistence_count,
        "new_tension_rate": None,
        "resolution_rate": None,
        "churn": None,
        "tensions": tension_rows,
        "limitations": sorted(set(limitations)),
    }


def build_soul_summary(root: str | Path, *, subject: str = "self") -> dict[str, Any]:
    """Build the read-only SOUL continuity summary for ``subject``."""
    index = _read_index(root)
    return {
        "ok": True,
        "schema": SOUL_SUMMARY_SCHEMA,
        "subject": str(subject or "self"),
        "generated_at": _now(),
        "measurements_are_evidence": False,
        "light_cone_breadth": _build_light_cone(root, str(subject or "self"), index),
        "observed_endorsed_divergence": _build_divergence(root, str(subject or "self")),
        "persistent_tensions": _build_tensions(root, str(subject or "self"), index),
    }


__all__ = ["SOUL_SUMMARY_SCHEMA", "build_soul_summary"]

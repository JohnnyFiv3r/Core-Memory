"""Read-only SOUL continuity summary.

This module builds product-neutral measurement packets over existing SOUL,
Dreamer, worldline/storyline, myelination, and candidate surfaces. The summary
is deliberately read-only: measurements are not evidence, do not mutate SOUL,
and do not alter graph truth.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.graph.storylines import derive_storylines
from core_memory.graph.worldlines import derive_worldlines
from core_memory.runtime.dreamer.assembly_depth import compute_assembly_depth
from core_memory.soul.goals import list_goals
from core_memory.soul.identity_value_signals import detect_identity_value_findings
from core_memory.soul.store import current_soul_entries
from core_memory.soul.tension_signals import detect_goal_conflicts

SOUL_SUMMARY_SCHEMA = "soul_summary.v1"

_ENDORSED_GOAL_STATES = {"endorsed", "active"}
_TERMINAL_TENSION_STATUSES = {"resolved", "superseded", "inactive", "retracted"}
_TENSION_RATE_WINDOW_DAYS = 30.0
_INACTIVE_ASSOC_STATUSES = {"retracted", "superseded", "inactive"}
_LIGHT_CONE_STOPWORDS = {
    "about",
    "after",
    "against",
    "and",
    "because",
    "been",
    "being",
    "between",
    "from",
    "have",
    "into",
    "that",
    "their",
    "this",
    "through",
    "value",
    "with",
}


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


def _timestamps_for_beads(beads: dict[str, Any], bead_ids: list[str]) -> list[datetime]:
    out: list[datetime] = []
    for bid in bead_ids:
        row = beads.get(str(bid))
        if not isinstance(row, dict):
            continue
        for field in ("observed_at", "recorded_at", "created_at"):
            dt = _parse_time(row.get(field))
            if dt:
                out.append(dt)
                break
    return out


def _periods_for_timestamps(timestamps: list[datetime]) -> list[str]:
    return sorted({dt.astimezone(timezone.utc).date().isoformat() for dt in timestamps})


def _source_ref_token(kind: str, value: Any) -> str:
    s = str(value or "").strip()
    return f"{kind}:{s}" if s else ""


def _source_refs_from_rows(rows: list[Any]) -> list[str]:
    refs: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            token = _source_ref_token("source_ref", row)
            if token:
                refs.add(token)
            continue
        if not isinstance(row, dict):
            continue
        kind = str(row.get("source_kind") or row.get("kind") or row.get("type") or "source").strip()
        ref = (
            row.get("source_ref")
            or row.get("external_id")
            or row.get("id")
            or row.get("url")
            or row.get("path")
        )
        token = _source_ref_token(kind, ref)
        if token:
            refs.add(token)
    return sorted(refs)


def _source_refs_for_beads(beads: dict[str, Any], bead_ids: list[str]) -> list[str]:
    refs: set[str] = set()
    scalar_fields = (
        "source_ref",
        "source_id",
        "source_event_id",
        "raw_source_object_id",
        "document_id",
        "ragie_document_id",
        "conversation_id",
        "transcript_id",
        "source_session_id",
    )
    for bid in bead_ids:
        row = beads.get(str(bid))
        if not isinstance(row, dict):
            continue
        for field in scalar_fields:
            token = _source_ref_token(field, row.get(field))
            if token:
                refs.add(token)
        refs.update(_source_refs_from_rows(list(row.get("source_refs") or [])))
        attr = row.get("source_attribution")
        if isinstance(attr, dict):
            refs.update(_source_refs_from_rows([attr]))
    return sorted(refs)


def _source_refs_from_evidence(evidence: list[Any]) -> list[str]:
    return _source_refs_from_rows(evidence)


def _iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).isoformat() if dt else ""


def _age_days(first_seen: datetime | None, now: datetime) -> float | None:
    if not first_seen:
        return None
    return round(max(0.0, (now - first_seen).total_seconds() / 86400.0), 6)


def _evidence_refs_for_beads(bead_ids: list[str]) -> list[dict[str, str]]:
    return [{"type": "bead", "id": str(bid)} for bid in bead_ids if str(bid)]


def _evidence_refs_for_soul_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    rid = str(entry.get("revision_id") or "").strip()
    if rid:
        refs.append({"type": "soul_revision", "id": rid})
    refs.extend([dict(e) for e in (entry.get("evidence") or []) if isinstance(e, dict)])
    return refs


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(v) for v in value)
    return str(value)


def _tokens_for_text(*values: Any) -> set[str]:
    text = " ".join(_flatten_text(value) for value in values).lower()
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text)
        if len(token) >= 4 and token not in _LIGHT_CONE_STOPWORDS
    }


def _bead_tokens(bead: dict[str, Any]) -> set[str]:
    return _tokens_for_text(
        bead.get("title"),
        bead.get("summary"),
        bead.get("detail"),
        bead.get("because"),
        bead.get("topics"),
        bead.get("entities"),
        bead.get("type"),
    )


def _supporting_beads_for_text(
    beads: dict[str, Any],
    *values: Any,
    limit: int = 12,
) -> list[str]:
    target = _tokens_for_text(*values)
    if not target:
        return []
    scored: list[tuple[int, str]] = []
    for bid, bead in beads.items():
        if not isinstance(bead, dict):
            continue
        score = len(target & _bead_tokens(bead))
        if score > 0:
            scored.append((score, str(bid)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [bid for _, bid in scored[: max(1, limit)]]


def _persistence_qualified(*, sessions: list[str], periods: list[str], source_refs: list[str]) -> bool:
    return len(set(sessions)) >= 2 or len(set(periods)) >= 2 or len(set(source_refs)) >= 2


def _tension_observation_fields(
    *,
    beads: dict[str, Any],
    bead_ids: list[str],
    now: datetime,
    created_at: Any = None,
    latest_at: Any = None,
    resolved_at: Any = None,
    source_refs: list[str] | None = None,
) -> dict[str, Any]:
    timestamps = _timestamps_for_beads(beads, bead_ids)
    for raw in (created_at, latest_at, resolved_at):
        dt = _parse_time(raw)
        if dt:
            timestamps.append(dt)
    first_seen = min(timestamps) if timestamps else None
    latest_seen = max(timestamps) if timestamps else None
    periods = _periods_for_timestamps(timestamps)
    refs = sorted(set(source_refs or []) | set(_source_refs_for_beads(beads, bead_ids)))
    return {
        "source_refs": refs,
        "periods": periods,
        "periods_spanned": len(periods),
        "first_seen_at": _iso(first_seen),
        "latest_seen_at": _iso(latest_seen),
        "resolved_at": _iso(_parse_time(resolved_at)),
        "age_days": _age_days(first_seen, now),
        "persistence_qualified": _persistence_qualified(
            sessions=_sessions_for_beads(beads, bead_ids),
            periods=periods,
            source_refs=refs,
        ),
    }


def _compute_tension_rates(
    tension_rows: list[dict[str, Any]],
    now: datetime,
) -> tuple[float | None, float | None, float | None, list[str]]:
    dated_created = [_parse_time(row.get("first_seen_at")) for row in tension_rows]
    dated_created = [dt for dt in dated_created if dt]
    terminal_rows = [
        row
        for row in tension_rows
        if str(row.get("status") or "").lower() in _TERMINAL_TENSION_STATUSES
    ]
    dated_resolved = [
        _parse_time(row.get("resolved_at")) or _parse_time(row.get("latest_seen_at"))
        for row in terminal_rows
    ]
    dated_resolved = [dt for dt in dated_resolved if dt]

    if len(dated_created) < 2 and not dated_resolved:
        return None, None, None, ["tension_churn_history_unavailable"] if tension_rows else []

    all_dates = dated_created + dated_resolved
    start = min(all_dates) if all_dates else now
    end = max(max(all_dates), now) if all_dates else now
    period_days = max(1.0, (end - start).total_seconds() / 86400.0)
    new_count = sum(1 for row in tension_rows if _parse_time(row.get("first_seen_at")))
    resolved_count = len(dated_resolved)
    new_rate = round((new_count / period_days) * _TENSION_RATE_WINDOW_DAYS, 6)
    resolution_rate = round((resolved_count / period_days) * _TENSION_RATE_WINDOW_DAYS, 6)
    churn = round(((new_count + resolved_count) / period_days) * _TENSION_RATE_WINDOW_DAYS, 6)
    return new_rate, resolution_rate, churn, []


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


def _association_dependency_count(index: dict[str, Any], bead_ids: list[str]) -> int:
    members = {str(bid) for bid in bead_ids if str(bid)}
    if len(members) < 2:
        return 0
    count = 0
    for assoc in index.get("associations") or []:
        if not isinstance(assoc, dict):
            continue
        status = str(assoc.get("status") or "active").strip().lower() or "active"
        if status in _INACTIVE_ASSOC_STATUSES:
            continue
        src = str(assoc.get("source_bead") or assoc.get("source_bead_id") or "")
        tgt = str(assoc.get("target_bead") or assoc.get("target_bead_id") or "")
        if src in members and tgt in members and src != tgt:
            count += 1
    return count


def _storyline_binding_report(storyline: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    backbone = storyline.get("backbone") if isinstance(storyline.get("backbone"), dict) else {}
    bead_ids = [str(b) for b in (backbone.get("bead_ids") or []) if str(b)]
    if not bead_ids:
        return None
    span_days = _span_days(backbone.get("span") or {})
    overlay_count = len(storyline.get("overlays") or [])
    tension_count = len(storyline.get("tensions") or [])
    causal_count = _association_dependency_count(index, bead_ids)
    length_factor = min(1.0, len(bead_ids) / 5.0)
    span_factor = min(1.0, float(span_days or 0.0) / 365.0)
    overlay_factor = min(1.0, overlay_count / 3.0)
    tension_factor = min(1.0, tension_count / 3.0)
    assembly_depth = round(
        (length_factor * 0.45)
        + (span_factor * 0.25)
        + (overlay_factor * 0.2)
        + (tension_factor * 0.1),
        6,
    )
    binding_mass = round(assembly_depth * (1.0 + causal_count + overlay_count + tension_count), 6)
    return {
        "kind": "storyline",
        "storyline_id": str(storyline.get("id") or ""),
        "worldline_id": str(backbone.get("id") or ""),
        "worldline_kind": str(backbone.get("kind") or ""),
        "label": str(backbone.get("label") or ""),
        "bead_ids": bead_ids,
        "span_days": span_days,
        "storyline_span_days": span_days,
        "assembly_depth": assembly_depth,
        "binding_mass_component": binding_mass,
        "causal_dependency_count": causal_count,
        "overlay_count": overlay_count,
        "tension_count": tension_count,
        "evidence_refs": _evidence_refs_for_beads(bead_ids),
        "source_refs": _source_refs_for_beads(
            index.get("beads") if isinstance(index.get("beads"), dict) else {},
            bead_ids,
        ),
    }


def _identity_light_cone_reports(
    root: str | Path,
    subject: str,
    beads: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    limitations: list[str] = []
    try:
        entries = (
            current_soul_entries(root, file_name="IDENTITY.md", subject=subject).get("entries")
            or {}
        )
    except Exception:
        return [], ["identity_projection_unavailable"]

    reports: list[dict[str, Any]] = []
    for key, entry in sorted(entries.items(), key=lambda item: str(item[0])):
        if not isinstance(entry, dict):
            continue
        epistemic_status = str(entry.get("epistemic_status") or "").strip().lower()
        if epistemic_status != "endorsed":
            continue
        content = str(entry.get("content") or "")
        supporting_bead_ids = _supporting_beads_for_text(beads, key, content)
        sessions = _sessions_for_beads(beads, supporting_bead_ids)
        source_refs = sorted(
            set(_source_refs_from_evidence(list(entry.get("evidence") or [])))
            | set(_source_refs_for_beads(beads, supporting_bead_ids))
        )
        support_factor = min(1.0, len(supporting_bead_ids) / 4.0)
        session_factor = min(1.0, len(sessions) / 3.0)
        source_factor = min(1.0, len(source_refs) / 3.0)
        assembly_depth = round(
            (support_factor * 0.45)
            + (session_factor * 0.25)
            + (source_factor * 0.15)
            + 0.15,
            6,
        )
        binding_mass = round(
            assembly_depth * (1.0 + len(supporting_bead_ids) + len(source_refs)),
            6,
        )
        reports.append(
            {
                "kind": "identity_entry",
                "source": "soul_file",
                "entry_key": str(key),
                "epistemic_status": epistemic_status,
                "revision_id": str(entry.get("revision_id") or ""),
                "content": content,
                "supporting_bead_ids": supporting_bead_ids,
                "session_count": len(sessions),
                "sessions": sessions,
                "assembly_depth": assembly_depth,
                "binding_mass_component": binding_mass,
                "contributes_to_primary_horizon": False,
                "evidence_refs": _evidence_refs_for_soul_entry(entry),
                "source_refs": source_refs,
            }
        )

    return reports, limitations


def _tension_light_cone_reports(
    root: str | Path,
    subject: str,
    index: dict[str, Any],
    tension_summary: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if tension_summary is None:
        try:
            summary = _build_tensions(root, subject, index)
        except Exception:
            return [], ["tension_projection_unavailable"]
    else:
        summary = tension_summary

    reports: list[dict[str, Any]] = []
    for row in summary.get("tensions") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in _TERMINAL_TENSION_STATUSES:
            continue
        recurrence_count = max(1, int(row.get("recurrence_count") or 1))
        periods_spanned = int(row.get("periods_spanned") or 0)
        source_refs = [str(ref) for ref in (row.get("source_refs") or []) if str(ref)]
        source_factor = min(1.0, len(source_refs) / 3.0)
        recurrence_factor = min(1.0, recurrence_count / 3.0)
        period_factor = min(1.0, periods_spanned / 3.0)
        persisted = bool(row.get("persistence_qualified"))
        base_depth = max(0.1, _assembly_depth_value(row.get("assembly_depth")))
        assembly_depth = round(
            min(
                1.0,
                (base_depth * 0.5)
                + (recurrence_factor * 0.18)
                + (period_factor * 0.12)
                + (source_factor * 0.1)
                + (0.1 if persisted else 0.0),
            ),
            6,
        )
        binding_mass = round(
            assembly_depth * (1.0 + recurrence_count + periods_spanned + len(source_refs)),
            6,
        )
        reports.append(
            {
                "kind": "tension",
                "source": row.get("source"),
                "tension_id": str(row.get("id") or ""),
                "status": status,
                "tension_kind": str(row.get("kind") or ""),
                "title": str(row.get("title") or ""),
                "statement": str(row.get("statement") or ""),
                "recurrence_count": recurrence_count,
                "persistence_qualified": persisted,
                "periods_spanned": periods_spanned,
                "sessions": list(row.get("sessions") or []),
                "assembly_depth": assembly_depth,
                "binding_mass_component": binding_mass,
                "related_goals": list(row.get("related_goals") or []),
                "related_worldlines": list(row.get("related_worldlines") or []),
                "related_identity_entries": list(row.get("related_identity_entries") or []),
                "contributes_to_primary_horizon": False,
                "evidence_refs": list(row.get("evidence_refs") or []),
                "source_refs": source_refs,
            }
        )

    limitations = [
        str(item)
        for item in (summary.get("limitations") or [])
        if str(item) and str(item) != "tension_churn_history_unavailable"
    ]
    return reports, limitations


def _build_light_cone(
    root: str | Path,
    subject: str,
    index: dict[str, Any],
    tension_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    limitations: list[str] = []
    breakdown: list[dict[str, Any]] = []

    goals_payload = list_goals(str(root), subject=subject, include_terminal=False)
    goals = list(goals_payload.get("goals") or []) if goals_payload.get("ok") else []
    endorsed_goals = [g for g in goals if str(g.get("state") or "") in _ENDORSED_GOAL_STATES]
    candidate_goals = [g for g in goals if str(g.get("state") or "") not in _ENDORSED_GOAL_STATES]

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
                "contributes_to_primary_horizon": True,
                "evidence_refs": _evidence_refs_for_beads([bid]),
                "source_refs": _source_refs_for_beads(beads, [bid]),
            }
        )
    for goal in candidate_goals:
        bid = str(goal.get("bead_id") or "")
        bead = beads.get(bid) if isinstance(beads.get(bid), dict) else {}
        breakdown.append(
            {
                "kind": "goal",
                "goal_id": str(goal.get("goal_id") or ""),
                "bead_id": bid,
                "title": str(goal.get("title") or ""),
                "state": str(goal.get("state") or ""),
                "subject_scope": str(
                    bead.get("subject_scope")
                    or bead.get("goal_subject_scope")
                    or bead.get("scope")
                    or "project"
                ),
                "target_horizon_days": _goal_horizon_days(bead),
                "contributes_to_primary_horizon": False,
                "evidence_refs": _evidence_refs_for_beads([bid]),
                "source_refs": _source_refs_for_beads(beads, [bid]),
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
                        "evidence_refs": _evidence_refs_for_beads(list(wl.get("bead_ids") or [])),
                        "source_refs": _source_refs_for_beads(beads, list(wl.get("bead_ids") or [])),
                    }
                )
    if worldlines and not spans:
        limitations.append("worldline_span_timestamps_unavailable")

    try:
        storylines = list(derive_storylines(root).get("storylines") or [])
    except Exception:
        storylines = []
        limitations.append("storyline_projection_unavailable")

    storyline_spans: list[float] = []
    storyline_binding_values: list[float] = []
    for storyline in storylines:
        report = _storyline_binding_report(storyline, index)
        if not report:
            continue
        if report.get("storyline_span_days") is not None:
            storyline_spans.append(float(report["storyline_span_days"]))
        storyline_binding_values.append(float(report.get("binding_mass_component") or 0.0))
        if len(breakdown) < 25:
            breakdown.append(report)

    tension_binding_values: list[float] = []
    tension_reports, tension_limitations = _tension_light_cone_reports(
        root,
        subject,
        index,
        tension_summary=tension_summary,
    )
    limitations.extend(tension_limitations)
    for report in tension_reports:
        tension_binding_values.append(float(report.get("binding_mass_component") or 0.0))
        if len(breakdown) < 25:
            breakdown.append(report)

    identity_binding_values: list[float] = []
    identity_reports, identity_limitations = _identity_light_cone_reports(root, subject, beads)
    limitations.extend(identity_limitations)
    for report in identity_reports:
        identity_binding_values.append(float(report.get("binding_mass_component") or 0.0))
        if len(breakdown) < 25:
            breakdown.append(report)

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

    goal_binding_values: list[float] = []
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
        goal_binding_values.append(mass)
        for row in breakdown:
            if row.get("kind") == "goal" and row.get("bead_id") == tid:
                row["assembly_depth"] = round(depth, 6)
                row["binding_mass_component"] = round(mass, 6)
                row["causal_dependency_count"] = causal
                row["myelinated_path_support"] = myelinated
                row["supersession_survival"] = survival
                break
    binding_values = (
        goal_binding_values
        + storyline_binding_values
        + tension_binding_values
        + identity_binding_values
    )
    binding_mass = round(sum(binding_values), 6) if binding_values else 0.0
    if endorsed_goals and not goal_binding_values:
        limitations.append("endorsed_goal_binding_mass_unavailable")
    if not (storyline_binding_values or tension_binding_values or identity_binding_values):
        limitations.append("non_bead_assembly_depth_unavailable")

    horizon = _p90(horizons)
    worldline_span = _p90(spans)
    storyline_span = _p90(storyline_spans)
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
        "storyline_span_days_p90": storyline_span,
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


def _build_divergence(root: str | Path, subject: str, index: dict[str, Any]) -> dict[str, Any]:
    beads = index.get("beads") if isinstance(index.get("beads"), dict) else {}
    limitations: list[str] = []
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
            bead_ids = [str(b) for b in (row.get("supporting_bead_ids") or []) if str(b)]
            sessions = _sessions_for_beads(beads, bead_ids)
            positive.append(
                {
                    **_candidate_base(row),
                    "source": "dreamer_candidate",
                    "value_theme": str(row.get("value_theme") or ""),
                    "occurrence_count": int(row.get("occurrence_count") or 0),
                    "session_count": int(row.get("session_count") or 0),
                    "sessions": sessions,
                    "source_refs": _source_refs_for_beads(beads, bead_ids),
                }
            )
        elif ht == "identity_divergence_candidate":
            bead_ids = [str(b) for b in (row.get("supporting_bead_ids") or []) if str(b)]
            sessions = _sessions_for_beads(beads, bead_ids)
            negative.append(
                {
                    **_candidate_base(row),
                    "source": "dreamer_candidate",
                    "identity_entry_key": str(row.get("identity_entry_key") or ""),
                    "source_revision_id": str(row.get("source_revision_id") or ""),
                    "session_count": len(sessions),
                    "sessions": sessions,
                    "source_refs": _source_refs_for_beads(beads, bead_ids),
                }
            )

    candidate_value_keys = {str(row.get("value_theme") or "") for row in positive}
    candidate_identity_keys = {str(row.get("identity_entry_key") or "") for row in negative}
    try:
        findings = detect_identity_value_findings(root, subject=subject)
    except Exception:
        findings = []
        limitations.append("identity_value_detection_unavailable")

    for finding in findings:
        kind = str(finding.get("finding") or "")
        if kind == "value_candidate":
            theme = str(finding.get("value_theme") or "")
            if not theme or theme in candidate_value_keys:
                continue
            bead_ids = [str(b) for b in (finding.get("supporting_bead_ids") or []) if str(b)]
            sessions = _sessions_for_beads(beads, bead_ids)
            positive.append(
                {
                    "candidate_id": "",
                    "source": "deterministic_projection",
                    "status": "observed",
                    "hypothesis_type": "value_candidate",
                    "statement": str(finding.get("statement") or ""),
                    "supporting_bead_ids": bead_ids,
                    "created_at": "",
                    "grounding": 1.0,
                    "confidence": None,
                    "value_theme": theme,
                    "occurrence_count": int(finding.get("occurrence_count") or len(bead_ids)),
                    "session_count": int(finding.get("session_count") or len(sessions)),
                    "sessions": sessions,
                    "source_refs": _source_refs_for_beads(beads, bead_ids),
                }
            )
            candidate_value_keys.add(theme)
        elif kind == "identity_divergence_candidate":
            key = str(finding.get("identity_entry_key") or "")
            if not key or key in candidate_identity_keys:
                continue
            bead_ids = [str(b) for b in (finding.get("supporting_bead_ids") or []) if str(b)]
            sessions = _sessions_for_beads(beads, bead_ids)
            negative.append(
                {
                    "candidate_id": "",
                    "source": "deterministic_projection",
                    "status": "observed",
                    "hypothesis_type": "identity_divergence_candidate",
                    "statement": str(finding.get("statement") or ""),
                    "supporting_bead_ids": bead_ids,
                    "created_at": "",
                    "grounding": 1.0,
                    "confidence": None,
                    "identity_entry_key": key,
                    "source_revision_id": str(finding.get("source_revision_id") or ""),
                    "session_count": len(sessions),
                    "sessions": sessions,
                    "source_refs": _source_refs_for_beads(beads, bead_ids),
                }
            )
            candidate_identity_keys.add(key)

    raw_weight = sum(max(1, int(p.get("session_count") or 1)) for p in positive)
    raw_weight += len(negative)
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
    now = datetime.now(timezone.utc)

    try:
        entries = current_soul_entries(root, file_name="TENSIONS.md", subject=subject).get("entries") or {}
    except Exception:
        entries = {}
        limitations.append("tension_entries_unavailable")

    for key, entry in entries.items():
        status = _tension_status_from_entry(str(key), entry or {})
        entry = entry or {}
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        resolved_at = metadata.get("resolved_at") or (entry.get("decided_at") if status in _TERMINAL_TENSION_STATUSES else "")
        obs = _tension_observation_fields(
            beads=beads,
            bead_ids=[],
            now=now,
            created_at=metadata.get("first_seen_at") or entry.get("created_at"),
            latest_at=entry.get("decided_at") or entry.get("created_at"),
            resolved_at=resolved_at,
            source_refs=_source_refs_from_evidence(list(entry.get("evidence") or [])),
        )
        tensions.append(
            {
                "id": f"soul:{key}",
                "source": "soul_file",
                "status": status,
                "kind": "soul_tension",
                "title": str(key),
                "statement": str(entry.get("content") or ""),
                "recurrence_count": 1,
                "sessions": [],
                "assembly_depth": 0.0,
                "related_goals": [],
                "related_worldlines": [],
                "related_identity_entries": [],
                "evidence_refs": [
                    {"type": "soul_revision", "id": str(entry.get("revision_id") or "")},
                    *[dict(e) for e in (entry.get("evidence") or []) if isinstance(e, dict)],
                ],
                **obs,
            }
        )

    for row in _read_dreamer_candidates(root):
        if str(row.get("hypothesis_type") or "") != "tension_candidate":
            continue
        bead_ids = [str(b) for b in (row.get("supporting_bead_ids") or []) if str(b)]
        sessions = _sessions_for_beads(beads, bead_ids)
        depth = _assembly_depth_value(row.get("assembly_depth"))
        obs = _tension_observation_fields(
            beads=beads,
            bead_ids=bead_ids,
            now=now,
            created_at=row.get("created_at"),
            source_refs=_source_refs_from_rows(list(row.get("source_refs") or [])),
        )
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
                "assembly_depth": depth,
                "related_goals": [
                    str(b)
                    for b in (row.get("conflict_bead_a"), row.get("conflict_bead_b"))
                    if str(b or "")
                ],
                "related_worldlines": [],
                "related_identity_entries": [],
                "evidence_refs": [{"type": "bead", "id": bid} for bid in bead_ids],
                **obs,
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
        obs = _tension_observation_fields(beads=beads, bead_ids=bead_ids, now=now)
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
                    "assembly_depth": 0.0,
                    "related_goals": [],
                    "related_worldlines": [str(backbone.get("id") or "")],
                    "related_identity_entries": [],
                    "evidence_refs": [{"type": "bead", "id": bid} for bid in bead_ids],
                    **obs,
                }
            )

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
        reports = compute_assembly_depth(root, target_kind="goal", limit=total_goals).get("reports") or []
        depth_by_goal = {str(rep.get("target_id")): float(rep.get("score") or 0.0) for rep in reports}
    except Exception:
        depth_by_goal = {}

    try:
        for det in detect_goal_conflicts(root, depth_by_goal=depth_by_goal):
            bead_ids = [
                str(b)
                for b in (det.get("conflict_bead_a"), det.get("conflict_bead_b"))
                if str(b or "")
            ]
            sessions = _sessions_for_beads(beads, bead_ids)
            obs = _tension_observation_fields(beads=beads, bead_ids=bead_ids, now=now)
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
                    "assembly_depth": _assembly_depth_value(det.get("assembly_depth")),
                    "related_goals": bead_ids,
                    "related_worldlines": [],
                    "related_identity_entries": [],
                    "evidence_refs": [{"type": "bead", "id": bid} for bid in bead_ids],
                    **obs,
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
    new_tension_rate, resolution_rate, churn, rate_limitations = _compute_tension_rates(
        tension_rows,
        now,
    )
    limitations.extend(rate_limitations)

    return {
        "status": _status_from_limitations(limitations),
        "active_load": active_load,
        "persistence_qualified_count": persistence_count,
        "new_tension_rate": new_tension_rate,
        "resolution_rate": resolution_rate,
        "churn": churn,
        "tensions": tension_rows,
        "limitations": sorted(set(limitations)),
    }


def build_soul_summary(root: str | Path, *, subject: str = "self") -> dict[str, Any]:
    """Build the read-only SOUL continuity summary for ``subject``."""
    index = _read_index(root)
    subject_key = str(subject or "self")
    persistent_tensions = _build_tensions(root, subject_key, index)
    return {
        "ok": True,
        "schema": SOUL_SUMMARY_SCHEMA,
        "subject": subject_key,
        "generated_at": _now(),
        "measurements_are_evidence": False,
        "light_cone_breadth": _build_light_cone(
            root,
            subject_key,
            index,
            tension_summary=persistent_tensions,
        ),
        "observed_endorsed_divergence": _build_divergence(root, subject_key, index),
        "persistent_tensions": persistent_tensions,
    }


__all__ = ["SOUL_SUMMARY_SCHEMA", "build_soul_summary"]

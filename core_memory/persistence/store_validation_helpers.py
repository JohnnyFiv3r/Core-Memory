from __future__ import annotations

from typing import Any


def normalize_links(links) -> list[dict]:
    """Normalize links to canonical list[{type, bead_id}] format."""
    if links is None:
        return []
    out: list[dict] = []
    if isinstance(links, list):
        for row in links:
            if not isinstance(row, dict):
                continue
            ltype = str(row.get("type") or "").strip()
            bid = str(row.get("bead_id") or row.get("id") or "").strip()
            if ltype and bid:
                out.append({"type": ltype, "bead_id": bid})
        return out
    if isinstance(links, dict):
        for k, v in links.items():
            if isinstance(v, list):
                for bid in v:
                    b = str(bid or "").strip()
                    if b:
                        out.append({"type": str(k), "bead_id": b})
            else:
                b = str(v or "").strip()
                if b:
                    out.append({"type": str(k), "bead_id": b})
    return out


def has_evidence(bead: dict) -> bool:
    return bool((bead.get("evidence_refs") or []) or (bead.get("tool_output_ids") or []) or (bead.get("tool_output_id") or "").strip())


def required_field_issues_for_store(bead: dict) -> list[str]:
    issues: list[str] = []
    t = str(bead.get("type") or "").strip()
    title = str(bead.get("title") or "").strip()
    summary = bead.get("summary") or []
    session_id = str(bead.get("session_id") or "").strip()
    source_turn_ids = bead.get("source_turn_ids") or []
    status = str(bead.get("status") or "").strip()
    created_at = str(bead.get("created_at") or "").strip()
    because = bead.get("because") or []
    detail = (bead.get("detail") or "").strip()

    if not t:
        issues.append("missing:type")
    if not title:
        issues.append("missing:title")
    if not isinstance(summary, list) or len(summary) == 0:
        issues.append("missing:summary")
    if not session_id:
        issues.append("missing:session_id")
    if not isinstance(source_turn_ids, list) or len(source_turn_ids) == 0:
        issues.append("missing:source_turn_ids")
    if not status:
        issues.append("missing:status")
    if not created_at:
        issues.append("missing:created_at")

    if isinstance(summary, list):
        if len(summary) > 3:
            issues.append("bounds:summary>3")
        for s in summary:
            if len(str(s)) > 220:
                issues.append("bounds:summary_item>220")
                break

    has_ev = has_evidence(bead)

    if t == "context":
        if not (bead.get("entities") or []):
            issues.append("context:missing_entities")
    elif t == "evidence":
        if not (bead.get("supports_bead_ids") or []):
            issues.append("evidence:missing_supports_bead_ids")
        if len((bead.get("detail") or "").strip()) < 40:
            issues.append("evidence:detail_too_short")
    elif t == "data_insight":
        if not (bead.get("entities") or []):
            issues.append("data_insight:missing_entities")
        if not (bead.get("detail") or "").strip():
            issues.append("data_insight:missing_detail")
    elif t == "lesson":
        if not (bead.get("because") or []):
            issues.append("lesson:missing_because")
        if not (bead.get("entities") or []):
            issues.append("lesson:missing_entities")
        if not (bead.get("supporting_facts") or []) and not (bead.get("evidence_refs") or []):
            issues.append("lesson:need_supporting_facts_or_evidence_refs")
    elif t == "decision":
        if not (bead.get("because") or []):
            issues.append("decision:missing_because")
    elif t == "design_principle":
        if not (bead.get("because") or []):
            issues.append("design_principle:missing_because")
    elif t == "goal":
        if not str(bead.get("goal_id") or "").strip():
            issues.append("goal:missing_goal_id")
        if not str(bead.get("success_criteria") or "").strip():
            issues.append("goal:missing_success_criteria")
    elif t == "outcome":
        result = str(bead.get("result") or "").strip().lower()
        if result not in {"resolved", "failed", "partial", "confirmed", "abandoned"}:
            issues.append("outcome:invalid_result")
        if not str(bead.get("linked_bead_id") or "").strip():
            issues.append("outcome:missing_linked_bead_id")
    elif t == "precedent":
        if not str(bead.get("condition") or "").strip():
            issues.append("precedent:missing_condition")
        if not str(bead.get("action") or "").strip():
            issues.append("precedent:missing_action")
    elif t == "hypothesis":
        hs = str(bead.get("hypothesis_status") or "").strip().lower()
        if hs not in {"pending", "validated", "falsified"}:
            issues.append("hypothesis:invalid_hypothesis_status")
        tb = str(bead.get("tested_by") or "").strip().lower()
        if tb and tb not in {"tool", "reasoning", "observation"}:
            issues.append("hypothesis:invalid_tested_by")
    elif t == "reflection":
        rt = str(bead.get("reflection_type") or "").strip().lower()
        if rt not in {"misjudgment", "overfitted_pattern", "meta_analysis", "pattern_recognition"}:
            issues.append("reflection:invalid_reflection_type")
    elif t == "tool_call":
        if not str(bead.get("tool") or bead.get("capability") or "").strip():
            issues.append("tool_call:missing_tool_or_capability")
        rs = str(bead.get("tool_result_status") or "").strip().lower()
        if rs and rs not in {"success", "failure"}:
            issues.append("tool_call:invalid_tool_result_status")
    elif t == "blocked":
        if not str(bead.get("blocked_by_description") or "").strip():
            issues.append("blocked:missing_blocked_by_description")
    elif t == "incident":
        if not str(bead.get("incident_id") or "").strip():
            issues.append("incident:missing_incident_id")
        sev = str(bead.get("severity") or "").strip().lower()
        if sev not in {"low", "medium", "high", "critical"}:
            issues.append("incident:invalid_severity")

    # Revision modifier cross-field validation
    has_revises = bool(str(bead.get("revises_bead_id") or "").strip())
    has_revision_type = bool(str(bead.get("revision_type") or "").strip())
    if has_revises and not has_revision_type:
        issues.append("revision:missing_revision_type")
    if has_revision_type and not has_revises:
        issues.append("revision:missing_revises_bead_id")

    return sorted(set(issues))


def validate_bead_fields_for_store(store: Any, bead: dict) -> None:
    """Required-fields validation with warn-first rollout."""
    context_tags = bead.get("context_tags")
    if context_tags is not None:
        if not isinstance(context_tags, list):
            raise ValueError("context_tags must be a list of strings")
        for tag in context_tags:
            if not isinstance(tag, str):
                raise ValueError("context_tags entries must be strings")

    issues = required_field_issues_for_store(bead)
    if issues and bool(store.strict_required_fields):
        raise ValueError("required field validation failed: " + ", ".join(issues))
    if issues:
        bead["validation_warnings"] = issues


__all__ = [
    "normalize_links",
    "has_evidence",
    "required_field_issues_for_store",
    "validate_bead_fields_for_store",
]

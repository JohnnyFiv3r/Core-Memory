from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core_memory.retrieval.contracts import (
    ClaimSlotItem,
    RecallPlanning,
    RecallResult,
    RecallStep,
    ResolvedGoalItem,
    recall_result_from_memory_execute,
    validate_recall_effort,
)
from core_memory.persistence.store_claim_ops import read_all_claim_rows, resolve_current_state
from core_memory.retrieval.tools.memory import execute as memory_execute

_CAUSAL_HINTS = {
    "why",
    "how",
    "cause",
    "caused",
    "because",
    "decision",
    "decide",
    "decided",
    "rationale",
    "reason",
    "led",
    "lead",
    "blocked",
    "unblocked",
    "changed",
    "supersede",
    "superseded",
    "resolved",
    "outcome",
    "last week",
    "timeline",
    "history",
}

_EFFORT_DEFAULTS: dict[str, dict[str, Any]] = {
    "low": {
        "k": 8,
        "grounding_mode": "search_only",
        "hydration": {},
        "planning_reason": "low effort uses direct lookup for low-latency recall",
    },
    "medium": {
        "k": 10,
        "grounding_mode": "prefer_grounded",
        "hydration": {"turn_sources": True, "max_beads": 8, "adjacent_before": 1, "adjacent_after": 1},
        "planning_reason": "medium effort uses default grounded recall with modest source hydration",
    },
    "high": {
        "k": 20,
        "grounding_mode": "prefer_grounded",
        "hydration": {"turn_sources": True, "max_beads": 16, "adjacent_before": 2, "adjacent_after": 2},
        "planning_reason": "high effort uses broader grounded recall for multi-hop, temporal, or audit queries",
    },
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _query_text(request: dict[str, Any]) -> str:
    return _text(request.get("raw_query") or request.get("query_text") or request.get("query"))


def _looks_causal_or_temporal(query: str) -> bool:
    q = f" {query.lower()} "
    return any(hint in q for hint in _CAUSAL_HINTS)


def _read_index(root: str) -> dict[str, Any]:
    try:
        payload = json.loads((Path(root) / ".beads" / "index.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _latest_update_by_bead(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for bead_id, bead in (index.get("beads") or {}).items():
        if not isinstance(bead, dict):
            continue
        updates = [u for u in (bead.get("claim_updates") or []) if isinstance(u, dict)]
        if not updates:
            continue
        updates.sort(
            key=lambda u: (
                int(u.get("chain_seq") or 0) if str(u.get("chain_seq") or "").isdigit() else 0,
                str(u.get("id") or ""),
            )
        )
        out[str(bead_id)] = updates[-1]
    return out


def _add_evidence_grounding(result: RecallResult, index: dict[str, Any]) -> None:
    latest = _latest_update_by_bead(index)
    for item in result.evidence:
        if item.grounding_hash:
            continue
        update = latest.get(str(item.bead_id)) or {}
        grounding_hash = _text(update.get("grounding_hash"))
        if grounding_hash:
            item.grounding_hash = grounding_hash


def _tokens(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", str(value or "").lower()) if t}


def _resolved_goals_for_result(result: RecallResult, index: dict[str, Any], query: str) -> list[ResolvedGoalItem]:
    beads = index.get("beads") or {}
    evidence_ids = {str(e.bead_id) for e in result.evidence if str(e.bead_id)}
    associated_ids: set[str] = set(evidence_ids)
    for assoc in index.get("associations") or []:
        if not isinstance(assoc, dict):
            continue
        src = _text(assoc.get("source_bead") or assoc.get("source_bead_id"))
        tgt = _text(assoc.get("target_bead") or assoc.get("target_bead_id"))
        if src in evidence_ids and tgt:
            associated_ids.add(tgt)
        if tgt in evidence_ids and src:
            associated_ids.add(src)
    q_tokens = _tokens(query)
    evidence_tokens: set[str] = set()
    for bid in evidence_ids:
        row = beads.get(bid) if isinstance(beads, dict) else None
        if isinstance(row, dict):
            evidence_tokens |= _tokens(" ".join([str(row.get("title") or ""), " ".join(str(x) for x in (row.get("summary") or []))]))

    items: list[ResolvedGoalItem] = []
    for bid, row in beads.items() if isinstance(beads, dict) else []:
        if not isinstance(row, dict):
            continue
        if _text(row.get("type")).lower() != "goal":
            continue
        state = _text(row.get("promotion_state") or row.get("goal_status") or row.get("status")).lower()
        if state != "resolved":
            continue
        resolved_by = _text(row.get("resolved_by_bead_id"))
        text_tokens = _tokens(" ".join([str(row.get("title") or ""), " ".join(str(x) for x in (row.get("summary") or []))]))
        related = (
            str(bid) in associated_ids
            or resolved_by in evidence_ids
            or bool(text_tokens & q_tokens)
            or bool(text_tokens & evidence_tokens)
        )
        if not related:
            continue
        items.append(
            ResolvedGoalItem(
                bead_id=str(bid),
                title=_text(row.get("title")),
                resolved_by_bead_id=resolved_by,
                resolved_at=_text(row.get("resolved_at")),
                reason=_text(row.get("promotion_reason")),
                metadata={"status": state},
            )
        )
    items.sort(key=lambda g: (g.resolved_at, g.bead_id), reverse=True)
    return items


def _latest_slot_update(updates: list[dict[str, Any]], subject: str, slot: str) -> dict[str, Any]:
    matching = [u for u in updates if _text(u.get("subject")) == subject and _text(u.get("slot")) == slot]
    if not matching:
        return {}
    matching.sort(
        key=lambda u: (
            int(u.get("chain_seq") or 0) if str(u.get("chain_seq") or "").isdigit() else 0,
            _text(u.get("id")),
        )
    )
    return matching[-1]


def _claim_slots_for_result(result: RecallResult, root: str, index: dict[str, Any]) -> dict[str, ClaimSlotItem]:
    evidence_ids = {str(e.bead_id) for e in result.evidence if str(e.bead_id)}
    beads = index.get("beads") or {}
    pairs: set[tuple[str, str]] = set()
    for bid in evidence_ids:
        row = beads.get(bid) if isinstance(beads, dict) else None
        if not isinstance(row, dict):
            continue
        for claim in row.get("claims") or []:
            if isinstance(claim, dict) and _text(claim.get("subject")) and _text(claim.get("slot")):
                pairs.add((_text(claim.get("subject")), _text(claim.get("slot"))))
        for update in row.get("claim_updates") or []:
            if isinstance(update, dict) and _text(update.get("subject")) and _text(update.get("slot")):
                pairs.add((_text(update.get("subject")), _text(update.get("slot"))))
    if not pairs:
        return {}
    _, all_updates = read_all_claim_rows(root)
    slots: dict[str, ClaimSlotItem] = {}
    for subject, slot in sorted(pairs):
        state = resolve_current_state(root, subject, slot)
        current = state.get("current_claim") if isinstance(state.get("current_claim"), dict) else {}
        latest_update = _latest_slot_update(all_updates, subject, slot)
        key = f"{subject}:{slot}"
        slots[key] = ClaimSlotItem(
            key=key,
            subject=subject,
            slot=slot,
            current_value=current.get("value") if isinstance(current, dict) else None,
            status=_text(state.get("status")),
            current_claim_id=_text(current.get("id") if isinstance(current, dict) else ""),
            chain_seq=int(latest_update.get("chain_seq")) if str(latest_update.get("chain_seq") or "").isdigit() else None,
            grounding_hash=_text(latest_update.get("grounding_hash")) or None,
        )
    return slots


def _enrich_recall_state(result: RecallResult, *, root: str, query: str) -> None:
    index = _read_index(root)
    if not index:
        return
    _add_evidence_grounding(result, index)
    result.resolved_goals = _resolved_goals_for_result(result, index, query)
    result.claim_slots = _claim_slots_for_result(result, root, index)


def _expected_shape(query: str) -> dict[str, Any]:
    """Small deterministic query-shape hint for diagnostics.

    This is intentionally not the future dynamic-effort planner. It only gives
    callers visibility into the static assumptions that guided this recall call.
    """
    q = query.lower()
    bead_types: list[str] = []
    relations: list[str] = []
    if any(term in q for term in ["decision", "decide", "decided", "rationale"]):
        bead_types.extend(["decision", "rationale"])
        relations.extend(["superseded_by", "resolves", "supports"])
    if any(term in q for term in ["why", "cause", "caused", "because", "led to"]):
        relations.extend(["caused_by", "led_to", "supports"])
    if any(term in q for term in ["goal", "finished", "done", "outcome", "resolved"]):
        bead_types.extend(["goal", "outcome"])
        relations.extend(["resolves", "led_to"])
    temporal_terms = ["last week", "yesterday", "today", "when", "timeline", "history"]
    time_range_hint = "relative" if any(term in q for term in temporal_terms) else ""
    return {
        "bead_types": sorted(set(bead_types)),
        "relations": sorted(set(relations)),
        "time_range_hint": time_range_hint,
    }


def _normalize_request(
    query_or_request: str | dict[str, Any],
    *,
    effort: str,
    intent: str | None,
    k: int | None,
    speaker: str | None,
    request_overrides: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    defaults = _EFFORT_DEFAULTS[effort]
    if isinstance(query_or_request, str):
        query = query_or_request.strip()
        if not query:
            raise ValueError("recall query must be a non-empty string")
        request: dict[str, Any] = {"raw_query": query}
    elif isinstance(query_or_request, dict):
        request = dict(query_or_request)
        query = _query_text(request)
        if not query:
            raise ValueError("recall request must include a non-empty query")
        request.setdefault("raw_query", query)
    else:
        raise TypeError("recall query_or_request must be a string or dict")

    default_intent = "causal" if effort != "low" and _looks_causal_or_temporal(query) else "remember"
    request.setdefault("intent", intent or default_intent)
    request.setdefault("k", int(defaults["k"] if k is None else k))
    request.setdefault("effort", effort)
    request.setdefault("grounding_mode", defaults["grounding_mode"])
    if defaults.get("hydration") and not request.get("hydration"):
        request["hydration"] = dict(defaults["hydration"])
    if speaker is not None:
        request.setdefault("speaker", speaker)
        facets = dict(request.get("facets") or {})
        metadata = dict(facets.get("metadata") or {})
        metadata.setdefault("speaker", speaker)
        facets["metadata"] = metadata
        request.setdefault("facets", facets)
    request.update(request_overrides)
    return query, request


def _filter_evidence_by_as_of(evidence: list[EvidenceItem], as_of_str: str) -> list[EvidenceItem]:
    from core_memory.temporal import normalize_as_of as _norm
    as_of_dt = _norm(as_of_str)
    if as_of_dt is None:
        return evidence
    filtered = []
    for item in evidence:
        created = str((item.metadata or {}).get("created_at") or "").strip()
        if not created:
            filtered.append(item)
            continue
        item_dt = _norm(created)
        if item_dt is None or item_dt <= as_of_dt:
            filtered.append(item)
    return filtered


def recall(
    query_or_request: str | dict[str, Any],
    *,
    effort: str = "medium",
    intent: str | None = None,
    k: int | None = None,
    speaker: str | None = None,
    as_of: str | None = None,
    root: str = ".",
    explain: bool = True,
    include_raw: bool = True,
    **request_overrides: Any,
) -> RecallResult:
    """Single-verb grounded recall orchestrator.

    Public callers choose effort (`low`, `medium`, `high`). Core Memory chooses
    internal retrieval tiers and reports what happened via `RecallResult.tier_path`
    and `RecallResult.steps`.
    """
    selected_effort = validate_recall_effort(effort)
    if selected_effort == "dynamic":
        raise ValueError('effort="dynamic" is reserved for a future query-planning mode; use low, medium, or high')

    if as_of is not None:
        from core_memory.temporal import normalize_as_of as _norm
        if _norm(as_of) is None:
            raise ValueError(f"as_of must be a valid ISO 8601 timestamp, got {as_of!r}")
        request_overrides = {**request_overrides, "as_of": as_of}

    query, request = _normalize_request(
        query_or_request,
        effort=selected_effort,
        intent=intent,
        k=k,
        speaker=speaker,
        request_overrides=request_overrides,
    )
    raw = memory_execute(request=request, root=root, explain=explain)
    result = recall_result_from_memory_execute(raw, query=query, effort=selected_effort, include_raw=include_raw)
    _enrich_recall_state(result, root=root, query=query)

    if as_of is not None:
        result.evidence = _filter_evidence_by_as_of(result.evidence, as_of)
        result.metadata["as_of"] = as_of
    result.planning = RecallPlanning(
        selected_effort=selected_effort,
        reason=str(_EFFORT_DEFAULTS[selected_effort]["planning_reason"]),
        expected_shape=_expected_shape(query),
    )
    if result.steps:
        result.steps[0].metadata = {
            **dict(result.steps[0].metadata or {}),
            "effort": selected_effort,
            "grounding_mode": request.get("grounding_mode"),
        }
    else:
        result.steps.append(
            RecallStep(
                tier="semantic",
                query=query,
                status="ok" if raw.get("ok", True) else "failed",
                result_count=len(result.evidence),
                metadata={"effort": selected_effort, "grounding_mode": request.get("grounding_mode")},
            )
        )
    return result

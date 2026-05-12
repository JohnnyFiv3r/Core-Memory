from __future__ import annotations

from typing import Any

from core_memory.retrieval.contracts import (
    RecallPlanning,
    RecallResult,
    RecallStep,
    recall_result_from_memory_execute,
    validate_recall_effort,
)
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


def recall(
    query_or_request: str | dict[str, Any],
    *,
    effort: str = "medium",
    intent: str | None = None,
    k: int | None = None,
    speaker: str | None = None,
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

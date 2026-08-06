from __future__ import annotations

import os

from core_memory.retrieval.pipeline import memory_search_request, memory_execute, memory_trace

SEARCH_RESULT_SCHEMA_VERSION = "memory_search_result.v1"
EXECUTE_RESULT_SCHEMA_VERSION = "memory_execute_result.v1"


def search(
    request: dict | None = None,
    root: str = ".",
    explain: bool = True,
    form_submission: dict | None = None,
) -> dict:
    """Canonical search surface.

    Public contract uses `request`. `form_submission` is accepted as compatibility
    alias for existing callers.
    """
    submission = dict(request or form_submission or {})
    out = memory_search_request(root=root, request=submission, explain=bool(explain))
    if isinstance(out, dict):
        out.setdefault("schema_version", SEARCH_RESULT_SCHEMA_VERSION)
        out.setdefault("contract", "memory_search")
        out.setdefault("request", submission)
        out.pop("snapped_query", None)
    return out


def trace(
    query: str = "",
    root: str = ".",
    k: int = 8,
    anchor_ids: list[str] | None = None,
    hydration: dict | None = None,
    max_depth: int | None = None,
    max_chains: int | None = None,
) -> dict:
    out = memory_trace(root=root, query=query, anchor_ids=anchor_ids, k=int(k), hydration=hydration, max_depth=max_depth, max_chains=max_chains)
    out.setdefault("schema_version", EXECUTE_RESULT_SCHEMA_VERSION)
    out.setdefault("contract", "memory_trace")
    return out


def plan(request: dict, root: str = ".") -> dict:
    """Plan over the durable junction roadmap with bounded graph fallback."""

    from core_memory.retrieval.roadmap_planner import plan_over_roadmap

    payload = dict(request or {})
    return plan_over_roadmap(
        root,
        query=str(payload.get("query") or ""),
        anchor_ids=list(payload.get("anchor_ids") or []),
        destination_anchor_ids=list(payload.get("destination_anchor_ids") or []),
        goal_bead_ids=list(payload.get("goal_bead_ids") or []),
        direction=str(payload.get("direction") or "upstream"),
        temporal_frame=str(payload.get("temporal_frame") or "auto"),
        allowed_source_ids=list(payload.get("allowed_source_ids") or []),
        denied_source_ids=list(payload.get("denied_source_ids") or []),
        max_vertices=int(payload.get("max_vertices") or 200),
    )


def execute(request: dict, root: str = ".", explain: bool = True) -> dict:
    if str(os.getenv("MEMORY_EXECUTE_ENABLED", "1")).lower() in {"0", "false", "off", "no"}:
        return {
            "ok": False,
            "error": "memory_execute_disabled",
            "schema_version": EXECUTE_RESULT_SCHEMA_VERSION,
            "contract": "memory_execute",
        }
    intent = str((request or {}).get("intent") or "")
    if intent == "causal" and str(os.getenv("MEMORY_EXECUTE_CAUSAL_ENABLED", "1")).lower() in {"0", "false", "off", "no"}:
        return {
            "ok": False,
            "error": "memory_execute_causal_disabled",
            "suggested_next": "use_memory_trace",
            "schema_version": EXECUTE_RESULT_SCHEMA_VERSION,
            "contract": "memory_execute",
        }
    out = memory_execute(root=root, request=request, explain=bool(explain))
    if isinstance(out, dict):
        out.setdefault("schema_version", EXECUTE_RESULT_SCHEMA_VERSION)
        out.setdefault("contract", "memory_execute")
    return out

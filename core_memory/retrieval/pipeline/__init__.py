from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import build_catalog
from .snap import snap_form
from .canonical import search_request as _search_request, execute_request as _execute_request, trace_request as _trace_request
from core_memory.graph.traversal import causal_traverse_chains as causal_traverse


def _normalize_search_request(request: dict | None) -> tuple[dict[str, Any], dict[str, Any]]:
    req = dict(request or {})
    facets = dict(req.get("facets") or {})
    constraints = dict(req.get("constraints") or {})

    query = str(req.get("query_text") or req.get("raw_query") or req.get("query") or "").strip()
    intent = str(req.get("intent") or "remember").strip() or "remember"
    k = int(req.get("k") or 10)

    incident_from_facets = str(((facets.get("incident_ids") or [None])[0] or "")).strip()
    incident_id = str(req.get("incident_id") or incident_from_facets or "").strip() or None
    scope = str(req.get("scope") or facets.get("scope") or "").strip() or None

    topic_keys = list(req.get("topic_keys") or facets.get("topic_keys") or [])
    bead_types = list(req.get("bead_types") or facets.get("bead_types") or [])
    relation_types = list(req.get("relation_types") or facets.get("relation_types") or [])
    must_terms = list(req.get("must_terms") or facets.get("must_terms") or [])
    avoid_terms = list(req.get("avoid_terms") or facets.get("avoid_terms") or [])
    time_range = dict(req.get("time_range") or facets.get("time_range") or {})

    require_structural = bool(req.get("require_structural", constraints.get("require_structural", False)))

    submission = {
        "query_text": query,
        "intent": intent,
        "k": k,
        "incident_id": incident_id,
        "scope": scope,
        "topic_keys": topic_keys,
        "bead_types": bead_types,
        "relation_types": relation_types,
        "must_terms": must_terms,
        "avoid_terms": avoid_terms,
        "time_range": time_range,
        "require_structural": require_structural,
    }
    normalization = {
        "query_source": (
            "query_text"
            if req.get("query_text") is not None
            else ("raw_query" if req.get("raw_query") is not None else ("query" if req.get("query") is not None else "default_empty"))
        ),
        "facets_used": bool(facets),
        "constraints_used": bool(constraints),
        "request_fields": sorted(list(req.keys())),
    }
    return submission, normalization


def _append_structural_chains(out: dict[str, Any], rp: Path, submission: dict[str, Any]) -> None:
    if not bool(submission.get("require_structural")):
        return

    anchor_ids = [str(r.get("bead_id") or "") for r in (out.get("results") or []) if str(r.get("bead_id") or "")][:5]
    trav = causal_traverse(rp, anchor_ids=anchor_ids, max_depth=2, max_chains=5) if anchor_ids else {"ok": True, "chains": []}
    chains = list(trav.get("chains") or [])
    relation_filter = {str(x).strip() for x in (submission.get("relation_types") or []) if str(x).strip()}
    if relation_filter:
        chains = [
            c
            for c in chains
            if {str(e.get("rel") or "") for e in (c.get("edges") or [])}.intersection(relation_filter)
        ]
    out["chains"] = chains[:3]
    if not out.get("chains"):
        warns = list(out.get("warnings") or [])
        if "require_structural_requested_but_no_chains" not in warns:
            warns.append("require_structural_requested_but_no_chains")
        out["warnings"] = warns


def memory_search_request(root: str, request: dict | None, explain: bool = False) -> dict:
    rp = Path(root)
    submission, normalization = _normalize_search_request(request)

    out = _search_request(
        root=rp,
        query=str(submission.get("query_text") or ""),
        k=int(submission.get("k") or 10),
        intent=str(submission.get("intent") or "remember"),
        submission=submission,
    )

    _append_structural_chains(out, rp, submission)

    out["suggested_next"] = out.get("next_action")
    if explain:
        out.setdefault("explain", {})
        out["explain"]["request_normalization"] = normalization
        out["explain"]["retrieval"] = {
            "result_count": len(out.get("results") or []),
            "chain_count": len(out.get("chains") or []),
            "warnings": list(out.get("warnings") or []),
        }
        top_score = float(((out.get("results") or [{}])[0] or {}).get("score") or 0.0)
        out["explain"]["confidence_diagnostics"] = {
            "top_score": top_score,
            "result_count": len(out.get("results") or []),
            "warning_count": len(out.get("warnings") or []),
        }
    return out


def memory_search_typed(root: str, submission: dict, explain: bool = False) -> dict:
    """Compatibility search shim for legacy typed form submissions.

    Canonical callers should use memory_search_request(request=...).
    """
    rp = Path(root)
    catalog = build_catalog(rp)
    snapped = snap_form(submission, catalog)
    s = snapped.get("snapped") or {}

    out = memory_search_request(root=root, request=s, explain=bool(explain))

    out["snapped_query"] = s
    if explain:
        out.setdefault("explain", {})
        out["explain"]["snapped_query"] = s
        out["explain"]["snap_decisions"] = snapped.get("decisions") or []
    return out


def memory_execute(root: str, request: dict, explain: bool = True) -> dict:
    return _execute_request(root=root, request=request, explain=bool(explain))


def memory_trace(root: str, query: str = "", anchor_ids: list[str] | None = None, k: int = 8, hydration: dict | None = None) -> dict:
    return _trace_request(root=root, query=query, anchor_ids=anchor_ids, k=int(k), intent="causal", hydration=hydration)

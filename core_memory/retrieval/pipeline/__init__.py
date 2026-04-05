from __future__ import annotations

from pathlib import Path

from .catalog import build_catalog
from .snap import snap_form
from .canonical import search_request as _search_request, execute_request as _execute_request, trace_request as _trace_request
from core_memory.graph.traversal import causal_traverse_chains as causal_traverse


def memory_search_typed(root: str, submission: dict, explain: bool = False) -> dict:
    rp = Path(root)
    catalog = build_catalog(rp)
    snapped = snap_form(submission, catalog)
    s = snapped.get("snapped") or {}
    out = _search_request(
        root=rp,
        query=str(s.get("query_text") or ""),
        k=int(s.get("k") or 10),
        intent=str(s.get("intent") or "remember"),
        submission=s,
    )

    if bool(s.get("require_structural")):
        anchor_ids = [str(r.get("bead_id") or "") for r in (out.get("results") or []) if str(r.get("bead_id") or "")][:5]
        trav = causal_traverse(rp, anchor_ids=anchor_ids, max_depth=2, max_chains=5) if anchor_ids else {"ok": True, "chains": []}
        chains = list(trav.get("chains") or [])
        relation_filter = {str(x).strip() for x in (s.get("relation_types") or []) if str(x).strip()}
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

    out["snapped_query"] = s
    out["suggested_next"] = out.get("next_action")
    if explain:
        out.setdefault("explain", {})
        out["explain"]["snapped_query"] = s
        out["explain"]["snap_decisions"] = snapped.get("decisions") or []
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


def memory_execute(root: str, request: dict, explain: bool = True) -> dict:
    return _execute_request(root=root, request=request, explain=bool(explain))


def memory_trace(root: str, query: str = "", anchor_ids: list[str] | None = None, k: int = 8, hydration: dict | None = None) -> dict:
    return _trace_request(root=root, query=query, anchor_ids=anchor_ids, k=int(k), intent="causal", hydration=hydration)

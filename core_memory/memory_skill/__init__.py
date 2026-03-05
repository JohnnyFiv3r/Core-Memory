from __future__ import annotations

from pathlib import Path

from .catalog import build_catalog
from .form import get_search_form
from .snap import snap_form
from .search import search_typed
from .explain import build_explain
from .execute import execute_request, evaluate_confidence_next, _load_beads


def memory_get_search_form(root: str) -> dict:
    rp = Path(root)
    catalog = build_catalog(rp)
    return get_search_form(catalog)


def memory_search_typed(root: str, submission: dict, explain: bool = False) -> dict:
    rp = Path(root)
    catalog = build_catalog(rp)
    snapped = snap_form(submission, catalog)
    out = search_typed(rp, snapped.get("snapped") or {}, include_explain=explain)
    out["snapped_query"] = snapped.get("snapped") or {}

    beads = _load_beads(rp)
    intent = str((out.get("snapped_query") or {}).get("intent") or "other")
    conf, nxt, cdiag = evaluate_confidence_next(
        intent=intent,
        results=out.get("results") or [],
        chains=out.get("chains") or [],
        snapped=out.get("snapped_query") or {},
        beads=beads,
        warnings=out.get("warnings") or [],
    )
    out["confidence"] = conf
    out["suggested_next"] = nxt

    if explain:
        ex = build_explain(out.get("snapped_query") or {}, snapped.get("decisions") or {}, out.get("warnings") or [], out.get("retrieval_debug") or {})
        ex["confidence_diagnostics"] = cdiag
        out["explain"] = ex
    return out


def memory_execute(root: str, request: dict, explain: bool = True) -> dict:
    return execute_request(request=request, root=root, explain=bool(explain))

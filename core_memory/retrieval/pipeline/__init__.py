from __future__ import annotations

from pathlib import Path

from .catalog import build_catalog
from core_memory.retrieval.search_form import get_search_form
from .snap import snap_form
from .canonical import search_request as _search_request, execute_request as _execute_request, trace_request as _trace_request


def memory_get_search_form(root: str) -> dict:
    rp = Path(root)
    catalog = build_catalog(rp)
    return get_search_form(catalog)


def memory_search_typed(root: str, submission: dict, explain: bool = False) -> dict:
    rp = Path(root)
    catalog = build_catalog(rp)
    snapped = snap_form(submission, catalog)
    s = snapped.get("snapped") or {}
    out = _search_request(root=rp, query=str(s.get("query_text") or ""), k=int(s.get("k") or 10), intent=str(s.get("intent") or "remember"))
    out["snapped_query"] = s
    out["suggested_next"] = out.get("next_action")
    if explain:
        out.setdefault("explain", {})
        out["explain"]["snap_decisions"] = snapped.get("decisions") or []
    return out


def memory_execute(root: str, request: dict, explain: bool = True) -> dict:
    return _execute_request(root=root, request=request, explain=bool(explain))


def memory_trace(root: str, query: str = "", anchor_ids: list[str] | None = None, k: int = 8, hydration: dict | None = None) -> dict:
    return _trace_request(root=root, query=query, anchor_ids=anchor_ids, k=int(k), intent="causal", hydration=hydration)

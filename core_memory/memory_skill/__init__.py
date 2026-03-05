from __future__ import annotations

from pathlib import Path

from .catalog import build_catalog
from .form import get_search_form
from .snap import snap_form
from .search import search_typed
from .explain import build_explain


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
    if explain:
        out["explain"] = build_explain(out.get("snapped_query") or {}, snapped.get("decisions") or {}, out.get("warnings") or [], out.get("retrieval_debug") or {})
    return out

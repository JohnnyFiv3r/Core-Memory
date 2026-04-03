from __future__ import annotations

import os

from core_memory.retrieval.pipeline import memory_search_typed, memory_execute, memory_trace

SEARCH_RESULT_SCHEMA_VERSION = "memory_search_result.v1"
EXECUTE_RESULT_SCHEMA_VERSION = "memory_execute_result.v1"


def search(form_submission: dict, root: str = ".", explain: bool = True) -> dict:
    out = memory_search_typed(root=root, submission=form_submission, explain=bool(explain))
    if isinstance(out, dict):
        out.setdefault("schema_version", SEARCH_RESULT_SCHEMA_VERSION)
        out.setdefault("contract", "typed_search")
    return out


def trace(
    query: str = "",
    root: str = ".",
    k: int = 8,
    anchor_ids: list[str] | None = None,
    hydration: dict | None = None,
) -> dict:
    out = memory_trace(root=root, query=query, anchor_ids=anchor_ids, k=int(k), hydration=hydration)
    out.setdefault("schema_version", EXECUTE_RESULT_SCHEMA_VERSION)
    out.setdefault("contract", "memory_trace")
    return out


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

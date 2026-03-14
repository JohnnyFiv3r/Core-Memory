from __future__ import annotations

import os

from core_memory.retrieval.pipeline import memory_get_search_form, memory_search_typed, memory_execute
from core_memory.retrieval.search_form import SEARCH_FORM_SCHEMA_VERSION
from core_memory.tools.memory_reason import memory_reason

SEARCH_RESULT_SCHEMA_VERSION = "memory_search_result.v1"
EXECUTE_RESULT_SCHEMA_VERSION = "memory_execute_result.v1"


def get_search_form(root: str = "./memory") -> dict:
    """Canonical typed-search form surface.

    Schema authority is owned by core_memory.retrieval.search_form.
    """
    out = memory_get_search_form(root)
    if isinstance(out, dict):
        out.setdefault("schema_version", SEARCH_FORM_SCHEMA_VERSION)
    return out


def search(form_submission: dict, root: str = "./memory", explain: bool = True) -> dict:
    out = memory_search_typed(root=root, submission=form_submission, explain=bool(explain))
    if isinstance(out, dict):
        out.setdefault("schema_version", SEARCH_RESULT_SCHEMA_VERSION)
        out.setdefault("contract", "typed_search")
    return out


def reason(
    query: str,
    root: str = "./memory",
    k: int = 8,
    debug: bool = False,
    explain: bool = False,
    pinned_incident_ids: list[str] | None = None,
    pinned_topic_keys: list[str] | None = None,
    pinned_bead_ids: list[str] | None = None,
) -> dict:
    return memory_reason(
        query=query,
        root=root,
        k=int(k),
        debug=bool(debug),
        explain=bool(explain),
        pinned_incident_ids=pinned_incident_ids,
        pinned_topic_keys=pinned_topic_keys,
        pinned_bead_ids=pinned_bead_ids,
    )


def execute(request: dict, root: str = "./memory", explain: bool = True) -> dict:
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
            "suggested_next": "use_memory_reason",
            "schema_version": EXECUTE_RESULT_SCHEMA_VERSION,
            "contract": "memory_execute",
        }
    out = memory_execute(root=root, request=request, explain=bool(explain))
    if isinstance(out, dict):
        out.setdefault("schema_version", EXECUTE_RESULT_SCHEMA_VERSION)
        out.setdefault("contract", "memory_execute")
    return out

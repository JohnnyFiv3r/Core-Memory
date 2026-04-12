from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

from core_memory.claim.resolver import resolve_all_current_state
from core_memory.retrieval.tools import memory as memory_tools


@contextmanager
def _env_override(extra: dict[str, str]):
    saved = {k: os.environ.get(k) for k in extra.keys()}
    try:
        for k, v in extra.items():
            os.environ[k] = str(v)
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _is_semantic_unavailable(out: dict[str, Any]) -> bool:
    err = dict(out.get("error") or {}) if isinstance(out, dict) else {}
    return str(err.get("code") or "").strip() == "semantic_backend_unavailable"


def _execute_with_fallback(*, root: str, request: dict[str, Any], explain: bool = True) -> dict[str, Any]:
    out = memory_tools.execute(request=request, root=root, explain=bool(explain))
    if not isinstance(out, dict) or not _is_semantic_unavailable(out):
        return out if isinstance(out, dict) else {"ok": False, "error": "invalid_response"}
    with _env_override({"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}):
        retry = memory_tools.execute(request=request, root=root, explain=bool(explain))
    if isinstance(retry, dict):
        warns = list(retry.get("warnings") or [])
        if "mcp_semantic_fallback_degraded_allowed" not in warns:
            warns.append("mcp_semantic_fallback_degraded_allowed")
        retry["warnings"] = warns
        return retry
    return out


def _trace_with_fallback(*, root: str, query: str, anchor_ids: list[str], k: int, hydration: dict[str, Any]) -> dict[str, Any]:
    out = memory_tools.trace(query=query, root=root, k=int(k), anchor_ids=anchor_ids, hydration=hydration)
    if not isinstance(out, dict) or not _is_semantic_unavailable(out):
        return out if isinstance(out, dict) else {"ok": False, "error": "invalid_response"}
    with _env_override({"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}):
        retry = memory_tools.trace(query=query, root=root, k=int(k), anchor_ids=anchor_ids, hydration=hydration)
    if isinstance(retry, dict):
        warns = list(retry.get("warnings") or [])
        if "mcp_semantic_fallback_degraded_allowed" not in warns:
            warns.append("mcp_semantic_fallback_degraded_allowed")
        retry["warnings"] = warns
        return retry
    return out


MCP_TYPED_READ_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "query_current_state": {
        "description": "Resolve current claim-state for a subject/slot and include canonical retrieval evidence.",
        "input": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "slot": {"type": "string"},
                "slot_key": {"type": "string", "description": "Optional direct key like 'user:timezone'."},
                "as_of": {"type": "string", "description": "Optional ISO timestamp for as-of resolution."},
                "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                "query": {"type": "string", "description": "Optional explicit retrieval query."},
                "include_history": {"type": "boolean", "default": False},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "query_temporal_window": {
        "description": "Run canonical retrieval with an explicit temporal window constraint.",
        "input": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "window_start": {"type": "string"},
                "window_end": {"type": "string"},
                "intent": {"type": "string", "default": "remember"},
                "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "query_causal_chain": {
        "description": "Run canonical causal trace and return anchors/chains for a why/how question.",
        "input": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "anchor_ids": {"type": "array", "items": {"type": "string"}},
                "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                "hydration": {"type": "object"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "query_contradictions": {
        "description": "Return claim-level conflicts plus contradiction/supersession retrieval evidence.",
        "input": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "slot": {"type": "string"},
                "slot_key": {"type": "string"},
                "as_of": {"type": "string"},
                "query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}


def _slot_key(*, subject: str | None, slot: str | None, slot_key: str | None) -> str:
    direct = str(slot_key or "").strip()
    if direct:
        return direct
    s = str(subject or "").strip() or "user"
    sl = str(slot or "").strip()
    if not sl:
        return ""
    return f"{s}:{sl}"


def _window_dict(start: str | None, end: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    s = str(start or "").strip()
    e = str(end or "").strip()
    if s:
        out["from"] = s
    if e:
        out["to"] = e
    return out


def query_current_state(
    *,
    root: str = ".",
    subject: str = "user",
    slot: str = "",
    slot_key: str = "",
    as_of: str = "",
    k: int = 8,
    query: str = "",
    include_history: bool = False,
) -> dict[str, Any]:
    key = _slot_key(subject=subject, slot=slot, slot_key=slot_key)
    if not key:
        return {"ok": False, "error": "missing_slot", "contract": "mcp.query_current_state.v1"}

    state = resolve_all_current_state(root, as_of=(str(as_of or "").strip() or None))
    row = dict((state.get("slots") or {}).get(key) or {})

    q = str(query or "").strip() or f"what is the current {key.replace(':', ' ')}"
    req = {
        "raw_query": q,
        "intent": "remember",
        "k": max(1, int(k)),
        "as_of": str(as_of or "").strip() or None,
        "grounding_mode": "search_only",
        "constraints": {"require_structural": False},
        "facets": {"must_terms": [str(slot or key.split(":", 1)[-1])]},
    }
    out = _execute_with_fallback(root=root, request=req, explain=True)

    payload = {
        "ok": bool(out.get("ok", True)),
        "contract": "mcp.query_current_state.v1",
        "query": {
            "subject": str(subject or "user"),
            "slot": str(slot or key.split(":", 1)[-1]),
            "slot_key": key,
            "as_of": str(as_of or "").strip() or None,
            "k": max(1, int(k)),
        },
        "current_state": {
            "status": str(row.get("status") or "not_found"),
            "current_claim": row.get("current_claim"),
            "conflicts": list(row.get("conflicts") or []),
            "history": list(row.get("history") or []) if bool(include_history) else [],
            "timeline": list(row.get("timeline") or []) if bool(include_history) else [],
        },
        "retrieval": out,
    }
    return payload


def query_temporal_window(
    *,
    root: str = ".",
    query: str,
    window_start: str = "",
    window_end: str = "",
    intent: str = "remember",
    k: int = 10,
) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "error": "missing_query", "contract": "mcp.query_temporal_window.v1"}

    window = _window_dict(window_start, window_end)
    req = {
        "raw_query": q,
        "intent": str(intent or "remember").strip() or "remember",
        "k": max(1, int(k)),
        "grounding_mode": "search_only",
        "as_of": str(window.get("to") or "") or None,
        "facets": {"time_range": dict(window)},
        "constraints": {"require_structural": False},
    }
    out = _execute_with_fallback(root=root, request=req, explain=True)
    return {
        "ok": bool(out.get("ok", True)),
        "contract": "mcp.query_temporal_window.v1",
        "query": {
            "query": q,
            "intent": str(intent or "remember").strip() or "remember",
            "window": window,
            "k": max(1, int(k)),
        },
        "retrieval": out,
    }


def query_causal_chain(
    *,
    root: str = ".",
    query: str,
    anchor_ids: list[str] | None = None,
    k: int = 8,
    hydration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "error": "missing_query", "contract": "mcp.query_causal_chain.v1"}

    out = _trace_with_fallback(
        query=q,
        root=root,
        k=max(1, int(k)),
        anchor_ids=list(anchor_ids or []),
        hydration=dict(hydration or {}),
    )
    return {
        "ok": bool(out.get("ok", True)),
        "contract": "mcp.query_causal_chain.v1",
        "query": {
            "query": q,
            "anchor_ids": list(anchor_ids or []),
            "k": max(1, int(k)),
        },
        "trace": out,
    }


def query_contradictions(
    *,
    root: str = ".",
    subject: str = "",
    slot: str = "",
    slot_key: str = "",
    as_of: str = "",
    query: str = "",
    k: int = 10,
) -> dict[str, Any]:
    key = _slot_key(subject=subject, slot=slot, slot_key=slot_key)
    state = resolve_all_current_state(root, as_of=(str(as_of or "").strip() or None))

    conflicts: list[dict[str, Any]] = []
    for sk, row in (state.get("slots") or {}).items():
        if key and str(sk) != key:
            continue
        if str((row or {}).get("status") or "") != "conflict":
            continue
        conflicts.append(
            {
                "slot_key": str(sk),
                "conflicts": list((row or {}).get("conflicts") or []),
                "current_claim": (row or {}).get("current_claim"),
            }
        )

    q = str(query or "").strip()
    if not q:
        if key:
            q = f"what contradictions exist for {key.replace(':', ' ')}"
        else:
            q = "what contradictions or supersessions are currently relevant"

    req = {
        "raw_query": q,
        "intent": "causal",
        "k": max(1, int(k)),
        "as_of": str(as_of or "").strip() or None,
        "grounding_mode": "prefer_grounded",
        "constraints": {"require_structural": True},
        "facets": {"relation_types": ["contradicts", "supersedes", "superseded_by"]},
    }
    out = _execute_with_fallback(root=root, request=req, explain=True)

    return {
        "ok": bool(out.get("ok", True)),
        "contract": "mcp.query_contradictions.v1",
        "query": {
            "slot_key": key or None,
            "as_of": str(as_of or "").strip() or None,
            "k": max(1, int(k)),
        },
        "claim_conflicts": conflicts,
        "retrieval": out,
    }

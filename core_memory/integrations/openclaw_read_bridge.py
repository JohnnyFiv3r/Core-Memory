"""OpenClaw read bridge: stdin/stdout JSON dispatch for memory read operations.

Mirrors the write bridge pattern (openclaw_agent_end_bridge.py) but for read-path
operations: search, reason, continuity, execute, search-form.

Usage (stdin → stdout):
    echo '{"action": "search", "query": "why PostgreSQL?", "root": "./memory"}' \
        | python -m core_memory.integrations.openclaw_read_bridge

Supported actions:
    search      — typed search (query → form_submission shorthand or full form)
    reason      — causal reasoning
    continuity  — rolling-window continuity injection
    execute     — unified auto-detect intent routing
    search-form — get the typed search form schema
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

from core_memory.persistence.store import DEFAULT_ROOT
from core_memory.retrieval.tools import memory as memory_tools
from core_memory.write_pipeline.continuity_injection import load_continuity_injection


def _resolve_root(payload: dict[str, Any]) -> str:
    return str(payload.get("root") or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT).strip()


def _handle_search(payload: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(payload)
    form = payload.get("form_submission")
    if not form:
        query = str(payload.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "missing_query_or_form_submission"}
        form = {"query_text": query, "k": int(payload.get("k", 8))}
    explain = bool(payload.get("explain", True))
    return memory_tools.search(form_submission=form, root=root, explain=explain)


def _handle_reason(payload: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(payload)
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "missing_query"}
    return memory_tools.reason(
        query=query,
        root=root,
        k=int(payload.get("k", 8)),
        debug=bool(payload.get("debug", False)),
        explain=bool(payload.get("explain", False)),
        pinned_incident_ids=payload.get("pinned_incident_ids"),
        pinned_topic_keys=payload.get("pinned_topic_keys"),
        pinned_bead_ids=payload.get("pinned_bead_ids"),
    )


def _handle_continuity(payload: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(payload)
    max_items = int(payload.get("max_items", 80))
    result = load_continuity_injection(root, max_items=max_items)
    fmt = str(payload.get("format", "json")).strip().lower()
    if fmt == "text":
        records = result.get("records") or []
        lines = []
        for r in records:
            typ = r.get("type", "")
            title = r.get("title", "")
            summary = " ".join(r.get("summary") or []) if isinstance(r.get("summary"), list) else str(r.get("summary", ""))
            lines.append(f"[{typ}] {title}: {summary}")
        return {"ok": True, "format": "text", "text": "\n".join(lines), "count": len(records)}
    return {"ok": True, "format": "json", **result}


def _handle_execute(payload: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(payload)
    request = payload.get("request")
    if not request:
        raw_query = str(payload.get("query") or "").strip()
        if not raw_query:
            return {"ok": False, "error": "missing_request_or_query"}
        request = {"raw_query": raw_query, "k": int(payload.get("k", 8))}
    explain = bool(payload.get("explain", True))
    return memory_tools.execute(request=request, root=root, explain=explain)


def _handle_search_form(payload: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(payload)
    return memory_tools.get_search_form(root=root)


_DISPATCH: dict[str, Any] = {
    "search": _handle_search,
    "reason": _handle_reason,
    "continuity": _handle_continuity,
    "execute": _handle_execute,
    "search-form": _handle_search_form,
    "search_form": _handle_search_form,
}


def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Route a single JSON request to the appropriate handler."""
    action = str(payload.get("action") or "").strip().lower()
    handler = _DISPATCH.get(action)
    if not handler:
        return {
            "ok": False,
            "error": f"unknown_action:{action}",
            "supported": sorted(_DISPATCH.keys()),
        }
    return handler(payload)


def main() -> None:
    """CLI bridge. Reads JSON from stdin, dispatches, writes JSON to stdout."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "ignore").strip()
        if not raw:
            print(json.dumps({"ok": False, "error": "missing_input"}))
            return
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            print(json.dumps({"ok": False, "error": "invalid_input_type"}))
            return
        result = dispatch(payload)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid_json:{exc}"}))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"bridge_exception:{exc}",
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()

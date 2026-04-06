from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def handle_memory_command(
    *,
    args: Any,
    memory: Any,
    mem_parser: Any,
    simple_recall_fallback: Callable[[Any, str, int], dict],
    memory_search_tool: Callable[..., dict],
    memory_trace_tool: Callable[..., dict],
    memory_execute: Callable[..., dict],
) -> bool:
    """Handle canonical memory search/trace/execute CLI subcommands."""
    if getattr(args, "command", None) != "memory":
        return False

    if args.memory_cmd == "search":
        payload = {
            "intent": str(getattr(args, "intent", "remember") or "remember"),
            "query_text": str(getattr(args, "query", "") or ""),
            "k": int(getattr(args, "k", 8) or 8),
        }
        out = memory_search_tool(request=payload, root=str(memory.root), explain=bool(args.explain))
        if not (out.get("results") or []):
            fallback = simple_recall_fallback(memory, str(payload.get("query_text") or ""), int(payload.get("k") or 8))
            if fallback.get("results"):
                out["results"] = fallback.get("results") or []
                out["fallback_applied"] = True
                warnings = list(out.get("warnings") or [])
                if "no_strong_anchor_match_free_text_mode" in warnings:
                    warnings = [w for w in warnings if w != "no_strong_anchor_match_free_text_mode"]
                warnings.append("cli_simple_fallback_applied")
                out["warnings"] = warnings
                out["confidence"] = "medium"
                out["suggested_next"] = "answer"
        print(json.dumps(out, indent=2))
        return True

    if args.memory_cmd == "trace":
        out = memory_trace_tool(
            query=str(getattr(args, "query", "") or ""),
            root=str(memory.root),
            k=int(getattr(args, "k", 8) or 8),
            anchor_ids=list(getattr(args, "anchor_ids", []) or []),
        )
        print(json.dumps(out, indent=2))
        return True

    if args.memory_cmd == "execute":
        req = str(args.request or "")
        if req.strip().startswith("{"):
            payload = json.loads(req)
        else:
            payload = json.loads(Path(req).read_text(encoding="utf-8"))
        print(json.dumps(memory_execute(request=payload, root=str(memory.root), explain=bool(args.explain)), indent=2))
        return True

    mem_parser.print_help()
    return True

"""PydanticAI read-path: continuity injection + memory tools.

Provides factory functions that return system-prompt generators and tool
functions suitable for wiring into a PydanticAI Agent.  Core Memory does
NOT own the agent — the caller wires these in.

Usage:
    from pydantic_ai import Agent
    from core_memory.integrations.pydanticai.memory_tools import (
        continuity_prompt,
        memory_search_tool,
        memory_reason_tool,
    )

    agent = Agent("openai:gpt-4o", tools=[
        memory_search_tool(root="./memory"),
        memory_reason_tool(root="./memory"),
    ])

    @agent.system_prompt
    def inject_memory():
        return continuity_prompt(root="./memory")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Optional

from core_memory.integrations.api import _resolve_root
from core_memory.write_pipeline.continuity_injection import load_continuity_injection
from core_memory.retrieval.tools.memory import (
    execute as memory_execute,
    get_search_form as memory_get_search_form,
    reason as memory_reason,
    search as memory_search,
)

logger = logging.getLogger(__name__)

CONTINUITY_HEADER = "## Memory Context (auto-injected)\n"
CONTINUITY_EMPTY = ""


# ── Continuity injection ──────────────────────────────────────────────


def continuity_prompt(
    root: Optional[str] = None,
    max_items: int = 80,
) -> str:
    """Load the rolling-window continuity context as a system-prompt string.

    Returns an empty string when no continuity records exist, so it is
    always safe to concatenate into a system prompt.
    """
    root_final = _resolve_root(root)
    try:
        ctx = load_continuity_injection(root_final, max_items=max_items)
    except Exception:
        logger.debug("continuity injection load failed; returning empty", exc_info=True)
        return CONTINUITY_EMPTY

    records = ctx.get("records") or []
    if not records:
        return CONTINUITY_EMPTY

    lines = [CONTINUITY_HEADER]
    for rec in records:
        title = rec.get("title") or rec.get("type", "memory")
        summary = rec.get("summary")
        if isinstance(summary, list):
            summary = " ".join(str(s) for s in summary)
        summary = summary or rec.get("detail") or ""
        lines.append(f"- **{title}**: {summary}")

    authority = ctx.get("authority", "unknown")
    lines.append(f"\n_Source: {authority}, {len(records)} record(s)_")
    return "\n".join(lines)


# ── Memory tools ──────────────────────────────────────────────────────


def memory_search_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a plain tool function for PydanticAI that searches memory.

    The agent provides a natural-language query; the tool runs it through
    Core Memory's typed-search pipeline and returns results as JSON.
    """
    root_final = _resolve_root(root)

    def search_memory(query: str, scope: str = "", type_filter: str = "") -> str:
        """Search long-term memory for relevant past context.

        Args:
            query: Natural-language search query.
            scope: Optional scope filter (personal, project, global).
            type_filter: Optional bead type filter (decision, lesson, goal, etc.).
        """
        form = memory_get_search_form(root_final)
        fields = form.get("fields") or {}

        submission: dict[str, Any] = {"query": query}
        if scope and "scope" in fields:
            submission["scope"] = scope
        if type_filter and "type" in fields:
            submission["type"] = type_filter

        try:
            result = memory_search(submission, root=root_final, explain=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

        hits = result.get("results") or result.get("hits") or []
        if not hits:
            return json.dumps({"results": [], "message": "No matching memories found."})

        compact = []
        for h in hits[:10]:
            entry: dict[str, Any] = {
                "title": h.get("title", ""),
                "type": h.get("type", ""),
                "summary": h.get("summary", ""),
            }
            if h.get("score") is not None:
                entry["score"] = round(float(h["score"]), 3)
            compact.append(entry)

        return json.dumps({"results": compact}, default=str)

    search_memory.__name__ = "search_memory"
    return search_memory


def memory_reason_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a plain tool function for PydanticAI that performs causal reasoning.

    The agent asks a reasoning question; the tool traverses the memory
    graph and returns an explanation grounded in stored beads.
    """
    root_final = _resolve_root(root)

    def reason_about_memory(query: str) -> str:
        """Reason about a question using the causal memory graph.

        Use this for questions like "why did we decide X?", "what led to Y?",
        or "what patterns have we seen around Z?".

        Args:
            query: A reasoning question about past decisions, patterns, or causes.
        """
        try:
            result = memory_reason(query, root=root_final, k=8, explain=True)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(result, default=str)

    reason_about_memory.__name__ = "reason_about_memory"
    return reason_about_memory


def memory_execute_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a plain tool function for PydanticAI that runs a memory execute request.

    This is the unified retrieval/reasoning facade — it auto-detects intent
    and routes to the appropriate pipeline.
    """
    root_final = _resolve_root(root)

    def execute_memory_request(query: str, intent: str = "search") -> str:
        """Execute a structured memory request (search, causal, or hybrid).

        Args:
            query: Natural-language query.
            intent: Request intent — 'search', 'causal', or 'hybrid'.
        """
        request = {"query": query, "intent": intent}
        try:
            result = memory_execute(request, root=root_final, explain=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(result, default=str)

    execute_memory_request.__name__ = "execute_memory_request"
    return execute_memory_request

"""PydanticAI read-path: continuity injection + memory tools.

Provides factory functions that return system-prompt generators and tool
functions suitable for wiring into a PydanticAI Agent.  Core Memory does
NOT own the agent — the caller wires these in.

Usage:
    from pydantic_ai import Agent
    from core_memory.integrations.pydanticai.memory_tools import (
        continuity_prompt,
        memory_search_tool,
        memory_trace_tool,
    )

    agent = Agent("openai:gpt-4o", tools=[
        memory_search_tool(root="./memory"),
        memory_trace_tool(root="./memory"),
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

from core_memory.integrations.api import (
    _resolve_root,
    get_turn,
    get_turn_tools,
    get_adjacent_turns,
    hydrate_bead_sources,
)
from core_memory.runtime.engine import process_session_start
from core_memory.write_pipeline.continuity_injection import load_continuity_injection
from core_memory.retrieval.tools.memory import (
    execute as memory_execute,
    search as memory_search,
    trace as memory_trace,
)

logger = logging.getLogger(__name__)

CONTINUITY_HEADER = "## Memory Context (auto-injected)\n"
CONTINUITY_EMPTY = ""


# ── Continuity injection ──────────────────────────────────────────────


def ensure_session_start_boundary(
    root: Optional[str] = None,
    *,
    session_id: str,
    source: str = "pydanticai",
    max_items: int = 80,
) -> dict[str, Any]:
    """Explicit adapter-owned session-start boundary helper."""
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing_session_id"}
    root_final = _resolve_root(root)
    return process_session_start(
        root=root_final,
        session_id=sid,
        source=source,
        max_items=max(1, int(max_items)),
    )


def ensure_session_start(
    root: Optional[str] = None,
    *,
    session_id: str,
    source: str = "pydanticai",
    max_items: int = 80,
) -> dict[str, Any]:
    """Backward/adapter-facing alias for explicit session-start boundary."""
    return ensure_session_start_boundary(
        root=root,
        session_id=session_id,
        source=source,
        max_items=max_items,
    )


def continuity_prompt(
    root: Optional[str] = None,
    max_items: int = 80,
    session_id: Optional[str] = None,
    ensure_session_start: bool = True,
) -> str:
    """Load the rolling-window continuity context as a system-prompt string.

    Returns an empty string when no continuity records exist, so it is
    always safe to concatenate into a system prompt.
    """
    root_final = _resolve_root(root)
    if ensure_session_start and session_id:
        try:
            ensure_session_start_result = ensure_session_start_boundary(
                root=root_final,
                session_id=str(session_id),
                source="pydanticai_continuity_prompt",
                max_items=max_items,
            )
            if not ensure_session_start_result.get("ok"):
                logger.debug("ensure_session_start returned non-ok: %s", ensure_session_start_result)
        except Exception:
            logger.debug("ensure_session_start failed", exc_info=True)

    try:
        ctx = load_continuity_injection(
            root_final,
            max_items=max_items,
            session_id=str(session_id or "") or None,
            ensure_session_start=False,
        )
    except Exception:
        logger.debug("continuity injection load failed; returning empty", exc_info=True)
        ctx = {}

    # SOUL self-model is injected at session start (§4.3): the agent reasons FROM
    # its self-model. Read it directly (decoupled from the discarded
    # process_session_start return) and prepend to the continuity context.
    soul_text = ""
    try:
        from core_memory.soul.injection import soul_injection_text
        soul_text = soul_injection_text(root_final, subject="self")
    except Exception:
        logger.debug("soul injection load failed", exc_info=True)

    records = ctx.get("records") or []
    continuity_block = ""
    if records:
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
        lines.append("_Transcript hydration is available on demand via get_turn/hydrate tools._")
        continuity_block = "\n".join(lines)

    blocks = [b for b in (soul_text, continuity_block) if b]
    if not blocks:
        return CONTINUITY_EMPTY
    return "\n\n".join(blocks)


# ── Memory tools ──────────────────────────────────────────────────────


def memory_search_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a plain tool function for PydanticAI that searches memory.

    The agent provides a natural-language query; the tool runs canonical
    search and returns compact JSON results.
    """
    root_final = _resolve_root(root)

    def search_memory(query: str, scope: str = "", type_filter: str = "", k: int = 8) -> str:
        """Search long-term memory for relevant past context.

        Args:
            query: Natural-language search query.
            scope: Optional scope filter (personal, project, global).
            type_filter: Optional bead type filter (decision, lesson, goal, etc.).
        """
        submission: dict[str, Any] = {"query_text": query, "k": int(k)}
        if scope:
            submission["scope"] = scope
        if type_filter:
            submission["bead_types"] = [type_filter]

        try:
            result = memory_search(request=submission, root=root_final, explain=False)
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


def memory_trace_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a plain tool function for PydanticAI that performs causal trace.

    The agent asks a causal question; the tool returns canonical trace output
    (anchors/chains/grounding).
    """
    root_final = _resolve_root(root)

    def trace_memory(query: str, k: int = 8, max_depth: Optional[int] = None) -> str:
        """Trace a causal question using canonical memory traversal.

        Args:
            query: A causal question about prior decisions or outcomes.
            k: Anchor count.
            max_depth: Optional positive causal traversal hop cap. Leave unset
                to use the configured default (for example
                CORE_MEMORY_TRACE_MAX_DEPTH). Pass 0 to use the configured
                default explicitly.
        """
        try:
            depth = int(max_depth or 0)
            result = memory_trace(query=query, root=root_final, k=int(k), max_depth=depth if depth > 0 else None)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(result, default=str)

    trace_memory.__name__ = "trace_memory"
    return trace_memory


def memory_execute_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a plain tool function for PydanticAI that runs a memory execute request.

    This is the unified retrieval/reasoning facade — it auto-detects intent
    and routes to the appropriate pipeline.
    """
    root_final = _resolve_root(root)

    def execute_memory_request(query: str, intent: str = "") -> str:
        """Execute a structured memory request.

        Args:
            query: Natural-language query.
            intent: Optional request intent (for example 'remember' or 'causal').
                Leave blank to let canonical retrieval classify intent from query text.
        """
        request: dict[str, Any] = {"query": query}
        intent_n = str(intent or "").strip()
        if intent_n:
            request["intent"] = intent_n
        try:
            result = memory_execute(request, root=root_final, explain=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(result, default=str)

    execute_memory_request.__name__ = "execute_memory_request"
    return execute_memory_request


def get_turn_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a tool function to hydrate one archived turn by turn_id."""
    root_final = _resolve_root(root)

    def get_turn_record(turn_id: str, session_id: str = "") -> str:
        result = get_turn(turn_id=turn_id, root=root_final, session_id=session_id or None)
        if result is None:
            return json.dumps({"found": False, "turn_id": turn_id})
        return json.dumps({"found": True, "turn": result}, default=str)

    get_turn_record.__name__ = "get_turn_record"
    return get_turn_record


def get_turn_tools_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a tool function to fetch tool/mesh traces for one turn."""
    root_final = _resolve_root(root)

    def get_turn_trace(turn_id: str, session_id: str = "") -> str:
        result = get_turn_tools(turn_id=turn_id, root=root_final, session_id=session_id or None)
        if result is None:
            return json.dumps({"found": False, "turn_id": turn_id})
        return json.dumps({"found": True, **result}, default=str)

    get_turn_trace.__name__ = "get_turn_trace"
    return get_turn_trace


def get_adjacent_turns_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a tool function to fetch bounded neighboring turns around a pivot turn."""
    root_final = _resolve_root(root)

    def get_turn_neighbors(turn_id: str, session_id: str = "", before: int = 1, after: int = 1) -> str:
        result = get_adjacent_turns(
            turn_id=turn_id,
            root=root_final,
            session_id=session_id or None,
            before=before,
            after=after,
        )
        if result is None:
            return json.dumps({"found": False, "turn_id": turn_id})
        return json.dumps({"found": True, **result}, default=str)

    get_turn_neighbors.__name__ = "get_turn_neighbors"
    return get_turn_neighbors


def hydrate_bead_sources_tool(root: Optional[str] = None) -> Callable[..., str]:
    """Return a convenience hydration tool that resolves bead sources to turns."""
    root_final = _resolve_root(root)

    def hydrate_sources(
        bead_ids_json: str = "[]",
        turn_ids_json: str = "[]",
        include_tools: bool = False,
        before: int = 0,
        after: int = 0,
    ) -> str:
        try:
            bead_ids = json.loads(bead_ids_json) if bead_ids_json else []
        except Exception:
            bead_ids = []
        try:
            turn_ids = json.loads(turn_ids_json) if turn_ids_json else []
        except Exception:
            turn_ids = []

        result = hydrate_bead_sources(
            root=root_final,
            bead_ids=bead_ids if isinstance(bead_ids, list) else [],
            turn_ids=turn_ids if isinstance(turn_ids, list) else [],
            include_tools=include_tools,
            before=before,
            after=after,
        )
        return json.dumps(result, default=str)

    hydrate_sources.__name__ = "hydrate_sources"
    return hydrate_sources


def memory_approval_tools(root: Optional[str] = None) -> list[Callable[..., str]]:
    """Return the human-in-the-loop approval tools for a PydanticAI agent:
    request review, approve, reject, and list the pending queue."""
    root_final = _resolve_root(root)

    def request_memory_approval(bead_id: str, requested_by: str = "", note: str = "") -> str:
        """Flag a bead as awaiting human review (approval_status=pending)."""
        from core_memory import request_approval
        try:
            return json.dumps(request_approval(root=root_final, bead_id=bead_id, requested_by=requested_by, note=note), default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def approve_memory(bead_id: str, approver: str = "", note: str = "") -> str:
        """Approve a bead under review: grants confidence class A, records the approver."""
        from core_memory import approve_bead
        try:
            return json.dumps(approve_bead(root=root_final, bead_id=bead_id, approver=approver, note=note), default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def reject_memory(bead_id: str, approver: str = "", reason: str = "") -> str:
        """Reject a bead under review: excluded from retrieval, retained for audit."""
        from core_memory import reject_bead
        try:
            return json.dumps(reject_bead(root=root_final, bead_id=bead_id, approver=approver, reason=reason), default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def list_pending_approvals(limit: int = 100) -> str:
        """List beads awaiting human review (approval_status=pending)."""
        from core_memory import list_pending_approvals as _list_pending
        try:
            return json.dumps(_list_pending(root=root_final, limit=limit), default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    return [request_memory_approval, approve_memory, reject_memory, list_pending_approvals]

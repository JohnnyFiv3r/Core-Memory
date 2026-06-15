"""MCP streamable-HTTP protocol server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core_memory.integrations.mcp.agent_guide import PROMPT_NAME, load_agent_guide
from core_memory.integrations.mcp.constants import MCP_HEALTH_PATH, MCP_SPEC_VERSION
from core_memory.integrations.mcp.registry import TOOLS, call_tool


def _tool_description(name: str) -> str:
    tool = TOOLS[name]
    return tool.description


def _csv_env(name: str) -> list[str]:
    return [part.strip() for part in str(os.getenv(name) or "").split(",") if part.strip()]


def _transport_security_settings() -> Any:
    """Build MCP transport security settings for local and hosted deployments."""

    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except Exception as exc:  # pragma: no cover - same optional extra boundary as FastMCP
        raise RuntimeError("MCP HTTP server requires `core-memory[mcp]`.") from exc

    allowed_hosts = [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "core-memory-demo.onrender.com",
        "demo.usecorememory.com",
        "corememorydemo.vercel.app",
        *_csv_env("CORE_MEMORY_MCP_ALLOWED_HOSTS"),
    ]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        "https://core-memory-demo.onrender.com",
        "https://demo.usecorememory.com",
        "https://corememorydemo.vercel.app",
        *_csv_env("CORE_MEMORY_MCP_ALLOWED_ORIGINS"),
    ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(allowed_hosts)),
        allowed_origins=list(dict.fromkeys(allowed_origins)),
    )


def build_mcp_app(*, root: str | None = None, lock_root: bool = False, **kwargs: Any) -> Any:
    """Build the MCP sub-application mounted under `/mcp`.

    The app exposes `/mcp/healthz` plus the SDK-backed streamable-HTTP MCP
    endpoint at `/mcp/` when mounted by the HTTP server.

    When ``lock_root=True`` (set automatically in hosted mode) every tool
    ignores any caller-supplied ``root`` argument and always uses
    ``default_root``, preventing MCP clients from reading/writing arbitrary
    filesystem paths.
    """

    try:
        from fastapi import FastAPI
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - exercised in envs without extras
        raise RuntimeError("MCP HTTP server requires `core-memory[mcp]`.") from exc

    default_root = root or os.getenv("CORE_MEMORY_ROOT") or str(Path("~/.core-memory/store").expanduser())

    def _root(caller_root: str | None) -> str:
        """Return the effective root, honouring lock_root policy."""
        if lock_root:
            return default_root
        return caller_root or kwargs.get("root") or default_root
    mcp = FastMCP(
        "Core Memory",
        instructions=load_agent_guide(),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=kwargs.get("transport_security") or _transport_security_settings(),
    )
    mcp_http_app = mcp.streamable_http_app()
    app = FastAPI(
        title="Core Memory MCP Protocol Server",
        version=MCP_SPEC_VERSION,
        lifespan=lambda _app: mcp.session_manager.run(),
    )

    @app.get(MCP_HEALTH_PATH)
    async def mcp_healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "surface": "mcp",
            "mcp_version": MCP_SPEC_VERSION,
            "root": default_root,
            "tools": sorted(TOOLS),
            "prompt": PROMPT_NAME,
        }

    @mcp.tool(name="capture", description=_tool_description("capture"), structured_output=True)
    def capture_tool(
        turns: list[dict[str, Any]] | None = None,
        user: str | None = None,
        assistant: str | None = None,
        as_user: str | None = None,
        as_assistant: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "turns": turns,
            "user": user,
            "assistant": assistant,
            "as_user": as_user,
            "as_assistant": as_assistant,
            "session_id": session_id,
            "turn_id": turn_id,
            "root": _root(root),
        }
        return call_tool("capture", {k: v for k, v in payload.items() if v is not None})

    @mcp.tool(name="recall", description=_tool_description("recall"), structured_output=True)
    def recall_tool(
        query: str,
        effort: str = "medium",
        speaker: str | list[str] | None = None,
        hints: dict[str, Any] | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "recall",
            {
                "query": query,
                "effort": effort,
                "speaker": speaker,
                "hints": hints or {},
                "root": _root(root),
            },
        )

    @mcp.tool(name="capture_session", description=_tool_description("capture_session"), structured_output=True)
    def capture_session_tool(
        turns: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        path: str | None = None,
        from_format: str | None = None,
        session_id: str | None = None,
        session_prefix: str | None = None,
        transcript_id: str | None = None,
        flush_policy: str | None = None,
        mode: str | None = None,
        window_size: int | None = None,
        max_turns: int | None = None,
        metadata: dict[str, Any] | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        _cs_payload = {
            "turns": turns,
            "messages": messages,
            "path": path,
            "from": from_format,
            "session_id": session_id,
            "session_prefix": session_prefix,
            "transcript_id": transcript_id,
            "flush_policy": flush_policy,
            "mode": mode,
            "window_size": window_size,
            "max_turns": max_turns,
            "metadata": metadata,
            "root": _root(root),
        }
        # Strip None values so capture_session_handler's setdefault() can apply
        # its own defaults (session_prefix="session_sync", flush_policy="end_only").
        return call_tool("capture_session", {k: v for k, v in _cs_payload.items() if v is not None})

    @mcp.tool(
        name="sync_transcript_snapshot",
        description=_tool_description("sync_transcript_snapshot"),
        structured_output=True,
    )
    def sync_transcript_snapshot_tool(
        turns: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        recent_turns: list[dict[str, Any]] | None = None,
        checkpoint_summary: str | None = None,
        durable_facts: list[Any] | None = None,
        decisions: list[Any] | None = None,
        preferences: list[Any] | None = None,
        open_threads: list[Any] | None = None,
        session_id: str | None = None,
        session_prefix: str | None = None,
        transcript_id: str | None = None,
        conversation_label: str | None = None,
        source_client: str | None = None,
        source_system: str | None = None,
        snapshot_mode: str | None = None,
        snapshot_reason: str | None = None,
        previous_snapshot_hash: str | None = None,
        user_opted_in: bool | None = None,
        metadata: dict[str, Any] | None = None,
        flush_policy: str | None = None,
        mode: str | None = None,
        window_size: int | None = None,
        max_turns: int | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "turns": turns,
            "messages": messages,
            "recent_turns": recent_turns,
            "checkpoint_summary": checkpoint_summary,
            "durable_facts": durable_facts,
            "decisions": decisions,
            "preferences": preferences,
            "open_threads": open_threads,
            "session_id": session_id,
            "session_prefix": session_prefix,
            "transcript_id": transcript_id,
            "conversation_label": conversation_label,
            "source_client": source_client,
            "source_system": source_system,
            "snapshot_mode": snapshot_mode,
            "snapshot_reason": snapshot_reason,
            "previous_snapshot_hash": previous_snapshot_hash,
            "user_opted_in": user_opted_in,
            "metadata": metadata,
            "flush_policy": flush_policy,
            "mode": mode,
            "window_size": window_size,
            "max_turns": max_turns,
            "root": _root(root),
        }
        return call_tool("sync_transcript_snapshot", {k: v for k, v in payload.items() if v is not None})

    @mcp.tool(name="ingest", description=_tool_description("ingest"), structured_output=True)
    def ingest_tool(
        path: str | None = None,
        turns: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        from_format: str | None = None,
        session_prefix: str | None = None,
        session_id: str | None = None,
        transcript_id: str | None = None,
        self_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        flush_policy: str | None = None,
        mode: str | None = None,
        window_size: int | None = None,
        max_turns: int | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "ingest",
            {
                "path": path,
                "turns": turns,
                "messages": messages,
                "from": from_format,
                "session_prefix": session_prefix,
                "session_id": session_id,
                "transcript_id": transcript_id,
                "self_id": self_id,
                "metadata": metadata,
                "flush_policy": flush_policy,
                "mode": mode,
                "window_size": window_size,
                "max_turns": max_turns,
                "root": _root(root),
            },
        )

    @mcp.tool(name="status", description=_tool_description("status"), structured_output=True)
    def status_tool(root: str | None = None) -> dict[str, Any]:
        return call_tool("status", {"root": _root(root)})

    @mcp.tool(name="query_current_state", description=_tool_description("query_current_state"), structured_output=True)
    def query_current_state_tool(
        subject: str = "user",
        slot: str = "",
        slot_key: str = "",
        as_of: str = "",
        k: int = 8,
        query: str = "",
        include_history: bool = False,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "query_current_state",
            {
                "root": _root(root),
                "subject": subject,
                "slot": slot,
                "slot_key": slot_key,
                "as_of": as_of,
                "k": k,
                "query": query,
                "include_history": include_history,
            },
        )

    @mcp.tool(
        name="query_temporal_window",
        description=_tool_description("query_temporal_window"),
        structured_output=True,
    )
    def query_temporal_window_tool(
        query: str,
        window_start: str = "",
        window_end: str = "",
        intent: str = "remember",
        k: int = 10,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "query_temporal_window",
            {
                "root": _root(root),
                "query": query,
                "window_start": window_start,
                "window_end": window_end,
                "intent": intent,
                "k": k,
            },
        )

    @mcp.tool(name="query_causal_chain", description=_tool_description("query_causal_chain"), structured_output=True)
    def query_causal_chain_tool(
        query: str,
        anchor_ids: list[str] | None = None,
        k: int = 8,
        hydration: dict[str, Any] | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "query_causal_chain",
            {
                "root": _root(root),
                "query": query,
                "anchor_ids": anchor_ids or [],
                "k": k,
                "hydration": hydration or {},
            },
        )

    @mcp.tool(
        name="query_contradictions",
        description=_tool_description("query_contradictions"),
        structured_output=True,
    )
    def query_contradictions_tool(
        subject: str = "",
        slot: str = "",
        slot_key: str = "",
        as_of: str = "",
        query: str = "",
        k: int = 10,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "query_contradictions",
            {
                "root": _root(root),
                "subject": subject,
                "slot": slot,
                "slot_key": slot_key,
                "as_of": as_of,
                "query": query,
                "k": k,
            },
        )

    @mcp.tool(
        name="write_turn_finalized",
        description=_tool_description("write_turn_finalized"),
        structured_output=True,
    )
    def write_turn_finalized_tool(
        session_id: str,
        turn_id: str,
        turns: list[dict[str, Any]],
        transaction_id: str = "",
        trace_id: str = "",
        metadata: dict[str, Any] | None = None,
        tools_trace: list[dict[str, Any]] | None = None,
        mesh_trace: list[dict[str, Any]] | None = None,
        window_turn_ids: list[str] | None = None,
        window_bead_ids: list[str] | None = None,
        origin: str = "USER_TURN",
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "write_turn_finalized",
            {
                "root": _root(root),
                "session_id": session_id,
                "turn_id": turn_id,
                "turns": turns,
                "transaction_id": transaction_id,
                "trace_id": trace_id,
                "metadata": metadata or {},
                "tools_trace": tools_trace or [],
                "mesh_trace": mesh_trace or [],
                "window_turn_ids": window_turn_ids or [],
                "window_bead_ids": window_bead_ids or [],
                "origin": origin,
            },
        )

    @mcp.tool(
        name="apply_reviewed_proposal",
        description=_tool_description("apply_reviewed_proposal"),
        structured_output=True,
    )
    def apply_reviewed_proposal_tool(
        candidate_id: str,
        decision: str,
        reviewer: str = "",
        notes: str = "",
        apply: bool = True,
        resolution: str = "",
        context_a: str = "",
        context_b: str = "",
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "apply_reviewed_proposal",
            {
                "root": _root(root),
                "candidate_id": candidate_id,
                "decision": decision,
                "reviewer": reviewer,
                "notes": notes,
                "apply": apply,
                "resolution": resolution,
                "context_a": context_a,
                "context_b": context_b,
            },
        )

    @mcp.tool(
        name="submit_entity_merge_proposal",
        description=_tool_description("submit_entity_merge_proposal"),
        structured_output=True,
    )
    def submit_entity_merge_proposal_tool(
        source_entity_id: str,
        target_entity_id: str,
        source_bead_id: str = "",
        target_bead_id: str = "",
        confidence: float = 0.9,
        reviewer: str = "",
        rationale: str = "",
        notes: str = "",
        run_metadata: dict[str, Any] | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "submit_entity_merge_proposal",
            {
                "root": _root(root),
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "source_bead_id": source_bead_id,
                "target_bead_id": target_bead_id,
                "confidence": confidence,
                "reviewer": reviewer,
                "rationale": rationale,
                "notes": notes,
                "run_metadata": run_metadata or {},
            },
        )

    @mcp.tool(
        name="request_memory_approval",
        description=_tool_description("request_memory_approval"),
        structured_output=True,
    )
    def request_memory_approval_tool(
        bead_id: str,
        requested_by: str = "",
        note: str = "",
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "request_memory_approval",
            {"root": _root(root), "bead_id": bead_id, "requested_by": requested_by, "note": note},
        )

    @mcp.tool(
        name="approve_memory",
        description=_tool_description("approve_memory"),
        structured_output=True,
    )
    def approve_memory_tool(
        bead_id: str,
        approver: str = "",
        note: str = "",
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "approve_memory",
            {"root": _root(root), "bead_id": bead_id, "approver": approver, "note": note},
        )

    @mcp.tool(
        name="reject_memory",
        description=_tool_description("reject_memory"),
        structured_output=True,
    )
    def reject_memory_tool(
        bead_id: str,
        approver: str = "",
        reason: str = "",
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "reject_memory",
            {"root": _root(root), "bead_id": bead_id, "approver": approver, "reason": reason},
        )

    @mcp.tool(
        name="list_pending_approvals",
        description=_tool_description("list_pending_approvals"),
        structured_output=True,
    )
    def list_pending_approvals_tool(
        limit: int = 100,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool("list_pending_approvals", {"root": _root(root), "limit": limit})

    @mcp.prompt(name=PROMPT_NAME, description="Canonical Core Memory agent guide for MCP clients.")
    def core_memory_agent_guide() -> str:
        return load_agent_guide()

    app.state.mcp_session_manager = mcp.session_manager
    app.mount("/", mcp_http_app)
    return app

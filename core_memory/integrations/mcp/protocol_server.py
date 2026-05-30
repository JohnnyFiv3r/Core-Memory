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


def build_mcp_app(*, root: str | None = None, **kwargs: Any) -> Any:
    """Build the MCP sub-application mounted under `/mcp`.

    The app exposes `/mcp/healthz` plus the SDK-backed streamable-HTTP MCP
    endpoint at `/mcp/` when mounted by the HTTP server.
    """

    try:
        from fastapi import FastAPI
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - exercised in envs without extras
        raise RuntimeError("MCP HTTP server requires `core-memory[mcp]`.") from exc

    default_root = root or os.getenv("CORE_MEMORY_ROOT") or str(Path("~/.core-memory/store").expanduser())
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
            "root": root or kwargs.get("root") or default_root,
        }
        return call_tool("capture", {k: v for k, v in payload.items() if v is not None})

    @mcp.tool(name="recall", description=_tool_description("recall"), structured_output=True)
    def recall_tool(
        query: str,
        effort: str = "medium",
        speaker: str | list[str] | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "recall",
            {
                "query": query,
                "effort": effort,
                "speaker": speaker,
                "root": root or kwargs.get("root") or default_root,
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
        max_turns: int | None = None,
        metadata: dict[str, Any] | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "capture_session",
            {
                "turns": turns,
                "messages": messages,
                "path": path,
                "from": from_format,
                "session_id": session_id,
                "session_prefix": session_prefix,
                "transcript_id": transcript_id,
                "flush_policy": flush_policy,
                "max_turns": max_turns,
                "metadata": metadata,
                "root": root or kwargs.get("root") or default_root,
            },
        )

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
                "max_turns": max_turns,
                "root": root or kwargs.get("root") or default_root,
            },
        )

    @mcp.tool(name="status", description=_tool_description("status"), structured_output=True)
    def status_tool(root: str | None = None) -> dict[str, Any]:
        return call_tool("status", {"root": root or kwargs.get("root") or default_root})

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
                "root": root or kwargs.get("root") or default_root,
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
                "root": root or kwargs.get("root") or default_root,
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
                "root": root or kwargs.get("root") or default_root,
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
                "root": root or kwargs.get("root") or default_root,
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
                "root": root or kwargs.get("root") or default_root,
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
                "root": root or kwargs.get("root") or default_root,
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
                "root": root or kwargs.get("root") or default_root,
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

    @mcp.prompt(name=PROMPT_NAME, description="Canonical Core Memory agent guide for MCP clients.")
    def core_memory_agent_guide() -> str:
        return load_agent_guide()

    app.state.mcp_session_manager = mcp.session_manager
    app.mount("/", mcp_http_app)
    return app

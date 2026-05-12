"""MCP streamable-HTTP protocol server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core_memory.integrations.mcp.agent_guide import PROMPT_NAME, load_agent_guide
from core_memory.integrations.mcp.constants import MCP_HEALTH_PATH, MCP_HTTP_PATH, MCP_SPEC_VERSION
from core_memory.integrations.mcp.registry import TOOLS, call_tool


def _tool_description(name: str) -> str:
    tool = TOOLS[name]
    return tool.description


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

    @mcp.tool(name="ingest", description=_tool_description("ingest"), structured_output=True)
    def ingest_tool(
        path: str,
        from_format: str | None = None,
        session_prefix: str | None = None,
        self_id: str | None = None,
        root: str | None = None,
    ) -> dict[str, Any]:
        return call_tool(
            "ingest",
            {
                "path": path,
                "from": from_format,
                "session_prefix": session_prefix,
                "self_id": self_id,
                "root": root or kwargs.get("root") or default_root,
            },
        )

    @mcp.tool(name="status", description=_tool_description("status"), structured_output=True)
    def status_tool(root: str | None = None) -> dict[str, Any]:
        return call_tool("status", {"root": root or kwargs.get("root") or default_root})

    @mcp.prompt(name=PROMPT_NAME, description="Canonical Core Memory agent guide for MCP clients.")
    def core_memory_agent_guide() -> str:
        return load_agent_guide()

    app.state.mcp_session_manager = mcp.session_manager
    app.mount("/", mcp_http_app)
    return app

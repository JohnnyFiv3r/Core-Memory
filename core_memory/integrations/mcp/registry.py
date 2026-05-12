"""MCP protocol tool registry.

The v1 protocol surface mirrors public Core Memory verbs:
`capture`, `recall`, `ingest`, and `status`.

Handlers are intentionally explicit wrappers rather than auto-generated from
internal function signatures; this lets the MCP contract remain stable while
Core Memory internals evolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core_memory.integrations.mcp.agent_guide import tool_description
from core_memory.integrations.mcp.tools.capture import capture_handler
from core_memory.integrations.mcp.tools.ingest import ingest_handler
from core_memory.integrations.mcp.tools.recall import recall_handler
from core_memory.integrations.mcp.tools.status import status_handler


MCPHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: MCPHandler | None = None


def _not_implemented(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "mcp_tool_not_implemented",
        "message": "MCP tool handler lands in a later implementation slice.",
        "payload_keys": sorted((payload or {}).keys()),
    }


_RECALL_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": ["string", "null"]},
        "why": {"type": ["string", "null"]},
        "evidence": {"type": "array"},
        "sources": {"type": "array"},
        "tier_path": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array"},
        "planning": {"type": "object"},
        "status": {"type": "string"},
        "schema_version": {"const": "recall_result.v1"},
        "contract": {"const": "recall_result"},
    },
}


TOOLS: dict[str, MCPToolDefinition] = {
    "capture": MCPToolDefinition(
        name="capture",
        description=tool_description("capture"),
        input_schema={
            "type": "object",
            "properties": {
                "turns": {"type": "array"},
                "user": {"type": "string"},
                "assistant": {"type": "string"},
                "as_user": {"type": "string"},
                "as_assistant": {"type": "string"},
                "session_id": {"type": "string"},
                "turn_id": {"type": "string"},
                "root": {"type": "string"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "session_id": {"type": "string"},
                "turn_id": {"type": "string"},
                "bead_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        handler=capture_handler,
    ),
    "recall": MCPToolDefinition(
        name="recall",
        description=tool_description("recall"),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "effort": {"enum": ["low", "medium", "high"]},
                "speaker": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                "root": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema=_RECALL_RESULT_SCHEMA,
        handler=recall_handler,
    ),
    "ingest": MCPToolDefinition(
        name="ingest",
        description=tool_description("ingest"),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "from": {"enum": ["auto", "json", "jsonl", "markdown", "text"]},
                "format": {"enum": ["auto", "json", "jsonl", "markdown", "text"]},
                "session_prefix": {"type": "string"},
                "session_id": {"type": "string"},
                "turn_id": {"type": "string"},
                "self_id": {"type": "string"},
                "root": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "path": {"type": "string"},
                "format": {"type": "string"},
                "session_id": {"type": "string"},
                "turn_id": {"type": "string"},
                "turns_ingested": {"type": "integer"},
                "malformed_count": {"type": "integer"},
                "bead_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        handler=ingest_handler,
    ),
    "status": MCPToolDefinition(
        name="status",
        description=tool_description("status"),
        input_schema={"type": "object", "properties": {"root": {"type": "string"}}, "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "root": {"type": "string"},
                "beads_total": {"type": "integer"},
                "sessions_total": {"type": "integer"},
                "last_capture_at": {"type": ["string", "null"]},
                "connected_adapters": {"type": "array", "items": {"type": "string"}},
                "mcp_version": {"type": "string"},
                "server_version": {"type": "string"},
            },
        },
        handler=status_handler,
    ),
}


def list_tools() -> list[MCPToolDefinition]:
    return list(TOOLS.values())


def call_tool(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = TOOLS.get(str(name or ""))
    if tool is None:
        return {
            "ok": False,
            "error": {
                "code": "tool_not_found",
                "message": f"unknown MCP tool: {name}",
                "data": {"tool": name},
            },
        }
    if tool.handler is None:
        return _not_implemented(payload or {})
    return tool.handler(payload or {})

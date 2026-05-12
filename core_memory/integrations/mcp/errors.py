"""Stable MCP protocol error contracts for Core Memory.

This module intentionally contains only lightweight data helpers in the
scaffold pass. Protocol-specific exception adaptation lands with the MCP SDK
integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CoreMemoryMCPError:
    """Normalized Core Memory operational error for MCP tool responses."""

    code: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "data": dict(self.data)}}


ERROR_CODES: dict[str, str] = {
    "cm.store_not_found": "The configured Core Memory store path is missing or unreadable.",
    "cm.invalid_turn": "The capture input failed turn schema validation.",
    "cm.parser_format_unsupported": "The ingest file format was not detected or is unsupported.",
    "cm.parser_aborted": "Transcript parsing failed before a valid ingest could be completed.",
    "cm.path_not_readable": "The ingest path is not readable from the MCP server process.",
    "cm.recall_effort_exhausted": "Recall exhausted the selected effort limit before a complete answer.",
    "cm.recall_ungrounded": "Recall could not produce a grounded answer.",
    "cm.unsupported_mcp_version": "The client negotiated an unsupported MCP spec version.",
}

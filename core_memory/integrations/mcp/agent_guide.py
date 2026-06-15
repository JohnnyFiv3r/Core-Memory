"""Canonical agent-guide loading for MCP instruction injection."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
import re

CANONICAL_AGENT_GUIDE_RELATIVE = Path("core_memory/integrations/mcp/core-memory-agent-guide.md")
DOCS_AGENT_GUIDE_RELATIVE = Path("docs/agent-guide/core-memory-agent-guide.md")
PACKAGE_GUIDE_NAME = "core-memory-agent-guide.md"
PROMPT_NAME = "core-memory.agent-guide"
_TOOL_SECTION_RE = re.compile(r"<!-- tool:(?P<name>[a-z_]+):start -->(?P<body>.*?)<!-- tool:(?P=name):end -->", re.S)


def fallback_tool_description(tool_name: str) -> str:
    descriptions = {
        "capture": "Capture completed conversation turns into Core Memory using the canonical write boundary.",
        "recall": "Recall grounded memory with effort='low', 'medium', or 'high' depending on latency and depth needs.",
        "capture_session": (
            "End-of-session safety net: replay the full conversation transcript through canonical capture semantics. "
            "Call this once before the conversation ends or compacts to ensure no durable state is lost."
        ),
        "sync_transcript_snapshot": (
            "Required safety net for opted-in long chats: replay the visible, user-authorized transcript snapshot "
            "through canonical ingest/capture semantics. Call after milestones, periodically before compaction, "
            "and pass user_opted_in=true."
        ),
        "ingest": "Ingest a local transcript file into Core Memory when the file is readable by the MCP server.",
        "status": "Report Core Memory MCP server and store health.",
    }
    return descriptions.get(tool_name, "Core Memory MCP tool.")


@lru_cache(maxsize=1)
def load_agent_guide() -> str:
    """Load the packaged canonical MCP agent guide."""

    try:
        return resources.files("core_memory.integrations.mcp").joinpath(PACKAGE_GUIDE_NAME).read_text(encoding="utf-8")
    except Exception:
        path = Path(__file__).with_name(PACKAGE_GUIDE_NAME)
        if path.exists():
            return path.read_text(encoding="utf-8")
    return "# Core Memory Agent Guide\n\nCanonical guide unavailable; use tool descriptions as fallback.\n"


@lru_cache(maxsize=1)
def tool_descriptions() -> dict[str, str]:
    guide = load_agent_guide()
    descriptions: dict[str, str] = {}
    for match in _TOOL_SECTION_RE.finditer(guide):
        name = match.group("name")
        body = match.group("body").strip()
        body = re.sub(r"^## Tool: .+?$", "", body, count=1, flags=re.M).strip()
        descriptions[name] = re.sub(r"\s+", " ", body).strip()
    for name in ("capture", "recall", "capture_session", "sync_transcript_snapshot", "ingest", "status"):
        descriptions.setdefault(name, fallback_tool_description(name))
    return descriptions


def tool_description(tool_name: str) -> str:
    return tool_descriptions().get(tool_name, fallback_tool_description(tool_name))

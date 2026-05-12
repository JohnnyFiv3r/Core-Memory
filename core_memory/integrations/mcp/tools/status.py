"""MCP `status` tool wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory._version import VERSION
from core_memory.persistence.store import DEFAULT_ROOT, MemoryStore
from core_memory.integrations.mcp.constants import MCP_SPEC_VERSION


def status_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    root = str(payload.get("root") or DEFAULT_ROOT)
    store_root = Path(root)
    store = MemoryStore(root=root)
    stats = store.stats()
    sessions_dir = store_root / ".beads" / "turns"
    sessions_total = 0
    if sessions_dir.exists():
        sessions_total = len([p for p in sessions_dir.glob("*.jsonl") if p.is_file()])
    return {
        "ok": True,
        "root": root,
        "beads_total": int(stats.get("total_beads") or 0),
        "sessions_total": sessions_total,
        "last_capture_at": None,
        "connected_adapters": ["json"],
        "mcp_version": MCP_SPEC_VERSION,
        "server_version": VERSION,
    }

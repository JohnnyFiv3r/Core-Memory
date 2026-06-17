"""MCP `status` tool wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core_memory._version import VERSION
from core_memory.integrations.mcp.constants import MCP_SPEC_VERSION
from core_memory.integrations.mcp.tools.transcript_snapshot_state import (
    MCP_TOOLS_VERSION,
    TRANSCRIPT_SNAPSHOT_SCHEMA_VERSION,
    load_transcript_snapshot_state,
)
from core_memory.persistence.store import DEFAULT_ROOT, MemoryStore
from core_memory.runtime.queue.side_effect_queue import side_effect_queue_status


def _advertised_tools_count() -> int:
    try:
        from core_memory.integrations.mcp.registry import TOOLS

        return len(TOOLS)
    except Exception:
        return 0


def _writable(root: Path) -> tuple[bool, str]:
    try:
        events_dir = root / ".beads" / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        probe = events_dir / ".status-write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


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
    writable, writable_error = _writable(store_root)
    snapshot_state = load_transcript_snapshot_state(root)
    latest_snapshot = snapshot_state.get("latest") if isinstance(snapshot_state.get("latest"), dict) else {}
    try:
        queue_status = side_effect_queue_status(root)
        queue_depth = int(queue_status.get("queue_depth") or 0)
        queue_error = str(queue_status.get("last_error") or "")
    except Exception as exc:
        queue_depth = 0
        queue_error = str(exc)
    last_error = str(snapshot_state.get("last_error") or queue_error or writable_error or "")
    return {
        "ok": True,
        "root": root,
        "store_root": root,
        "writable": writable,
        "beads_total": int(stats.get("total_beads") or 0),
        "sessions_total": sessions_total,
        "last_capture_at": latest_snapshot.get("completed_at") or None,
        "last_snapshot_id": latest_snapshot.get("snapshot_id") or None,
        "last_snapshot_source": latest_snapshot.get("source_client") or latest_snapshot.get("source_system") or None,
        "last_snapshot_conversation_id": latest_snapshot.get("conversation_id") or None,
        "queue_depth": queue_depth,
        "tools_version": MCP_TOOLS_VERSION,
        "schema_version": TRANSCRIPT_SNAPSHOT_SCHEMA_VERSION,
        "last_error": last_error or None,
        "advertised_tools_count": _advertised_tools_count(),
        "connected_adapters": ["json"],
        "mcp_version": MCP_SPEC_VERSION,
        "server_version": VERSION,
    }

"""MCP `capture_session` tool wrapper — end-of-session safety-net sync."""

from __future__ import annotations

from typing import Any

from core_memory.integrations.mcp.tools.ingest import ingest_handler


def capture_session_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replay the full conversation transcript through the canonical ingest path.

    Accepts the same shapes as `ingest` (inline turns, messages, or a file path)
    and routes them through canonical capture semantics.  Designed to be called
    once at the end of a conversation as a lossless safety net — any durable
    state that per-turn `capture` missed is recovered here.
    """
    payload = dict(payload or {})

    # Group mode is more permissive than dyadic — it accepts any speaker mix
    # without requiring both user and assistant to be present.
    payload.setdefault("mode", "group")

    # Use a session-scoped prefix so the ingest doesn't collide with live captures.
    payload.setdefault("session_prefix", "session_sync")

    # Flush at end of session so the rolling window is updated for next session.
    payload.setdefault("flush_policy", "flush")

    result = ingest_handler(payload)

    # Re-label the tool key in error responses so callers can distinguish this
    # tool from plain `ingest` in error logs.
    if not result.get("ok"):
        err = result.get("error")
        if isinstance(err, dict) and isinstance(err.get("data"), dict):
            err["data"]["tool"] = "capture_session"
    return result

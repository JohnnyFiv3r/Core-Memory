"""OpenClaw coordinator integration helpers for Core Memory event runtime.

DEPRECATED: This module is kept for backward compatibility.
Use `core_memory.integrations.openclaw_agent_end_bridge` for new code.

These helpers are designed to be called from coordinator finalize/commit points.
They keep integration explicit and non-invasive while enforcing one-pass-per-turn.
"""

from __future__ import annotations

from typing import Any

from .event_worker import SidecarPolicy
from .memory_engine import process_turn_finalized, emit_turn_finalized, process_pending_legacy_events


def coordinator_finalize_hook(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str,
    trace_id: str,
    user_query: str,
    assistant_final: str,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: list[dict] | None = None,
    mesh_trace: list[dict] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call this at coordinator finalize to emit memory event once per top-level turn."""
    return emit_turn_finalized(
        root=root,
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=user_query,
        assistant_final=assistant_final,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        metadata=metadata,
    )


def finalize_and_process_turn(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str,
    trace_id: str,
    user_query: str,
    assistant_final: str,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: list[dict] | None = None,
    mesh_trace: list[dict] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    policy: SidecarPolicy | None = None,
) -> dict[str, Any]:
    """Atomically emit + process one finalized turn via canonical trigger orchestrator."""
    return process_turn_finalized(
        root=root,
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        user_query=user_query,
        assistant_final=assistant_final,
        trace_depth=trace_depth,
        origin=origin,
        tools_trace=tools_trace,
        mesh_trace=mesh_trace,
        window_turn_ids=window_turn_ids,
        window_bead_ids=window_bead_ids,
        metadata=metadata,
        policy=policy,
    )


def process_pending_memory_events(root: str, max_events: int = 50, policy: SidecarPolicy | None = None) -> dict[str, Any]:
    """Legacy compatibility wrapper routed through memory_engine ownership."""
    return process_pending_legacy_events(root=root, max_events=max_events, policy=policy)

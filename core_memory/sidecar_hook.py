"""Coordinator finalize hook adapter for memory sidecar integration.

Non-invasive shim: coordinator code can call these helpers at finalize/commit
without importing persistence internals directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .sidecar import TurnEnvelope, emit_memory_event, get_memory_pass, mark_memory_pass


def should_emit_memory_event(trace_depth: int, origin: str) -> bool:
    """Emit only for top-level non-memory-pass turns."""
    if trace_depth != 0:
        return False
    if (origin or "").upper() == "MEMORY_PASS":
        return False
    return True


def maybe_emit_finalize_memory_event(
    root: str,
    *,
    session_id: str,
    turn_id: str,
    transaction_id: str,
    trace_id: str,
    user_query: str,
    assistant_final: Optional[str],
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: Optional[list[dict]] = None,
    mesh_trace: Optional[list[dict]] = None,
    window_turn_ids: Optional[list[str]] = None,
    window_bead_ids: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """Finalize hook: emit one memory event per top-level user turn.

    Returns a status dict suitable for coordinator logs/metrics.
    """
    if not should_emit_memory_event(trace_depth=trace_depth, origin=origin):
        return {"emitted": False, "reason": "guard_skipped"}

    root_path = Path(root)
    prior = get_memory_pass(root_path, session_id, turn_id)

    envelope = TurnEnvelope(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        origin=origin,
        user_query=user_query,
        assistant_final=assistant_final,
        tools_trace=tools_trace or [],
        mesh_trace=mesh_trace or [],
        window_turn_ids=window_turn_ids or [],
        window_bead_ids=window_bead_ids or [],
        metadata=metadata or {},
    )
    envelope.finalize_hashes()

    if prior and prior.get("status") == "done":
        if prior.get("envelope_hash") == envelope.envelope_hash:
            return {"emitted": False, "reason": "idempotent_done"}
        # same turn_id, changed final output -> mutation/amend path
        mark_memory_pass(root_path, session_id, turn_id, "pending", envelope.envelope_hash)
        event = emit_memory_event(root_path, envelope)
        return {
            "emitted": True,
            "reason": "turn_mutation",
            "event_id": event.event_id,
            "assistant_final_hash": envelope.assistant_final_hash,
        }

    mark_memory_pass(root_path, session_id, turn_id, "pending", envelope.envelope_hash)
    event = emit_memory_event(root_path, envelope)
    return {
        "emitted": True,
        "reason": "emitted",
        "event_id": event.event_id,
        "assistant_final_hash": envelope.assistant_final_hash,
    }

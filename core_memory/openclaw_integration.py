"""OpenClaw coordinator integration helpers for Core Memory sidecar.

These helpers are designed to be called from coordinator finalize/commit points.
They keep integration explicit and non-invasive while enforcing one-pass-per-turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .sidecar import get_memory_pass
from .sidecar_hook import maybe_emit_finalize_memory_event
from .sidecar_worker import process_memory_event, SidecarPolicy


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
    return maybe_emit_finalize_memory_event(
        root,
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
    """Atomically emit + process one finalized turn.

    Simpler native path for chat-triggered execution: avoids detached two-step
    finalize/process races while preserving sidecar contracts and idempotency.
    """
    emitted = coordinator_finalize_hook(
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

    if not emitted.get("emitted"):
        return {
            "ok": True,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 0,
        }

    events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
    if not events_file.exists():
        return {
            "ok": False,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 1,
            "error": "events_file_missing_after_emit",
        }

    last_row: dict[str, Any] | None = None
    with open(events_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            env = row.get("envelope") or {}
            if env.get("session_id") == session_id and env.get("turn_id") == turn_id:
                last_row = row

    if not last_row:
        return {
            "ok": False,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 1,
            "error": "event_row_not_found",
        }

    try:
        delta = process_memory_event(root, last_row, policy=policy)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "mode": "turn",
            "emitted": emitted,
            "processed": 0,
            "failed": 1,
            "error": str(exc),
        }

    return {
        "ok": True,
        "mode": "turn",
        "emitted": emitted,
        "processed": 1,
        "failed": 0,
        "delta": delta,
    }


def process_pending_memory_events(root: str, max_events: int = 50, policy: SidecarPolicy | None = None) -> dict[str, Any]:
    """Process pending TURN_FINALIZED memory events from local JSONL queue.

    This is a lightweight poller path for single-node/dev environments.
    """
    events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
    if not events_file.exists():
        return {"processed": 0, "failed": 0}

    processed = 0
    failed = 0

    with open(events_file, "r", encoding="utf-8") as f:
        for line in f:
            if processed >= max_events:
                break
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            envelope = row.get("envelope") or {}
            if not envelope:
                continue

            # Skip memory-origin events for recursion safety
            if (envelope.get("origin") or "").upper() == "MEMORY_PASS":
                continue

            session_id = envelope.get("session_id", "")
            turn_id = envelope.get("turn_id", "")
            prior = get_memory_pass(Path(root), session_id, turn_id)
            if prior and prior.get("status") == "done":
                continue

            try:
                process_memory_event(root, row, policy=policy)
                processed += 1
            except Exception:
                failed += 1

    return {"processed": processed, "failed": failed}

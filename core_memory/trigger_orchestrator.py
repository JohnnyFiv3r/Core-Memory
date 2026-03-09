from __future__ import annotations

"""Compatibility helpers for trigger orchestration.

P8A Step 2: runtime sequencing ownership has moved to `core_memory.memory_engine`.
This module is retained as a thin compatibility layer for callers still importing
`run_turn_finalize_pipeline` / `run_flush_pipeline`.
"""

from typing import Any

LEGACY_SHIM = True
SHIM_REPLACEMENT = "core_memory.memory_engine"


def run_turn_finalize_pipeline(
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
    policy=None,
) -> dict[str, Any]:
    from .memory_engine import process_turn_finalized

    out = process_turn_finalized(
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
    out.setdefault("shim", {})
    out["shim"].update({"module": "core_memory.trigger_orchestrator", "delegated_to": "core_memory.memory_engine"})
    return out


def run_flush_pipeline(
    *,
    root: str,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
    source: str = "flush_hook",
    flush_tx_id: str | None = None,
) -> dict[str, Any]:
    from .memory_engine import process_flush

    out = process_flush(
        root=root,
        session_id=session_id,
        promote=bool(promote),
        token_budget=int(token_budget),
        max_beads=int(max_beads),
        source=source,
        flush_tx_id=flush_tx_id,
    )
    out.setdefault("shim", {})
    out["shim"].update({"module": "core_memory.trigger_orchestrator", "delegated_to": "core_memory.memory_engine"})
    return out

from __future__ import annotations

from typing import Any

from .sidecar_worker import SidecarPolicy
from .trigger_orchestrator import run_turn_finalize_pipeline, run_flush_pipeline


# Canonical runtime center (V2-P3-T1): thin orchestration ownership surface.
# This module intentionally delegates to proven canonical trigger paths while
# providing one obvious "start here" runtime entry for contributors.


def process_turn_finalized(
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
    return run_turn_finalize_pipeline(
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


def process_flush(
    *,
    root: str,
    session_id: str,
    promote: bool,
    token_budget: int,
    max_beads: int,
    source: str = "flush_hook",
    flush_tx_id: str | None = None,
) -> dict[str, Any]:
    return run_flush_pipeline(
        root=root,
        session_id=session_id,
        promote=bool(promote),
        token_budget=int(token_budget),
        max_beads=int(max_beads),
        source=source,
        flush_tx_id=flush_tx_id,
    )

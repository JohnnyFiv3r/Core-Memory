from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .live_session import read_live_session_beads
from .association import build_crawler_context, apply_crawler_updates
from .sidecar import get_memory_pass, mark_memory_pass, try_claim_memory_pass
from .sidecar_hook import maybe_emit_finalize_memory_event
from .sidecar_worker import SidecarPolicy, process_memory_event
from .trigger_orchestrator import run_flush_pipeline, run_turn_finalize_pipeline


# Canonical runtime center.
# P6A Step 2: deepen ownership by making memory_engine responsible for
# input normalization/defaulting and orchestration preflight composition.


def _normalize_turn_request(
    *,
    session_id: str,
    turn_id: str,
    transaction_id: str | None,
    trace_id: str | None,
    user_query: str,
    assistant_final: str,
    trace_depth: int,
    origin: str,
    tools_trace: list[dict] | None,
    mesh_trace: list[dict] | None,
    window_turn_ids: list[str] | None,
    window_bead_ids: list[str] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    tid = str(turn_id or "").strip()
    tx = str(transaction_id or f"tx-{tid}-{uuid.uuid4().hex[:8]}")
    tr = str(trace_id or f"tr-{tid}-{uuid.uuid4().hex[:8]}")

    return {
        "session_id": sid,
        "turn_id": tid,
        "transaction_id": tx,
        "trace_id": tr,
        "user_query": str(user_query or ""),
        "assistant_final": str(assistant_final or ""),
        "trace_depth": int(trace_depth or 0),
        "origin": str(origin or "USER_TURN"),
        "tools_trace": list(tools_trace or []),
        "mesh_trace": list(mesh_trace or []),
        "window_turn_ids": [str(x) for x in (window_turn_ids or [])],
        "window_bead_ids": [str(x) for x in (window_bead_ids or [])],
        "metadata": dict(metadata or {}),
    }


def process_turn_finalized(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str | None = None,
    trace_id: str | None = None,
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
    req = _normalize_turn_request(
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

    out = run_turn_finalize_pipeline(
        root=root,
        session_id=req["session_id"],
        turn_id=req["turn_id"],
        transaction_id=req["transaction_id"],
        trace_id=req["trace_id"],
        user_query=req["user_query"],
        assistant_final=req["assistant_final"],
        trace_depth=req["trace_depth"],
        origin=req["origin"],
        tools_trace=req["tools_trace"],
        mesh_trace=req["mesh_trace"],
        window_turn_ids=req["window_turn_ids"],
        window_bead_ids=req["window_bead_ids"],
        metadata=req["metadata"],
        policy=policy,
    )
    out.setdefault("engine", {})
    out["engine"].update({"normalized": True, "entry": "process_turn_finalized"})
    return out


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
    # Engine-owned preflight: snapshot live-session authority context.
    live = read_live_session_beads(root, session_id)

    out = run_flush_pipeline(
        root=root,
        session_id=str(session_id or ""),
        promote=bool(promote),
        token_budget=int(token_budget),
        max_beads=int(max_beads),
        source=str(source or "flush_hook"),
        flush_tx_id=flush_tx_id,
    )
    out.setdefault("engine", {})
    out["engine"].update(
        {
            "entry": "process_flush",
            "live_session_authority": str(live.get("authority") or "unknown"),
            "live_session_count": int(live.get("count") or 0),
        }
    )
    return out


def read_live_session(*, root: str, session_id: str) -> dict[str, Any]:
    return read_live_session_beads(root, session_id)


def emit_turn_finalized(
    *,
    root: str,
    session_id: str,
    turn_id: str,
    transaction_id: str | None = None,
    trace_id: str | None = None,
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
    req = _normalize_turn_request(
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
    out = maybe_emit_finalize_memory_event(
        root,
        session_id=req["session_id"],
        turn_id=req["turn_id"],
        transaction_id=req["transaction_id"],
        trace_id=req["trace_id"],
        user_query=req["user_query"],
        assistant_final=req["assistant_final"],
        trace_depth=req["trace_depth"],
        origin=req["origin"],
        tools_trace=req["tools_trace"],
        mesh_trace=req["mesh_trace"],
        window_turn_ids=req["window_turn_ids"],
        window_bead_ids=req["window_bead_ids"],
        metadata=req["metadata"],
    )
    out.setdefault("engine", {})
    out["engine"].update({"normalized": True, "entry": "emit_turn_finalized"})
    return out


def crawler_turn_context(*, root: str, session_id: str, limit: int = 200, carry_in_bead_ids: list[str] | None = None) -> dict[str, Any]:
    out = build_crawler_context(root=root, session_id=session_id, limit=limit, carry_in_bead_ids=carry_in_bead_ids)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "crawler_turn_context"})
    return out


def apply_crawler_turn_updates(
    *, root: str, session_id: str, updates: dict[str, Any], visible_bead_ids: list[str] | None = None
) -> dict[str, Any]:
    out = apply_crawler_updates(root=root, session_id=session_id, updates=updates, visible_bead_ids=visible_bead_ids)
    out.setdefault("engine", {})
    out["engine"].update({"entry": "apply_crawler_turn_updates"})
    return out


def process_pending_legacy_events(root: str, max_events: int = 50, policy: SidecarPolicy | None = None) -> dict[str, Any]:
    """Legacy compatibility poller, routed through memory_engine ownership."""
    legacy_enabled = os.environ.get("CORE_MEMORY_ENABLE_LEGACY_POLLER", "0") == "1"
    if not legacy_enabled:
        return {
            "processed": 0,
            "failed": 0,
            "skipped": True,
            "reason": "legacy_poller_disabled",
            "authority_path": "legacy_sidecar_compat",
            "engine": {"entry": "process_pending_legacy_events"},
        }

    events_file = Path(root) / ".beads" / "events" / "memory-events.jsonl"
    if not events_file.exists():
        return {
            "processed": 0,
            "failed": 0,
            "authority_path": "legacy_sidecar_compat",
            "engine": {"entry": "process_pending_legacy_events"},
        }

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

            if (envelope.get("origin") or "").upper() == "MEMORY_PASS":
                continue

            session_id = envelope.get("session_id", "")
            turn_id = envelope.get("turn_id", "")
            prior = get_memory_pass(Path(root), session_id, turn_id)
            if prior and prior.get("status") == "done":
                continue

            claimed, state_after = try_claim_memory_pass(Path(root), session_id, turn_id)
            if not claimed:
                continue

            try:
                process_memory_event(root, row, policy=policy)
                processed += 1
            except Exception as exc:
                failed += 1
                mark_memory_pass(
                    Path(root),
                    session_id,
                    turn_id,
                    "failed",
                    envelope_hash=(state_after or {}).get("envelope_hash", ""),
                    reason="worker_exception",
                    error=str(exc),
                )

    return {
        "processed": processed,
        "failed": failed,
        "authority_path": "legacy_sidecar_compat",
        "engine": {"entry": "process_pending_legacy_events"},
    }

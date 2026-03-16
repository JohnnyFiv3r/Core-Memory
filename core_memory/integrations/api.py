from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional

from core_memory.runtime.ingress import maybe_emit_finalize_memory_event
from core_memory.persistence.store import DEFAULT_ROOT


@dataclass
class IntegrationContext:
    framework: str
    source: str
    store_full_text: bool = True
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    thread_id: Optional[str] = None

    def to_metadata(self) -> dict[str, Any]:
        md = asdict(self)
        return {k: v for k, v in md.items() if v is not None}


def _resolve_root(root: Optional[str]) -> str:
    return (root or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT).strip()


def emit_turn_finalized(
    *,
    root: Optional[str] = None,
    session_id: str,
    turn_id: str,
    transaction_id: str,
    user_query: str,
    assistant_final: str,
    trace_id: Optional[str] = None,
    trace_depth: int = 0,
    origin: str = "USER_TURN",
    tools_trace: Optional[list[dict]] = None,
    mesh_trace: Optional[list[dict]] = None,
    window_turn_ids: Optional[list[str]] = None,
    window_bead_ids: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    strict: bool = False,
) -> Optional[str]:
    """Stable integration port for external orchestrators.

    Emits one TURN_FINALIZED memory event for a top-level turn.
    - strict=False (default): return None when emission is skipped
    - strict=True: raise ValueError when skipped
    """
    root_final = _resolve_root(root)
    trace_id_final = (trace_id or f"tr-{turn_id}-{uuid.uuid4().hex[:8]}").strip()
    result = maybe_emit_finalize_memory_event(
        root_final,
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id_final,
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
    if not result.get("emitted"):
        if strict:
            raise ValueError(f"turn not emitted: {result.get('reason')}")
        return None
    event_id = result.get("event_id")
    if not event_id:
        if strict:
            raise ValueError("missing event_id after emit")
        return None
    return str(event_id)


def emit_turn_finalized_from_envelope(*, root: Optional[str] = None, envelope: dict[str, Any], strict: bool = False) -> Optional[str]:
    return emit_turn_finalized(
        root=root,
        session_id=str(envelope.get("session_id") or "main"),
        turn_id=str(envelope.get("turn_id") or uuid.uuid4().hex[:12]),
        transaction_id=str(envelope.get("transaction_id") or f"tx-{uuid.uuid4().hex[:12]}"),
        trace_id=str(envelope.get("trace_id") or f"tr-{uuid.uuid4().hex[:12]}"),
        user_query=str(envelope.get("user_query") or ""),
        assistant_final=str(envelope.get("assistant_final") or ""),
        trace_depth=int(envelope.get("trace_depth", 0) or 0),
        origin=str(envelope.get("origin") or "USER_TURN"),
        tools_trace=envelope.get("tools_trace") or [],
        mesh_trace=envelope.get("mesh_trace") or [],
        window_turn_ids=envelope.get("window_turn_ids") or [],
        window_bead_ids=envelope.get("window_bead_ids") or [],
        metadata=envelope.get("metadata") or {},
        strict=strict,
    )

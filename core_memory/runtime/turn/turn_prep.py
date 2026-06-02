from __future__ import annotations

import uuid
from typing import Any

from core_memory.policy.bead_typing import classify_bead_type
from core_memory.schema.turn import (
    Turn,
    assistant_content,
    normalize_turns,
    reject_legacy_turn_kwargs,
    serialize_turns,
    turn_speakers,
    turns_summary,
    user_content,
)


def normalize_turn_request(
    *,
    session_id: str,
    turn_id: str,
    transaction_id: str | None,
    trace_id: str | None,
    turns: list[Turn | dict[str, Any]] | None,
    trace_depth: int,
    origin: str,
    tools_trace: list[dict] | None,
    mesh_trace: list[dict] | None,
    window_turn_ids: list[str] | None,
    window_bead_ids: list[str] | None,
    metadata: dict[str, Any] | None,
    **legacy_kwargs: Any,
) -> dict[str, Any]:
    """Canonical turn-finalized input normalization."""
    reject_legacy_turn_kwargs(legacy_kwargs, surface="process_turn_finalized")
    sid = str(session_id or "").strip()
    tid = str(turn_id or "").strip()
    tx = str(transaction_id or f"tx-{tid}-{uuid.uuid4().hex[:8]}")
    tr = str(trace_id or f"tr-{tid}-{uuid.uuid4().hex[:8]}")
    normalized_turns = normalize_turns(turns)
    speakers = turn_speakers(normalized_turns)

    return {
        "session_id": sid,
        "turn_id": tid,
        "transaction_id": tx,
        "trace_id": tr,
        "turns": serialize_turns(normalized_turns),
        "speakers": speakers,
        # Derived compatibility fields for internal policies while the rest of the
        # pipeline moves to the canonical N-speaker shape.
        "user_query": user_content(normalized_turns),
        "assistant_final": assistant_content(normalized_turns),
        "turn_text": turns_summary(normalized_turns),
        "source_turn_ref": {"turn_id": tid, "session_id": sid, "speakers": speakers},
        "trace_depth": int(trace_depth or 0),
        "origin": str(origin or "USER_TURN"),
        "tools_trace": list(tools_trace or []),
        "mesh_trace": list(mesh_trace or []),
        "window_turn_ids": [str(x) for x in (window_turn_ids or [])],
        "window_bead_ids": [str(x) for x in (window_bead_ids or [])],
        "metadata": dict(metadata or {}),
    }


def infer_semantic_bead_type(user_query: str, assistant_final: str) -> str:
    """LLM-first bead type policy classifier with deterministic fallback."""
    return classify_bead_type(user_query=user_query, assistant_final=assistant_final)

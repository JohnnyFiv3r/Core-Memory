from __future__ import annotations

import uuid
from typing import Any

from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.dreamer_candidates import decide_dreamer_candidate, submit_entity_merge_candidate


MCP_TYPED_WRITE_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "write_turn_finalized": {
        "description": "Canonical turn-finalized write boundary.",
        "input": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "turn_id": {"type": "string"},
                "user_query": {"type": "string"},
                "assistant_final": {"type": "string"},
                "transaction_id": {"type": "string"},
                "trace_id": {"type": "string"},
                "metadata": {"type": "object"},
                "tools_trace": {"type": "array", "items": {"type": "object"}},
                "mesh_trace": {"type": "array", "items": {"type": "object"}},
                "window_turn_ids": {"type": "array", "items": {"type": "string"}},
                "window_bead_ids": {"type": "array", "items": {"type": "string"}},
                "origin": {"type": "string", "default": "USER_TURN"},
            },
            "required": ["session_id", "turn_id", "user_query", "assistant_final"],
            "additionalProperties": False,
        },
    },
    "apply_reviewed_proposal": {
        "description": "Apply an accepted/rejected Dreamer proposal through canonical adjudication path.",
        "input": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["accept", "reject"]},
                "reviewer": {"type": "string"},
                "notes": {"type": "string"},
                "apply": {"type": "boolean", "default": True},
            },
            "required": ["candidate_id", "decision"],
            "additionalProperties": False,
        },
    },
    "submit_entity_merge_proposal": {
        "description": "Submit a reviewable entity-merge proposal to Dreamer candidate queue.",
        "input": {
            "type": "object",
            "properties": {
                "source_entity_id": {"type": "string"},
                "target_entity_id": {"type": "string"},
                "source_bead_id": {"type": "string"},
                "target_bead_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.9},
                "reviewer": {"type": "string"},
                "rationale": {"type": "string"},
                "notes": {"type": "string"},
                "run_metadata": {"type": "object"},
            },
            "required": ["source_entity_id", "target_entity_id"],
            "additionalProperties": False,
        },
    },
}


def write_turn_finalized(
    *,
    root: str = ".",
    session_id: str,
    turn_id: str,
    user_query: str,
    assistant_final: str,
    transaction_id: str = "",
    trace_id: str = "",
    metadata: dict[str, Any] | None = None,
    tools_trace: list[dict[str, Any]] | None = None,
    mesh_trace: list[dict[str, Any]] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    origin: str = "USER_TURN",
) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    tid = str(turn_id or "").strip()
    uq = str(user_query or "").strip()
    af = str(assistant_final or "").strip()
    if not sid or not tid or not uq or not af:
        return {"ok": False, "error": "missing_required_fields", "contract": "mcp.write_turn_finalized.v1"}

    tx = str(transaction_id or "").strip() or f"tx-{tid}-{uuid.uuid4().hex[:8]}"
    tr = str(trace_id or "").strip() or f"tr-{tid}-{uuid.uuid4().hex[:8]}"

    out = process_turn_finalized(
        root=root,
        session_id=sid,
        turn_id=tid,
        transaction_id=tx,
        trace_id=tr,
        user_query=uq,
        assistant_final=af,
        metadata=dict(metadata or {}),
        tools_trace=list(tools_trace or []),
        mesh_trace=list(mesh_trace or []),
        window_turn_ids=list(window_turn_ids or []),
        window_bead_ids=list(window_bead_ids or []),
        origin=str(origin or "USER_TURN"),
    )
    event_id = str(((((out.get("emitted") or {}).get("payload") or {}).get("event") or {}).get("event_id") or ""))
    return {
        "ok": bool(out.get("ok", True)),
        "contract": "mcp.write_turn_finalized.v1",
        "authority_path": str(out.get("authority_path") or "canonical_in_process"),
        "event_id": event_id,
        "processed": int(out.get("processed") or 0),
        "result": out,
    }


def apply_reviewed_proposal(
    *,
    root: str = ".",
    candidate_id: str,
    decision: str,
    reviewer: str = "",
    notes: str = "",
    apply: bool = True,
) -> dict[str, Any]:
    out = decide_dreamer_candidate(
        root=root,
        candidate_id=str(candidate_id or ""),
        decision=str(decision or ""),
        reviewer=str(reviewer or ""),
        notes=str(notes or ""),
        apply=bool(apply),
    )
    if isinstance(out, dict):
        out = dict(out)
        out.setdefault("contract", "mcp.apply_reviewed_proposal.v1")
    return out


def submit_entity_merge_proposal(
    *,
    root: str = ".",
    source_entity_id: str,
    target_entity_id: str,
    source_bead_id: str = "",
    target_bead_id: str = "",
    confidence: float = 0.9,
    reviewer: str = "",
    rationale: str = "",
    notes: str = "",
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = submit_entity_merge_candidate(
        root=root,
        source_entity_id=str(source_entity_id or "").strip(),
        target_entity_id=str(target_entity_id or "").strip(),
        source_bead_id=str(source_bead_id or "").strip(),
        target_bead_id=str(target_bead_id or "").strip(),
        confidence=float(confidence or 0.0),
        reviewer=str(reviewer or ""),
        rationale=str(rationale or ""),
        notes=str(notes or ""),
        run_metadata=dict(run_metadata or {}),
    )
    if isinstance(out, dict):
        out = dict(out)
        out.setdefault("contract", "mcp.submit_entity_merge_proposal.v1")
    return out

from __future__ import annotations

import uuid
from typing import Any

from core_memory.identifiers import validate_archive_id
from core_memory.runtime.dreamer.candidates import decide_dreamer_candidate, submit_entity_merge_candidate
from core_memory.runtime.engine import process_turn_finalized
from core_memory.runtime.turn.receipt import receipt_view, rejected_turn_receipt
from core_memory.schema.agent_authored_updates import (
    AgentAuthoredUpdatesV1,
    AuthoringMode,
    agent_authored_updates_json_schema,
)
from core_memory.schema.turn import reject_legacy_turn_kwargs

MCP_TYPED_WRITE_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "write_turn_finalized": {
        "description": "Canonical turn-finalized write boundary.",
        "input": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "turn_id": {"type": "string"},
                "turns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "speaker": {"type": "string"},
                            "content": {"type": "string"},
                            "role": {"type": "string", "enum": ["user", "assistant", "other"]},
                            "ts": {"type": ["string", "null"]},
                            "metadata": {"type": "object"},
                        },
                        "required": ["speaker"],
                        "additionalProperties": False,
                    },
                },
                "transaction_id": {"type": "string"},
                "trace_id": {"type": "string"},
                "metadata": {"type": "object"},
                "crawler_updates": agent_authored_updates_json_schema(),
                "authoring_mode": {"type": "string", "enum": ["inline", "delegated"]},
                "tools_trace": {"type": "array", "items": {"type": "object"}},
                "mesh_trace": {"type": "array", "items": {"type": "object"}},
                "window_turn_ids": {"type": "array", "items": {"type": "string"}},
                "window_bead_ids": {"type": "array", "items": {"type": "string"}},
                "origin": {"type": "string", "default": "USER_TURN"},
            },
            "required": ["session_id", "turn_id", "turns"],
            "additionalProperties": False,
        },
    },
    "request_memory_approval": {
        "description": "Flag a bead as awaiting human review (approval_status=pending).",
        "input": {
            "type": "object",
            "properties": {
                "bead_id": {"type": "string"},
                "requested_by": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["bead_id"],
            "additionalProperties": False,
        },
    },
    "approve_memory": {
        "description": (
            "Approve a bead under review: grants confidence class A and records the approver. Content is never edited."
        ),
        "input": {
            "type": "object",
            "properties": {
                "bead_id": {"type": "string"},
                "approver": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["bead_id"],
            "additionalProperties": False,
        },
    },
    "reject_memory": {
        "description": (
            "Reject a bead under review: excluded from current-truth retrieval, retained in the index for audit."
        ),
        "input": {
            "type": "object",
            "properties": {
                "bead_id": {"type": "string"},
                "approver": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["bead_id"],
            "additionalProperties": False,
        },
    },
    "apply_reviewed_proposal": {
        "description": (
            "Apply an accepted/rejected Dreamer proposal through canonical adjudication path. "
            "For a contradiction_pressure_candidate, pass `resolution` (prefer_a | prefer_b | "
            "retract_both | defer | both_valid) — the choice id from the conflict review prompt. "
            "For both_valid, also pass context_a (scope label for value_a) and context_b (scope "
            "label for value_b). Both must be non-empty strings; use '' for 'default / everywhere else'."
        ),
        "input": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["accept", "reject"]},
                "reviewer": {"type": "string"},
                "notes": {"type": "string"},
                "apply": {"type": "boolean", "default": True},
                "resolution": {
                    "type": "string",
                    "enum": ["prefer_a", "prefer_b", "retract_both", "defer", "both_valid"],
                    "description": "Contradiction resolution choice (contradiction_pressure_candidate only).",
                },
                "context_a": {
                    "type": "string",
                    "description": (
                        "Scope label for value_a when resolution='both_valid'. Use empty string for "
                        "'default / everywhere else'."
                    ),
                },
                "context_b": {
                    "type": "string",
                    "description": (
                        "Scope label for value_b when resolution='both_valid'. Use empty string for "
                        "'default / everywhere else'."
                    ),
                },
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
    "maintain": {
        "description": (
            "Unified governed maintenance facade for approvals, cleanup, async ops, association review, Dreamer "
            "decisions, SOUL revisions, Myelination refresh, corrections, append-only semantic reauthoring, and "
            "pending-turn repair. Dry-run previews report required_authority, authority_ok, and validation_errors; "
            "live semantic maintenance requires a successful copied-tenant receipt."
        ),
        "input": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "scope": {"type": "object"},
                "targets": {"type": "object"},
                "proposal": {"type": "object"},
                "decision": {"type": "object"},
                "authority": {"type": "object"},
                "dry_run": {"type": "boolean", "default": True},
                "apply": {"type": "boolean", "default": False},
                "idempotency_key": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}


def write_turn_finalized(
    *,
    root: str = ".",
    session_id: str,
    turn_id: str,
    turns: list[dict[str, Any]] | None = None,
    transaction_id: str = "",
    trace_id: str = "",
    metadata: dict[str, Any] | None = None,
    crawler_updates: AgentAuthoredUpdatesV1 | None = None,
    authoring_mode: AuthoringMode | None = None,
    tools_trace: list[dict[str, Any]] | None = None,
    mesh_trace: list[dict[str, Any]] | None = None,
    window_turn_ids: list[str] | None = None,
    window_bead_ids: list[str] | None = None,
    origin: str = "USER_TURN",
    **legacy_kwargs: Any,
) -> dict[str, Any]:
    try:
        reject_legacy_turn_kwargs(legacy_kwargs, surface="write_turn_finalized")
    except TypeError:
        return dict(
            rejected_turn_receipt(
                session_id=session_id,
                turn_id=turn_id,
                error_code="legacy_turn_fields_removed",
            )
        )
    try:
        sid = validate_archive_id(session_id, field="session_id")
        tid = validate_archive_id(turn_id, field="turn_id")
    except ValueError as exc:
        return dict(rejected_turn_receipt(session_id=session_id, turn_id=turn_id, error_code=str(exc)))
    if not turns:
        return dict(rejected_turn_receipt(session_id=session_id, turn_id=turn_id, error_code="missing_required_fields"))

    tx = str(transaction_id or "").strip() or f"tx-{tid}-{uuid.uuid4().hex[:8]}"
    tr = str(trace_id or "").strip() or f"tr-{tid}-{uuid.uuid4().hex[:8]}"

    out = process_turn_finalized(
        root=root,
        session_id=sid,
        turn_id=tid,
        transaction_id=tx,
        trace_id=tr,
        turns=list(turns or []),
        metadata=dict(metadata or {}),
        crawler_updates=crawler_updates,
        authoring_mode=authoring_mode,
        tools_trace=list(tools_trace or []),
        mesh_trace=list(mesh_trace or []),
        window_turn_ids=list(window_turn_ids or []),
        window_bead_ids=list(window_bead_ids or []),
        origin=str(origin or "USER_TURN"),
    )
    return dict(receipt_view(out))


def request_memory_approval(*, root: str = ".", bead_id: str, requested_by: str = "", note: str = "") -> dict[str, Any]:
    from core_memory import request_approval

    out = dict(
        request_approval(
            root=root, bead_id=str(bead_id or ""), requested_by=str(requested_by or ""), note=str(note or "")
        )
    )
    out.setdefault("contract", "mcp.request_memory_approval.v1")
    return out


def approve_memory(*, root: str = ".", bead_id: str, approver: str = "", note: str = "") -> dict[str, Any]:
    from core_memory import approve_bead

    out = dict(approve_bead(root=root, bead_id=str(bead_id or ""), approver=str(approver or ""), note=str(note or "")))
    out.setdefault("contract", "mcp.approve_memory.v1")
    return out


def reject_memory(*, root: str = ".", bead_id: str, approver: str = "", reason: str = "") -> dict[str, Any]:
    from core_memory import reject_bead

    out = dict(
        reject_bead(root=root, bead_id=str(bead_id or ""), approver=str(approver or ""), reason=str(reason or ""))
    )
    out.setdefault("contract", "mcp.reject_memory.v1")
    return out


def apply_reviewed_proposal(
    *,
    root: str = ".",
    candidate_id: str,
    decision: str,
    reviewer: str = "",
    notes: str = "",
    apply: bool = True,
    resolution: str = "",
    context_a: str | None = None,
    context_b: str | None = None,
) -> dict[str, Any]:
    out = decide_dreamer_candidate(
        root=root,
        candidate_id=str(candidate_id or ""),
        decision=str(decision or ""),
        reviewer=str(reviewer or ""),
        notes=str(notes or ""),
        apply=bool(apply),
        resolution=str(resolution or "") or None,
        scope_a=str(context_a) if context_a is not None else None,
        scope_b=str(context_b) if context_b is not None else None,
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


def maintain(
    *,
    root: str = ".",
    action: str,
    scope: dict[str, Any] | None = None,
    targets: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    authority: dict[str, Any] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    from core_memory.management import maintain as core_maintain

    out = core_maintain(
        root=root,
        action=action,
        scope=dict(scope or {}),
        targets=dict(targets or {}),
        proposal=dict(proposal or {}),
        decision=dict(decision or {}),
        authority=dict(authority or {}),
        dry_run=bool(dry_run),
        apply=bool(apply),
        idempotency_key=str(idempotency_key or ""),
    )
    if isinstance(out, dict):
        out = dict(out)
        out.setdefault("contract", "mcp.maintain.v1")
    return out

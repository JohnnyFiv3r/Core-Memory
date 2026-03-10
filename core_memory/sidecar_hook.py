"""DEPRECATED transitional compatibility module.

Canonical replacement: `core_memory.event_ingress`.

This file is retained as a temporary shim backing for migration. New
runtime-facing ingress code should import `event_ingress` instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .sidecar import TurnEnvelope, emit_memory_event, get_memory_pass, mark_memory_pass, sha256_hex


def should_emit_memory_event(trace_depth: int, origin: str) -> bool:
    """Emit only for top-level non-memory-pass turns."""
    if trace_depth != 0:
        return False
    if (origin or "").upper() == "MEMORY_PASS":
        return False
    return True


def _normalize_tools_trace(entries: Optional[list[dict]]) -> list[dict]:
    out: list[dict] = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        tool_call_id = str(e.get("tool_call_id") or e.get("id") or "").strip() or None
        category = str(e.get("category") or e.get("tool") or "").strip() or None
        redaction_applied = bool(e.get("redaction_applied", False))

        result_hash = str(e.get("result_hash") or "").strip()
        if not result_hash:
            result_basis = e.get("result")
            if result_basis is not None:
                result_hash = sha256_hex(str(result_basis))

        out.append(
            {
                "tool_call_id": tool_call_id,
                "category": category,
                "result_hash": result_hash or None,
                "redaction_applied": redaction_applied,
            }
        )
    return out


def _normalize_mesh_trace(entries: Optional[list[dict]]) -> list[dict]:
    out: list[dict] = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        capability = str(e.get("capability") or e.get("name") or "").strip() or None
        parent_span_id = str(e.get("parent_span_id") or e.get("parent") or "").strip() or None

        input_hash = str(e.get("input_hash") or "").strip()
        if not input_hash and e.get("input") is not None:
            input_hash = sha256_hex(str(e.get("input")))

        output_hash = str(e.get("output_hash") or "").strip()
        if not output_hash and e.get("output") is not None:
            output_hash = sha256_hex(str(e.get("output")))

        out.append(
            {
                "capability": capability,
                "parent_span_id": parent_span_id,
                "input_hash": input_hash or None,
                "output_hash": output_hash or None,
            }
        )
    return out


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

    md = dict(metadata or {})
    full_text = assistant_final or ""
    store_full_text = bool(md.get("store_full_text", os.environ.get("CORE_MEMORY_STORE_FULL_TEXT", "1") != "0"))
    assistant_final_ref = None
    assistant_final_value = assistant_final

    if not store_full_text and full_text:
        content_hash = sha256_hex(full_text)
        private_dir = root_path / ".beads" / "events" / "private"
        private_dir.mkdir(parents=True, exist_ok=True)
        blob = private_dir / f"{content_hash}.txt"
        if not blob.exists():
            blob.write_text(full_text, encoding="utf-8")
        assistant_final_ref = str(blob)
        snippet = full_text[:160].strip()
        assistant_final_value = f"[redacted] {snippet}" if snippet else "[redacted]"
        md["privacy_mode"] = "ref_only"

    envelope = TurnEnvelope(
        session_id=session_id,
        turn_id=turn_id,
        transaction_id=transaction_id,
        trace_id=trace_id,
        origin=origin,
        user_query=user_query,
        assistant_final=assistant_final_value,
        assistant_final_ref=assistant_final_ref,
        tools_trace=_normalize_tools_trace(tools_trace),
        mesh_trace=_normalize_mesh_trace(mesh_trace),
        window_turn_ids=window_turn_ids or [],
        window_bead_ids=window_bead_ids or [],
        metadata=md,
    )
    envelope.finalize_hashes(full_text_override=full_text if not store_full_text else None)

    if prior and prior.get("status") == "done":
        if prior.get("envelope_hash") == envelope.envelope_hash:
            return {"emitted": False, "reason": "idempotent_done"}
        # same turn_id, changed final output -> mutation/amend path
        previous_envelope_hash = str(prior.get("envelope_hash") or "")
        envelope.metadata = dict(envelope.metadata or {})
        if previous_envelope_hash:
            envelope.metadata["supersedes_envelope_hash"] = previous_envelope_hash
        mark_memory_pass(
            root_path,
            session_id,
            turn_id,
            "pending",
            envelope.envelope_hash,
            reason="turn_mutation",
            supersedes_envelope_hash=previous_envelope_hash,
        )
        event = emit_memory_event(root_path, envelope)
        payload = {"event": event.to_dict(), "envelope": envelope.to_dict()}
        return {
            "emitted": True,
            "reason": "turn_mutation",
            "event_id": event.event_id,
            "assistant_final_hash": envelope.assistant_final_hash,
            "supersedes_envelope_hash": previous_envelope_hash,
            "payload": payload,
        }

    mark_memory_pass(root_path, session_id, turn_id, "pending", envelope.envelope_hash)
    event = emit_memory_event(root_path, envelope)
    payload = {"event": event.to_dict(), "envelope": envelope.to_dict()}
    return {
        "emitted": True,
        "reason": "emitted",
        "event_id": event.event_id,
        "assistant_final_hash": envelope.assistant_final_hash,
        "payload": payload,
    }

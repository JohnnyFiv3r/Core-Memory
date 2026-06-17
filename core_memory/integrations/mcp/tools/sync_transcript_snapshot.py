"""MCP `sync_transcript_snapshot` tool wrapper."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core_memory.integrations.mcp.tools.ingest import ingest_handler
from core_memory.runtime.associations.coverage import enqueue_association_coverage

_CHECKPOINT_LIST_FIELDS = ("durable_facts", "decisions", "preferences", "open_threads")
_SNAPSHOT_REASONS = {
    "periodic",
    "milestone",
    "user_requested",
    "before_compaction",
    "end_of_session",
}
_STABLE_ID_FIELDS = ("transcript_id", "conversation_id", "session_id")


def _error(code: str, message: str, *, field: str = "", received: Any = None) -> dict[str, Any]:
    data: dict[str, Any] = {"tool": "sync_transcript_snapshot"}
    if field:
        data["field"] = field
    if received is not None:
        data["received"] = received
    return {"ok": False, "error": {"code": code, "message": message, "data": data}}


def _stable_hash(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _safe_id_part(value: Any) -> str:
    text = str(value or "").strip()
    out = []
    for char in text:
        if char.isalnum() or char in {"_", ".", ":", "-"}:
            out.append(char)
        elif char.isspace() or char in {"/", "\\"}:
            out.append("-")
    return "".join(out).strip("-_.:")[:96]


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _content_lines(label: str, values: list[Any]) -> list[str]:
    lines: list[str] = []
    clean_values = [v for v in values if str(v).strip()]
    if not clean_values:
        return lines
    lines.append(f"{label}:")
    for value in clean_values:
        if isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        else:
            rendered = str(value)
        lines.append(f"- {rendered}")
    return lines


def _checkpoint_turn(payload: dict[str, Any]) -> dict[str, Any] | None:
    summary = str(payload.get("checkpoint_summary") or "").strip()
    blocks: list[str] = []
    if summary:
        blocks.append("Checkpoint summary:\n" + summary)
    labels = {
        "durable_facts": "Durable facts",
        "decisions": "Decisions",
        "preferences": "Preferences",
        "open_threads": "Open threads",
    }
    for field in _CHECKPOINT_LIST_FIELDS:
        lines = _content_lines(labels[field], _as_list(payload.get(field)))
        if lines:
            blocks.append("\n".join(lines))
    if not blocks:
        return None
    return {
        "speaker": "core_memory_checkpoint",
        "role": "assistant",
        "content": "\n\n".join(blocks),
        "metadata": {
            "checkpoint": True,
            "checkpoint_kind": "model_authored",
        },
    }


def _snapshot_reason(payload: dict[str, Any]) -> str:
    reason = str(payload.get("snapshot_reason") or "periodic").strip().lower()
    return reason if reason in _SNAPSHOT_REASONS else "periodic"


def _resolve_transcript_id(payload: dict[str, Any], *, source_system: str, source_client: str) -> str:
    explicit = _safe_id_part(payload.get("transcript_id"))
    if explicit:
        return explicit

    conversation_id = str(payload.get("conversation_id") or "").strip()
    if conversation_id:
        digest = _stable_hash(
            {"source_system": source_system, "source_client": source_client, "conversation_id": conversation_id}
        )
        return f"conversation:{digest[:16]}"

    session_id = _safe_id_part(payload.get("session_id"))
    if session_id:
        return f"session:{session_id}"

    return ""


def sync_transcript_snapshot_handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replay a visible transcript snapshot through canonical ingest semantics.

    This is a model-obvious wrapper for periodic/full transcript sync. It does
    not change the lower-level `capture` contract; it delegates to `ingest`.
    """

    payload = dict(payload or {})
    if payload.get("user_opted_in") is not True:
        return _error(
            "cm.snapshot_opt_in_required",
            "sync_transcript_snapshot requires user_opted_in=true from an explicit user/app sync opt-in",
            field="user_opted_in",
            received=payload.get("user_opted_in"),
        )

    turns = payload.get("turns")
    messages = payload.get("messages")
    has_turns = isinstance(turns, list)
    has_messages = isinstance(messages, list)
    if has_turns and has_messages:
        return _error(
            "cm.snapshot_ambiguous_input",
            "sync_transcript_snapshot accepts turns or messages, not both",
            field="turns",
        )

    snapshot_mode = str(payload.get("snapshot_mode") or "full").strip().lower()
    if snapshot_mode not in {"full", "checkpoint"}:
        snapshot_mode = "full"

    source_rows: list[Any]
    if has_turns:
        source_rows = list(turns or [])
    elif has_messages:
        source_rows = list(messages or [])
    else:
        recent_turns = _as_list(payload.get("recent_turns"))
        checkpoint = _checkpoint_turn(payload)
        if not recent_turns and checkpoint is None:
            return _error(
                "cm.snapshot_requires_turns",
                "sync_transcript_snapshot requires turns, messages, or checkpoint content",
                field="turns",
            )
        source_rows = list(recent_turns)
        if checkpoint is not None:
            source_rows.append(checkpoint)
        snapshot_mode = "checkpoint"

    if snapshot_mode == "checkpoint" and (has_turns or has_messages):
        checkpoint = _checkpoint_turn(payload)
        if checkpoint is not None:
            source_rows.append(checkpoint)

    transcript_hash = _stable_hash({"snapshot_mode": snapshot_mode, "rows": source_rows})
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    source_client = str(payload.get("source_client") or metadata.get("source_client") or "").strip()
    source_system = str(
        payload.get("source_system") or metadata.get("source_system") or source_client or "chat_mcp"
    ).strip()
    transcript_id = _resolve_transcript_id(payload, source_system=source_system, source_client=source_client)
    if not transcript_id:
        return _error(
            "cm.snapshot_stable_id_required",
            "sync_transcript_snapshot requires a stable transcript_id, conversation_id, or session_id",
            field="transcript_id",
            received={field: payload.get(field) for field in _STABLE_ID_FIELDS if payload.get(field) is not None},
        )

    metadata.setdefault("source_system", source_system)
    metadata.setdefault("source_client", source_client or source_system)
    if payload.get("conversation_id") is not None:
        metadata.setdefault("conversation_id", str(payload.get("conversation_id") or ""))
    metadata.setdefault("capture_surface", "mcp_tool")
    metadata.setdefault("snapshot_kind", "visible_transcript")
    metadata.setdefault("snapshot_mode", snapshot_mode)
    metadata.setdefault("snapshot_reason", _snapshot_reason(payload))
    metadata.setdefault("conversation_label", str(payload.get("conversation_label") or ""))
    metadata.setdefault("previous_snapshot_hash", str(payload.get("previous_snapshot_hash") or ""))
    metadata.setdefault("transcript_hash", transcript_hash)
    metadata.setdefault("user_opted_in", True)
    if snapshot_mode == "checkpoint":
        metadata.setdefault("checkpoint_kind", "model_authored")
        for field in _CHECKPOINT_LIST_FIELDS:
            if isinstance(payload.get(field), list):
                metadata.setdefault(field, list(payload.get(field) or []))

    ingest_payload: dict[str, Any] = {
        "session_id": payload.get("session_id"),
        "session_prefix": payload.get("session_prefix") or "transcript_snapshot",
        "transcript_id": transcript_id,
        "flush_policy": payload.get("flush_policy") or "end_only",
        "max_turns": payload.get("max_turns") or 1000,
        "mode": payload.get("mode") or "group",
        "window_size": payload.get("window_size") or 10,
        "metadata": metadata,
        "root": payload.get("root"),
    }
    if has_messages and snapshot_mode == "full":
        ingest_payload["messages"] = source_rows
    else:
        ingest_payload["turns"] = source_rows

    result = ingest_handler({k: v for k, v in ingest_payload.items() if v is not None})
    result = dict(result or {})
    if not result.get("ok"):
        err = result.get("error")
        if isinstance(err, dict) and isinstance(err.get("data"), dict):
            err["data"]["tool"] = "sync_transcript_snapshot"
    result["tool"] = "sync_transcript_snapshot"
    result["transcript_hash"] = transcript_hash
    result["snapshot_mode"] = snapshot_mode
    if result.get("ok") and result.get("bead_ids"):
        try:
            coverage = enqueue_association_coverage(
                root=str(payload.get("root") or "."),
                bead_ids=[str(x) for x in (result.get("bead_ids") or []) if str(x).strip()],
                session_id=str(ingest_payload.get("session_id") or ""),
                trigger="transcript_sync",
                run_inline=True,
            )
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            coverage = {"ok": False, "error": str(exc)}
        result["association_coverage"] = coverage
        result["association_run_id"] = str(coverage.get("run_id") or "")
        result["association_trigger"] = "transcript_sync"
    return result

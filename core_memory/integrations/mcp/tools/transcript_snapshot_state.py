"""Durable state for MCP transcript snapshot sync."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import atomic_write_json, store_lock

TRANSCRIPT_SNAPSHOT_SCHEMA_VERSION = "transcript_snapshot.v1"
MCP_TOOLS_VERSION = "mcp-tools.2026-06-17"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _state_path(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events" / "transcript-snapshot-state.json"


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": TRANSCRIPT_SNAPSHOT_SCHEMA_VERSION,
        "snapshots_by_key": {},
        "latest": None,
        "last_error": "",
    }


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return copy.deepcopy(value)
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return str(value)


def _load_state_unlocked(root: str | Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    state = _empty_state()
    state.update({k: v for k, v in data.items() if k in state})
    if not isinstance(state.get("snapshots_by_key"), dict):
        state["snapshots_by_key"] = {}
    if state.get("latest") is not None and not isinstance(state.get("latest"), dict):
        state["latest"] = None
    return state


def _write_state_unlocked(root: str | Path, state: dict[str, Any]) -> None:
    clean = _empty_state()
    clean.update(dict(state or {}))
    snapshots = clean.get("snapshots_by_key")
    if not isinstance(snapshots, dict):
        snapshots = {}
    if len(snapshots) > 1000:
        ordered = sorted(
            snapshots.items(),
            key=lambda item: str((item[1] or {}).get("updated_at") or (item[1] or {}).get("created_at") or ""),
        )
        snapshots = dict(ordered[-1000:])
    clean["snapshots_by_key"] = snapshots
    atomic_write_json(_state_path(root), _jsonable(clean))


def snapshot_id_for_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(str(idempotency_key or "").encode("utf-8")).hexdigest()
    return f"snap_{digest[:16]}"


def load_transcript_snapshot_state(root: str | Path) -> dict[str, Any]:
    with store_lock(Path(root)):
        return _load_state_unlocked(root)


def reserve_transcript_snapshot(
    root: str | Path,
    *,
    idempotency_key: str,
    fingerprint: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Reserve a snapshot write or return an idempotent replay record."""

    key = str(idempotency_key or "").strip()
    fp = str(fingerprint or "").strip()
    if not key or not fp:
        return {
            "ok": False,
            "error": {
                "code": "cm.snapshot_idempotency_invalid",
                "message": "idempotency_key and fingerprint are required",
            },
        }

    now = _now_iso()
    with store_lock(Path(root)):
        state = _load_state_unlocked(root)
        snapshots = state.setdefault("snapshots_by_key", {})
        existing = snapshots.get(key)
        if isinstance(existing, dict):
            if str(existing.get("fingerprint") or "") != fp:
                return {
                    "ok": False,
                    "error": {
                        "code": "cm.snapshot_idempotency_conflict",
                        "message": "idempotency_key was already used for different transcript snapshot content",
                        "data": {
                            "idempotency_key": key,
                            "snapshot_id": existing.get("snapshot_id") or snapshot_id_for_key(key),
                        },
                    },
                }
            status = str(existing.get("status") or "")
            if status == "done":
                return {"ok": True, "duplicate": True, "status": "done", "record": copy.deepcopy(existing)}
            if status == "pending":
                return {"ok": True, "duplicate": True, "status": "pending", "record": copy.deepcopy(existing)}

        attempts = int((existing or {}).get("attempts") or 0) + 1 if isinstance(existing, dict) else 1
        record = {
            "snapshot_id": snapshot_id_for_key(key),
            "idempotency_key": key,
            "fingerprint": fp,
            "status": "pending",
            "attempts": attempts,
            "created_at": str((existing or {}).get("created_at") or now) if isinstance(existing, dict) else now,
            "updated_at": now,
            "completed_at": "",
            "metadata": dict(metadata or {}),
            "result": {},
            "last_error": "",
        }
        snapshots[key] = record
        _write_state_unlocked(root, state)
        return {"ok": True, "duplicate": False, "status": "pending", "record": copy.deepcopy(record)}


def complete_transcript_snapshot(root: str | Path, *, idempotency_key: str, result: dict[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    key = str(idempotency_key or "").strip()
    with store_lock(Path(root)):
        state = _load_state_unlocked(root)
        snapshots = state.setdefault("snapshots_by_key", {})
        record = dict(snapshots.get(key) or {})
        record.setdefault("snapshot_id", snapshot_id_for_key(key))
        record.setdefault("idempotency_key", key)
        record["status"] = "done"
        record["updated_at"] = now
        record["completed_at"] = now
        record["result"] = _jsonable(dict(result or {}))
        record["last_error"] = ""
        snapshots[key] = record
        state["latest"] = {
            "snapshot_id": record.get("snapshot_id"),
            "idempotency_key": key,
            "completed_at": now,
            "transcript_hash": result.get("transcript_hash"),
            "snapshot_mode": result.get("snapshot_mode"),
            "conversation_id": (record.get("metadata") or {}).get("conversation_id"),
            "session_id": result.get("session_id") or (record.get("metadata") or {}).get("session_id"),
            "source_client": (record.get("metadata") or {}).get("source_client"),
            "source_system": (record.get("metadata") or {}).get("source_system"),
        }
        state["last_error"] = ""
        _write_state_unlocked(root, state)
        return copy.deepcopy(record)


def fail_transcript_snapshot(root: str | Path, *, idempotency_key: str, error: Any) -> dict[str, Any]:
    now = _now_iso()
    key = str(idempotency_key or "").strip()
    error_text = str(error or "")
    with store_lock(Path(root)):
        state = _load_state_unlocked(root)
        snapshots = state.setdefault("snapshots_by_key", {})
        record = dict(snapshots.get(key) or {})
        record.setdefault("snapshot_id", snapshot_id_for_key(key))
        record.setdefault("idempotency_key", key)
        record["status"] = "failed"
        record["updated_at"] = now
        record["last_error"] = error_text
        snapshots[key] = record
        state["last_error"] = error_text
        _write_state_unlocked(root, state)
        return copy.deepcopy(record)

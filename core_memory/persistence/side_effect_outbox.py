"""Low-level durable storage for runtime side-effect events."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import store_lock


def side_effect_queue_path(root: str | Path) -> Path:
    path = Path(root) / ".beads" / "events" / "side-effects-queue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def side_effect_state_path(root: str | Path) -> Path:
    path = Path(root) / ".beads" / "events" / "side-effects-queue-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_side_effect_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_side_effect_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def default_side_effect_state() -> dict[str, Any]:
    return {"consecutive_failures": 0, "opened_until": 0, "last_error": ""}


def load_side_effect_outbox_locked(root: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue = read_side_effect_json(side_effect_queue_path(root), [])
    state = read_side_effect_json(side_effect_state_path(root), default_side_effect_state())
    if not isinstance(queue, list):
        queue = []
    if not isinstance(state, dict):
        state = default_side_effect_state()
    return [row for row in queue if isinstance(row, dict)], dict(state)


def persist_side_effect_outbox_locked(
    root: str | Path,
    queue: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    write_side_effect_json(side_effect_queue_path(root), list(queue))
    write_side_effect_json(side_effect_state_path(root), dict(state))


def enqueue_persisted_side_effect(
    *,
    root: str | Path,
    kind: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Append an idempotent event without owning runtime kind semantics."""

    with store_lock(Path(root)):
        return enqueue_persisted_side_effect_locked(
            root=root,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key,
        )


def enqueue_persisted_side_effect_locked(
    *,
    root: str | Path,
    kind: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Append an event while the caller already owns the root store lock."""

    normalized_kind = str(kind or "").strip().lower()
    if not normalized_kind:
        return {"ok": False, "error": {"code": "missing_kind"}}
    idem = str(idempotency_key or "").strip()
    queue, state = load_side_effect_outbox_locked(root)
    if idem:
        for item in queue:
            if str((item or {}).get("idempotency_key") or "") == idem:
                return {
                    "ok": True,
                    "duplicate": True,
                    "id": item.get("id"),
                    "queue_depth": len(queue),
                    "kind": normalized_kind,
                }
    item = {
        "id": f"se-{uuid.uuid4().hex[:12]}",
        "kind": normalized_kind,
        "payload": dict(payload or {}),
        "idempotency_key": idem or None,
        "created_at": int(time.time()),
        "attempts": 0,
        "next_retry_at": 0,
        "lease_until": 0,
        "lease_token": None,
    }
    queue.append(item)
    persist_side_effect_outbox_locked(root, queue, state)
    return {
        "ok": True,
        "duplicate": False,
        "id": item["id"],
        "queue_depth": len(queue),
        "kind": normalized_kind,
    }


__all__ = [
    "default_side_effect_state",
    "enqueue_persisted_side_effect",
    "enqueue_persisted_side_effect_locked",
    "load_side_effect_outbox_locked",
    "persist_side_effect_outbox_locked",
    "read_side_effect_json",
    "side_effect_queue_path",
    "side_effect_state_path",
    "write_side_effect_json",
]

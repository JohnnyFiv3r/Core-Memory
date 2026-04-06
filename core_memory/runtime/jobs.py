from __future__ import annotations

"""Canonical async job/queue observability helpers.

This module intentionally exposes read-only status surfaces so operators can
inspect background work state without coupling to queue implementation details.
"""

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from core_memory.persistence.store import DEFAULT_ROOT


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(default)


def _semantic_queue_path(root: Path) -> Path:
    return root / ".beads" / "semantic" / "rebuild-queue.json"


def _compaction_queue_path(root: Path) -> Path:
    return root / ".beads" / "events" / "compaction-queue.json"


def _compaction_state_path(root: Path) -> Path:
    return root / ".beads" / "events" / "compaction-queue-state.json"


def semantic_rebuild_queue_status(root: str | Path = DEFAULT_ROOT) -> dict[str, Any]:
    root_p = Path(root)
    p = _semantic_queue_path(root_p)
    payload = _read_json(p, {"queued": False, "queued_at": None, "epoch": 0})
    if not isinstance(payload, dict):
        payload = {"queued": False, "queued_at": None, "epoch": 0}

    queued = bool(payload.get("queued"))
    return {
        "ok": True,
        "kind": "semantic_rebuild",
        "path": str(p),
        "queued": queued,
        "queued_at": payload.get("queued_at"),
        "epoch": int(payload.get("epoch") or 0),
        "pending": 1 if queued else 0,
    }


def compaction_queue_status(root: str | Path = DEFAULT_ROOT, *, now_ts: int | None = None) -> dict[str, Any]:
    root_p = Path(root)
    q_path = _compaction_queue_path(root_p)
    s_path = _compaction_state_path(root_p)

    queue = _read_json(q_path, [])
    if not isinstance(queue, list):
        queue = []

    state = _read_json(s_path, {"consecutive_failures": 0, "opened_until": 0, "last_error": ""})
    if not isinstance(state, dict):
        state = {"consecutive_failures": 0, "opened_until": 0, "last_error": ""}

    now = int(now_ts if now_ts is not None else time.time())
    opened_until = int(state.get("opened_until") or 0)
    circuit_open = opened_until > now

    retry_ready = 0
    next_retry_at: int | None = None
    for item in queue:
        if not isinstance(item, dict):
            continue
        nxt = int(item.get("next_retry_at") or 0)
        if nxt <= now:
            retry_ready += 1
        else:
            if next_retry_at is None or nxt < next_retry_at:
                next_retry_at = nxt

    processable_now = 0 if circuit_open else retry_ready

    return {
        "ok": True,
        "kind": "compaction",
        "path": str(q_path),
        "state_path": str(s_path),
        "queue_depth": len(queue),
        "retry_ready": retry_ready,
        "processable_now": processable_now,
        "next_retry_at": next_retry_at,
        "circuit_open": circuit_open,
        "opened_until": opened_until,
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "last_error": str(state.get("last_error") or ""),
    }


def async_jobs_status(root: str | Path = DEFAULT_ROOT, *, now_ts: int | None = None) -> dict[str, Any]:
    sem = semantic_rebuild_queue_status(root)
    comp = compaction_queue_status(root, now_ts=now_ts)
    pending_total = int(sem.get("pending") or 0) + int(comp.get("queue_depth") or 0)
    processable_now = int(comp.get("processable_now") or 0) + int(sem.get("pending") or 0)
    return {
        "ok": True,
        "root": str(root),
        "queues": {
            "semantic_rebuild": sem,
            "compaction": comp,
        },
        "pending_total": pending_total,
        "processable_now": processable_now,
    }


__all__ = [
    "async_jobs_status",
    "semantic_rebuild_queue_status",
    "compaction_queue_status",
]

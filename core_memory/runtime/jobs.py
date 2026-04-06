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
from core_memory.retrieval.lifecycle import enqueue_semantic_rebuild
from core_memory.runtime.compaction_queue import drain_compaction_queue, enqueue_compaction_event
from core_memory.retrieval.semantic_index import build_semantic_index


ASYNC_JOBS_SCHEMA_VERSION = "core_memory.async_jobs.v1"


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": str(code),
        "message": str(message),
    }
    payload.update(extra)
    return payload


def _with_schema(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out.setdefault("schema_version", ASYNC_JOBS_SCHEMA_VERSION)
    return out


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
    return _with_schema({
        "ok": True,
        "kind": "semantic_rebuild",
        "path": str(p),
        "queued": queued,
        "queued_at": payload.get("queued_at"),
        "epoch": int(payload.get("epoch") or 0),
        "pending": 1 if queued else 0,
    })


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

    return _with_schema({
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
    })


def async_jobs_status(root: str | Path = DEFAULT_ROOT, *, now_ts: int | None = None) -> dict[str, Any]:
    sem = semantic_rebuild_queue_status(root)
    comp = compaction_queue_status(root, now_ts=now_ts)
    pending_total = int(sem.get("pending") or 0) + int(comp.get("queue_depth") or 0)
    processable_now = int(comp.get("processable_now") or 0) + int(sem.get("pending") or 0)
    return _with_schema({
        "ok": True,
        "root": str(root),
        "queues": {
            "semantic_rebuild": sem,
            "compaction": comp,
        },
        "pending_total": pending_total,
        "processable_now": processable_now,
    })


def _normalize_job_kind(kind: str | None) -> str:
    k = str(kind or "").strip().lower().replace("_", "-")
    aliases = {
        "semantic": "semantic-rebuild",
        "semantic-rebuild": "semantic-rebuild",
        "semantic_rebuild": "semantic-rebuild",
        "compaction": "compaction",
        "compaction-flush": "compaction",
    }
    return aliases.get(k, k)


def enqueue_async_job(
    root: str | Path = DEFAULT_ROOT,
    *,
    kind: str,
    event: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue async work on canonical queue surfaces."""
    root_p = Path(root)
    k = _normalize_job_kind(kind)

    if k == "semantic-rebuild":
        out = enqueue_semantic_rebuild(root_p)
        return _with_schema({
            "ok": bool(out.get("ok")),
            "kind": "semantic-rebuild",
            "queue": out,
            "status": semantic_rebuild_queue_status(root_p),
        })

    if k == "compaction":
        out = enqueue_compaction_event(event=dict(event or {}), ctx=dict(ctx or {}), root=str(root_p))
        return _with_schema({
            "ok": bool(out.get("ok")),
            "kind": "compaction",
            "queue": out,
            "status": compaction_queue_status(root_p),
        })

    return _with_schema({
        "ok": False,
        "error": _error(
            "unknown_kind",
            "Unknown async job kind",
            kind=str(kind),
            allowed=["semantic-rebuild", "compaction"],
        ),
    })


def run_async_jobs(
    root: str | Path = DEFAULT_ROOT,
    *,
    run_semantic: bool = True,
    max_compaction: int = 1,
) -> dict[str, Any]:
    """Drain processable async work in a bounded, operator-invoked pass."""
    root_p = Path(root)

    sem_before = semantic_rebuild_queue_status(root_p)
    sem_run: dict[str, Any] = {
        "attempted": bool(run_semantic),
        "ran": False,
        "ok": True,
        "reason": "not_queued",
        "result": None,
    }

    if run_semantic and bool(sem_before.get("queued")):
        sem_run["ran"] = True
        sem_run["reason"] = "queued"
        try:
            result = build_semantic_index(root_p)
            sem_run["result"] = result
            sem_run["ok"] = bool(result.get("ok"))
        except Exception as exc:
            sem_run["result"] = {"ok": False, "error": str(exc)}
            sem_run["ok"] = False

    try:
        comp_run = drain_compaction_queue(root=str(root_p), max_items=max(0, int(max_compaction)))
    except Exception as exc:
        comp_run = {
            "ok": False,
            "processed": 0,
            "failed": 0,
            "queue_depth": 0,
            "error": str(exc),
        }

    status_after = async_jobs_status(root_p)
    ok = bool(sem_run.get("ok")) and bool(comp_run.get("ok")) and bool(status_after.get("ok"))
    errors: list[dict[str, Any]] = []
    if not bool(sem_run.get("ok")):
        errors.append(_error("semantic_run_failed", "Semantic rebuild step failed"))
    if not bool(comp_run.get("ok")):
        errors.append(_error("compaction_run_failed", "Compaction drain step failed"))
    if not bool(status_after.get("ok")):
        errors.append(_error("status_after_failed", "Async status computation failed"))

    return _with_schema({
        "ok": ok,
        "root": str(root_p),
        "semantic_before": sem_before,
        "semantic_run": sem_run,
        "compaction_run": comp_run,
        "status_after": status_after,
        "errors": errors,
    })


__all__ = [
    "async_jobs_status",
    "enqueue_async_job",
    "run_async_jobs",
    "semantic_rebuild_queue_status",
    "compaction_queue_status",
]

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from core_memory.persistence.semantic_lifecycle import (
    enqueue_semantic_rebuild,
    mark_flush_checkpoint,
    mark_semantic_dirty as _mark_semantic_dirty,
    mark_trace_dirty,
    mark_turn_checkpoint,
    semantic_status as _semantic_status,
    semantic_tail as _semantic_tail,
)

_log = logging.getLogger(__name__)

# Auto-drain state: one daemon thread per store root. Runtime ownership stays in
# this retrieval compatibility surface so persistence only mutates durable state.
_DRAIN_LOCK: threading.Lock = threading.Lock()
_DRAIN_THREADS: dict[str, threading.Thread] = {}


def _autodrain_worker(root_str: str) -> None:
    try:
        from core_memory.runtime.queue.jobs import run_async_jobs

        run_async_jobs(root_str, run_semantic=True, max_compaction=0, max_side_effects=0)
    except Exception as exc:
        _log.debug("semantic autodrain worker error for %s: %s", root_str, exc)
    finally:
        with _DRAIN_LOCK:
            _DRAIN_THREADS.pop(root_str, None)


def _maybe_start_autodrain(root: Path) -> None:
    if os.environ.get("CORE_MEMORY_SEMANTIC_AUTODRAIN", "on").strip().lower() == "off":
        return
    root_str = str(root)
    with _DRAIN_LOCK:
        existing = _DRAIN_THREADS.get(root_str)
        if existing is not None and existing.is_alive():
            return
        t = threading.Thread(
            target=_autodrain_worker,
            args=(root_str,),
            daemon=True,
            name=f"semantic-autodrain:{root_str[-24:]}",
        )
        _DRAIN_THREADS[root_str] = t
        t.start()


def mark_semantic_dirty(root: str | Path, *, reason: str, enqueue: bool = True) -> dict[str, Any]:
    out = _mark_semantic_dirty(root, reason=reason, enqueue=enqueue)
    if enqueue:
        _maybe_start_autodrain(Path(root))
    return out


def semantic_status(root: str | Path) -> dict[str, Any]:
    root_p = Path(root)
    status = _semantic_status(root_p)
    autodrain_on = os.environ.get("CORE_MEMORY_SEMANTIC_AUTODRAIN", "on").strip().lower() != "off"
    with _DRAIN_LOCK:
        drain_running = str(root_p) in _DRAIN_THREADS and _DRAIN_THREADS[str(root_p)].is_alive()
    status["autodrain"] = {
        "enabled": autodrain_on,
        "running": drain_running,
    }
    return status


def semantic_tail(root: str | Path, *, limit: int = 20) -> dict[str, Any]:
    out = _semantic_tail(root, limit=limit)
    if isinstance(out.get("summary"), dict):
        out["summary"] = semantic_status(root)
    return out


__all__ = [
    "enqueue_semantic_rebuild",
    "mark_semantic_dirty",
    "semantic_status",
    "semantic_tail",
    "mark_trace_dirty",
    "mark_turn_checkpoint",
    "mark_flush_checkpoint",
]

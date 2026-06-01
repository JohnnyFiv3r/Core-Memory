from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Auto-drain state: one daemon thread per store root.
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _semantic_dir(root: Path) -> Path:
    return root / ".beads" / "semantic"


def _manifest_path(root: Path) -> Path:
    return _semantic_dir(root) / "manifest.json"


def _queue_path(root: Path) -> Path:
    return _semantic_dir(root) / "rebuild-queue.json"


def _event_log_path(root: Path) -> Path:
    return _semantic_dir(root) / "events.jsonl"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def enqueue_semantic_rebuild(root: str | Path, *, mode: str = "delta") -> dict[str, Any]:
    root_p = Path(root)
    q_path = _queue_path(root_p)
    q = _read_json(q_path, {"queued": False, "queued_at": None, "epoch": 0, "mode": "delta"})
    mode_n = str(mode or "delta").strip().lower()
    if mode_n not in {"delta", "reconcile"}:
        mode_n = "delta"
    if not bool(q.get("queued")):
        q["queued"] = True
        q["queued_at"] = _now()
        q["epoch"] = int(q.get("epoch") or 0) + 1
    existing_mode = str(q.get("mode") or "delta").strip().lower()
    if existing_mode not in {"delta", "reconcile"}:
        existing_mode = "delta"
    if mode_n == "reconcile" or existing_mode == "reconcile":
        q["mode"] = "reconcile"
    else:
        q["mode"] = "delta"
    _write_json(q_path, q)
    return {
        "ok": True,
        "queued": bool(q.get("queued")),
        "epoch": int(q.get("epoch") or 0),
        "mode": str(q.get("mode") or "delta"),
    }


def enqueue_semantic_projection_upgrade_reconcile(root: str | Path) -> dict[str, Any]:
    """Queue a full semantic rebuild when persisted rows use an old text projection.

    Projection changes alter every existing row's embedding text.  Delta mode can
    only compare persisted row hashes to the current visible corpus and may use a
    backend-specific update path; existing indexes need the full reconcile path so
    Qdrant/FastEmbed and local rows are rebuilt from the new projection together.
    """
    from core_memory.schema.bead_projection import RETRIEVAL_TEXT_PROJECTION_VERSION

    root_p = Path(root)
    manifest_path = _manifest_path(root_p)
    rows_path = root_p / ".beads" / "semantic" / "rows.jsonl"
    faiss_path = root_p / ".beads" / "semantic" / "index.faiss"
    qdrant_path = root_p / ".beads" / "qdrant"
    if not manifest_path.exists():
        return {"ok": True, "queued": False, "reason": "no_persisted_semantic_index"}

    manifest = _read_json(manifest_path, {})
    if not isinstance(manifest, dict):
        manifest = {}
    has_persisted_index = (
        rows_path.exists()
        or faiss_path.exists()
        or qdrant_path.exists()
        or int(manifest.get("row_count") or 0) > 0
    )
    if not has_persisted_index:
        return {"ok": True, "queued": False, "reason": "no_persisted_semantic_index"}
    current = str(RETRIEVAL_TEXT_PROJECTION_VERSION)
    existing = str(manifest.get("projection_version") or "")
    if existing == current:
        return {"ok": True, "queued": False, "reason": "projection_current", "projection_version": current}

    queued = enqueue_semantic_rebuild(root_p, mode="reconcile")
    return {
        "ok": bool(queued.get("ok")),
        "queued": bool(queued.get("queued")),
        "reason": "projection_upgrade_reconcile_required",
        "previous_projection_version": existing or None,
        "projection_version": current,
        "queue": queued,
    }


def mark_semantic_dirty(root: str | Path, *, reason: str, enqueue: bool = True) -> dict[str, Any]:
    root_p = Path(root)
    m_path = _manifest_path(root_p)
    m = _read_json(
        m_path,
        {
            "dirty": False,
            "last_dirty_at": None,
            "last_dirty_reason": None,
            "last_turn_id": None,
            "last_flush_tx_id": None,
        },
    )
    m["dirty"] = True
    m["last_dirty_at"] = _now()
    m["last_dirty_reason"] = str(reason or "unspecified")
    _write_json(m_path, m)
    q = enqueue_semantic_rebuild(root_p, mode="delta") if enqueue else {"ok": True, "queued": False, "mode": "delta"}
    if enqueue:
        _maybe_start_autodrain(root_p)
    return {"ok": True, "manifest": str(m_path), "queue": q}


def semantic_status(root: str | Path) -> dict[str, Any]:
    """Read semantic lifecycle status without creating or mutating files."""
    root_p = Path(root)
    m_path = _manifest_path(root_p)
    q_path = _queue_path(root_p)
    manifest = _read_json(
        m_path,
        {
            "dirty": False,
            "last_dirty_at": None,
            "last_dirty_reason": None,
            "last_turn_id": None,
            "last_flush_tx_id": None,
        },
    )
    queue = _read_json(q_path, {"queued": False, "queued_at": None, "epoch": 0, "mode": "delta"})
    if not isinstance(manifest, dict):
        manifest = {}
    if not isinstance(queue, dict):
        queue = {}
    mode = str(queue.get("mode") or manifest.get("mode") or "delta").strip().lower()
    if mode not in {"delta", "reconcile"}:
        mode = "delta"
    autodrain_on = os.environ.get("CORE_MEMORY_SEMANTIC_AUTODRAIN", "on").strip().lower() != "off"
    with _DRAIN_LOCK:
        drain_running = str(root_p) in _DRAIN_THREADS and _DRAIN_THREADS[str(root_p)].is_alive()

    return {
        "ok": True,
        "root": str(root_p),
        "manifest_path": str(m_path),
        "queue_path": str(q_path),
        "dirty": bool(manifest.get("dirty")),
        "last_dirty_at": manifest.get("last_dirty_at"),
        "last_dirty_reason": manifest.get("last_dirty_reason"),
        "queue": {
            "queued": bool(queue.get("queued")),
            "queued_at": queue.get("queued_at"),
            "epoch": int(queue.get("epoch") or 0),
            "depth": 1 if bool(queue.get("queued")) else 0,
        },
        "queue_epoch": int(queue.get("epoch") or 0),
        "mode": mode,
        "last_checkpoint": {
            "turn_id": manifest.get("last_turn_id"),
            "flush_tx_id": manifest.get("last_flush_tx_id"),
        },
        "autodrain": {
            "enabled": autodrain_on,
            "running": drain_running,
        },
        "manifest": manifest,
    }


def semantic_tail(root: str | Path, *, limit: int = 20) -> dict[str, Any]:
    """Read recent semantic lifecycle events, or summarize state when no log exists."""
    root_p = Path(root)
    log_path = _event_log_path(root_p)
    n = max(0, int(limit))
    if not log_path.exists():
        return {
            "ok": True,
            "root": str(root_p),
            "path": str(log_path),
            "entries": [],
            "summary": semantic_status(root_p),
        }
    lines = log_path.read_text(encoding="utf-8").splitlines()
    tail_lines = lines[-n:] if n else []
    entries: list[Any] = []
    for line in tail_lines:
        try:
            entries.append(json.loads(line))
        except Exception:
            entries.append({"raw": line})
    return {
        "ok": True,
        "root": str(root_p),
        "path": str(log_path),
        "limit": n,
        "entries": entries,
    }


def mark_trace_dirty(root: str | Path, *, reason: str) -> dict[str, Any]:
    p = Path(root) / ".beads" / "events" / "trace-dirty.json"
    state = _read_json(p, {"dirty": False, "last_dirty_at": None, "last_dirty_reason": None})
    state["dirty"] = True
    state["last_dirty_at"] = _now()
    state["last_dirty_reason"] = str(reason or "unspecified")
    _write_json(p, state)
    return {"ok": True, "path": str(p)}


def mark_turn_checkpoint(root: str | Path, *, turn_id: str) -> dict[str, Any]:
    m_path = _manifest_path(Path(root))
    m = _read_json(m_path, {"last_turn_id": None})
    m["last_turn_id"] = str(turn_id or "")
    _write_json(m_path, m)
    return {"ok": True, "last_turn_id": m["last_turn_id"]}


def mark_flush_checkpoint(root: str | Path, *, flush_tx_id: str) -> dict[str, Any]:
    m_path = _manifest_path(Path(root))
    m = _read_json(m_path, {"last_flush_tx_id": None})
    m["last_flush_tx_id"] = str(flush_tx_id or "")
    _write_json(m_path, m)
    return {"ok": True, "last_flush_tx_id": m["last_flush_tx_id"]}

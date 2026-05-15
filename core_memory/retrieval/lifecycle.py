from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
        },
        "queue_epoch": int(queue.get("epoch") or 0),
        "mode": mode,
        "last_checkpoint": {
            "turn_id": manifest.get("last_turn_id"),
            "flush_tx_id": manifest.get("last_flush_tx_id"),
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

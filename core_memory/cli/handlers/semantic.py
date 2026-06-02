from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.retrieval.lifecycle import enqueue_semantic_rebuild, semantic_status, semantic_tail
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.runtime.queue.jobs import run_async_jobs


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _queue_health(status: dict[str, Any]) -> dict[str, Any]:
    """Derive queue health fields from a semantic_status snapshot."""
    queue = status.get("queue") or {}
    queued = bool(queue.get("queued"))
    queued_at_s = str(queue.get("queued_at") or "")
    stale = False
    stale_seconds = None
    if queued and queued_at_s:
        try:
            queued_at = datetime.fromisoformat(queued_at_s.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age = (now - queued_at).total_seconds()
            stale = age > 300  # 5-minute threshold
            stale_seconds = int(age)
        except Exception:
            pass
    autodrain = status.get("autodrain") or {}
    return {
        "queued": queued,
        "depth": int(queue.get("depth") or (1 if queued else 0)),
        "epoch": int(queue.get("epoch") or 0),
        "queued_at": queued_at_s or None,
        "stale": stale,
        "stale_seconds": stale_seconds,
        "autodrain_enabled": bool(autodrain.get("enabled", True)),
        "autodrain_running": bool(autodrain.get("running", False)),
    }


def handle_semantic_command(*, args: Any, root: str | Path) -> bool:
    """Handle the top-level `core-memory semantic ...` JSON command group.

    This command surface is intentionally thin: it exposes semantic lifecycle
    state and queues existing rebuild workers without owning semantic policy.
    """
    if getattr(args, "command", None) != "semantic":
        return False

    cmd = getattr(args, "semantic_cmd", None)
    root_p = Path(root)

    if cmd == "status":
        out = semantic_status(root_p)
        doctor = semantic_doctor(root_p)
        out["provider"] = doctor.get("provider")
        out["provider_detected"] = doctor.get("provider_detected")
        out["degraded"] = bool(doctor.get("degraded"))
        out["degraded_mode_enabled"] = bool(doctor.get("degraded_mode_enabled"))
        out["queue_health"] = _queue_health(out)
        _print_json(out)
        return True

    if cmd == "rebuild":
        mode = str(getattr(args, "mode", "delta") or "delta")
        queued = enqueue_semantic_rebuild(root_p, mode=mode)
        out: dict[str, Any] = {
            "ok": bool(queued.get("ok")),
            "command": "semantic.rebuild",
            "root": str(root_p),
            "mode": str(queued.get("mode") or mode),
            "queue": queued,
            "wait": bool(getattr(args, "wait", False)),
        }
        if bool(getattr(args, "wait", False)):
            out["run"] = run_async_jobs(root_p, run_semantic=True, max_compaction=0, max_side_effects=0)
            out["ok"] = bool(out["ok"]) and bool((out["run"] or {}).get("ok"))
        _print_json(out)
        if not out.get("ok"):
            raise SystemExit(2)
        return True

    if cmd == "tail":
        _print_json(semantic_tail(root_p, limit=int(getattr(args, "n", 20))))
        return True

    if cmd == "doctor":
        doc = semantic_doctor(root_p)
        status = semantic_status(root_p)
        doc["queue_health"] = _queue_health(status)
        _print_json(doc)
        return True

    if cmd == "backfill":
        dry_run = bool(getattr(args, "dry_run", False))
        status = semantic_status(root_p)
        out = {
            "ok": True,
            "command": "semantic.backfill",
            "root": str(root_p),
            "dry_run": dry_run,
            "mode": "reconcile",
        }
        if dry_run:
            out["would_rebuild"] = True
            out["current_status"] = status
            _print_json(out)
            return True
        queued = enqueue_semantic_rebuild(root_p, mode="reconcile")
        out["queue"] = queued
        run_result = run_async_jobs(root_p, run_semantic=True, max_compaction=0, max_side_effects=0)
        out["run"] = run_result
        out["ok"] = bool(out["ok"]) and bool(run_result.get("ok"))
        _print_json(out)
        if not out.get("ok"):
            raise SystemExit(2)
        return True

    return False

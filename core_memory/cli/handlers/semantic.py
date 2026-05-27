from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core_memory.retrieval.lifecycle import enqueue_semantic_rebuild, semantic_status, semantic_tail
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.runtime.queue.jobs import run_async_jobs


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


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
        _print_json(semantic_doctor(root_p))
        return True

    return False

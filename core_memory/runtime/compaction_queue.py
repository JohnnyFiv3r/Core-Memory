from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from core_memory.persistence.store import DEFAULT_ROOT
from core_memory.runtime.engine import process_flush


def _events_dir(root: str) -> Path:
    p = Path(root) / ".beads" / "events"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _queue_path(root: str) -> Path:
    return _events_dir(root) / "compaction-queue.json"


def _state_path(root: str) -> Path:
    return _events_dir(root) / "compaction-queue-state.json"


def _telemetry_log(root: str) -> Path:
    return _events_dir(root) / "compaction-queue-events.jsonl"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _log_event(root: str, row: dict[str, Any]) -> None:
    lp = _telemetry_log(root)
    with lp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def process_compaction_event(*, event: dict[str, Any], ctx: dict[str, Any] | None = None, root: str | None = None) -> dict[str, Any]:
    """Canonical compaction queue processor.

    This runtime-level processor is adapter-neutral and executes canonical
    flush orchestration. Adapter-specific policy gates (if any) should be
    applied by adapter wrappers before/around this processor.
    """
    root_final = str(root or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT)
    ctx = dict(ctx or {})

    session_id = str(
        ctx.get("sessionId")
        or ctx.get("sessionKey")
        or (event or {}).get("sessionId")
        or (event or {}).get("sessionKey")
        or "main"
    )

    compaction = event.get("compaction") if isinstance((event or {}).get("compaction"), dict) else {}
    token_budget = int(compaction.get("tokenBudget") or event.get("tokenBudget") or 1200)
    max_beads = int(compaction.get("maxBeads") or event.get("maxBeads") or 12)
    promote = bool(compaction.get("promote", True))

    flush_tx_id = str((event or {}).get("runId") or (event or {}).get("id") or f"flush-{uuid.uuid4().hex[:10]}")

    try:
        out = process_flush(
            root=root_final,
            session_id=session_id,
            promote=promote,
            token_budget=token_budget,
            max_beads=max_beads,
            source="compaction_queue",
            flush_tx_id=flush_tx_id,
        )
        return {
            "ok": bool(out.get("ok")),
            "session_id": session_id,
            "flush_tx_id": flush_tx_id,
            "result": out,
        }
    except Exception as exc:
        return {
            "ok": False,
            "session_id": session_id,
            "flush_tx_id": flush_tx_id,
            "error": str(exc),
        }


def enqueue_compaction_event(*, event: dict[str, Any], ctx: dict[str, Any] | None = None, root: str | None = None) -> dict[str, Any]:
    root_final = str(root or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT)
    qpath = _queue_path(root_final)
    queue = _read_json(qpath, [])
    if not isinstance(queue, list):
        queue = []

    item = {
        "id": f"cq-{uuid.uuid4().hex[:12]}",
        "created_at": int(time.time()),
        "attempts": 0,
        "next_retry_at": 0,
        "event": dict(event or {}),
        "ctx": dict(ctx or {}),
    }
    queue.append(item)
    _write_json(qpath, queue)
    _log_event(root_final, {"ts": int(time.time()), "kind": "enqueue", "id": item["id"], "queue_depth": len(queue)})
    return {"ok": True, "queued": 1, "queue_depth": len(queue), "id": item["id"]}


def drain_compaction_queue(
    *,
    root: str | None = None,
    max_items: int = 1,
    processor: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root_final = str(root or os.environ.get("CORE_MEMORY_ROOT") or DEFAULT_ROOT)
    qpath = _queue_path(root_final)
    spath = _state_path(root_final)
    queue = _read_json(qpath, [])
    state = _read_json(spath, {"consecutive_failures": 0, "opened_until": 0, "last_error": ""})
    if not isinstance(queue, list):
        queue = []
    if not isinstance(state, dict):
        state = {"consecutive_failures": 0, "opened_until": 0, "last_error": ""}

    now = int(time.time())
    opened_until = int(state.get("opened_until") or 0)
    if opened_until and now < opened_until:
        return {
            "ok": True,
            "processed": 0,
            "queue_depth": len(queue),
            "circuit_open": True,
            "opened_until": opened_until,
            "last_error": state.get("last_error") or "",
        }

    process_item = processor or process_compaction_event

    processed = 0
    failed = 0
    for item in list(queue):
        if processed >= int(max_items):
            break
        if int(item.get("next_retry_at") or 0) > now:
            continue

        out = process_item(event=item.get("event") or {}, ctx=item.get("ctx") or {}, root=root_final)
        if out.get("ok"):
            processed += 1
            queue.remove(item)
            state["consecutive_failures"] = 0
            state["opened_until"] = 0
            state["last_error"] = ""
            _log_event(root_final, {"ts": now, "kind": "drain_ok", "id": item.get("id"), "queue_depth": len(queue)})
            continue

        failed += 1
        item["attempts"] = int(item.get("attempts") or 0) + 1
        backoff = min(120, 2 ** min(6, item["attempts"]))
        item["next_retry_at"] = now + backoff
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        state["last_error"] = str(out.get("error") or (out.get("result") or {}).get("error") or "compaction_failed")
        if int(state["consecutive_failures"]) >= 3:
            state["opened_until"] = now + 30
        _log_event(root_final, {
            "ts": now,
            "kind": "drain_fail",
            "id": item.get("id"),
            "attempts": item["attempts"],
            "next_retry_at": item["next_retry_at"],
            "error": state["last_error"],
            "circuit_open_until": state.get("opened_until") or 0,
        })

    _write_json(qpath, queue)
    _write_json(spath, state)

    return {
        "ok": True,
        "processed": processed,
        "failed": failed,
        "queue_depth": len(queue),
        "circuit_open": bool(state.get("opened_until") and int(state.get("opened_until")) > int(time.time())),
        "opened_until": int(state.get("opened_until") or 0),
        "last_error": state.get("last_error") or "",
    }

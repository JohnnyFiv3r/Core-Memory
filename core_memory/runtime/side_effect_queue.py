from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.semantic_index import semantic_doctor
from core_memory.integrations.neo4j.sync import sync_to_neo4j
from core_memory import dreamer


_SIDE_EFFECT_KINDS = {"dreamer-run", "neo4j-sync", "health-recompute"}


def _events_dir(root: str | Path) -> Path:
    p = Path(root) / ".beads" / "events"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _queue_path(root: str | Path) -> Path:
    return _events_dir(root) / "side-effects-queue.json"


def _state_path(root: str | Path) -> Path:
    return _events_dir(root) / "side-effects-queue-state.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def enqueue_side_effect_event(
    *,
    root: str | Path,
    kind: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    k = str(kind or "").strip().lower()
    if k not in _SIDE_EFFECT_KINDS:
        return {
            "ok": False,
            "error": {"code": "unknown_kind", "kind": k, "allowed": sorted(_SIDE_EFFECT_KINDS)},
        }

    qpath = _queue_path(root)
    queue = _read_json(qpath, [])
    if not isinstance(queue, list):
        queue = []

    idem = str(idempotency_key or "").strip()
    if idem:
        for item in queue:
            if str((item or {}).get("idempotency_key") or "") == idem:
                return {
                    "ok": True,
                    "duplicate": True,
                    "id": item.get("id"),
                    "queue_depth": len(queue),
                    "kind": k,
                }

    item = {
        "id": f"se-{uuid.uuid4().hex[:12]}",
        "kind": k,
        "payload": dict(payload or {}),
        "idempotency_key": idem or None,
        "created_at": int(time.time()),
        "attempts": 0,
        "next_retry_at": 0,
    }
    queue.append(item)
    _write_json(qpath, queue)
    return {
        "ok": True,
        "duplicate": False,
        "id": item["id"],
        "queue_depth": len(queue),
        "kind": k,
    }


def side_effect_queue_status(root: str | Path, *, now_ts: int | None = None) -> dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    qpath = _queue_path(root)
    spath = _state_path(root)
    queue = _read_json(qpath, [])
    state = _read_json(spath, {"consecutive_failures": 0, "opened_until": 0, "last_error": ""})
    if not isinstance(queue, list):
        queue = []
    if not isinstance(state, dict):
        state = {"consecutive_failures": 0, "opened_until": 0, "last_error": ""}

    opened_until = int(state.get("opened_until") or 0)
    circuit_open = opened_until > now

    ready = 0
    next_retry_at: int | None = None
    by_kind: dict[str, int] = {}
    for item in queue:
        if not isinstance(item, dict):
            continue
        k = str(item.get("kind") or "unknown")
        by_kind[k] = int(by_kind.get(k, 0)) + 1
        nr = int(item.get("next_retry_at") or 0)
        if nr <= now:
            ready += 1
        else:
            if next_retry_at is None or nr < next_retry_at:
                next_retry_at = nr

    return {
        "ok": True,
        "kind": "side_effects",
        "path": str(qpath),
        "state_path": str(spath),
        "queue_depth": len(queue),
        "ready": ready,
        "processable_now": 0 if circuit_open else ready,
        "next_retry_at": next_retry_at,
        "circuit_open": circuit_open,
        "opened_until": opened_until,
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "last_error": str(state.get("last_error") or ""),
        "by_kind": by_kind,
    }


def process_side_effect_event(*, root: str | Path, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    k = str(kind or "").strip().lower()
    p = dict(payload or {})

    if k == "dreamer-run":
        store = MemoryStore(root=str(root))
        out = dreamer.run_analysis(
            store=store,
            novel_only=bool(p.get("novel_only", True)),
            seen_window_runs=int(p.get("seen_window_runs", 3)),
            max_exposure=int(p.get("max_exposure", 10)),
        )
        return {
            "ok": True,
            "kind": k,
            "results": out,
            "result_count": len(out) if isinstance(out, list) else 0,
        }

    if k == "neo4j-sync":
        out = sync_to_neo4j(
            root=str(root),
            session_id=(str(p.get("session_id")) if p.get("session_id") is not None else None),
            dry_run=bool(p.get("dry_run", False)),
            prune=bool(p.get("prune", False)),
        )
        # Non-runtime dependency/config issues are treated as terminal skip rather
        # than perpetual retries.
        if not bool(out.get("ok")):
            errs = [e for e in (out.get("errors") or []) if isinstance(e, dict)]
            terminal_codes = {"neo4j_disabled", "neo4j_dependency_missing", "neo4j_config_error"}
            if any(str(e.get("code") or "") in terminal_codes for e in errs):
                return {
                    "ok": True,
                    "kind": k,
                    "terminal_skipped": True,
                    "result": out,
                }
        return {
            "ok": bool(out.get("ok")),
            "kind": k,
            "result": out,
            "error": (out.get("errors") or [{}])[0] if not bool(out.get("ok")) else None,
        }

    if k == "health-recompute":
        out = semantic_doctor(Path(root))
        return {
            "ok": True,
            "kind": k,
            "result": out,
        }

    return {
        "ok": False,
        "kind": k,
        "error": {"code": "unknown_kind", "kind": k, "allowed": sorted(_SIDE_EFFECT_KINDS)},
    }


def drain_side_effect_queue(
    *,
    root: str | Path,
    max_items: int = 2,
    processor: Callable[..., dict[str, Any]] | None = None,
    now_ts: int | None = None,
) -> dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    qpath = _queue_path(root)
    spath = _state_path(root)

    queue = _read_json(qpath, [])
    state = _read_json(spath, {"consecutive_failures": 0, "opened_until": 0, "last_error": ""})
    if not isinstance(queue, list):
        queue = []
    if not isinstance(state, dict):
        state = {"consecutive_failures": 0, "opened_until": 0, "last_error": ""}

    opened_until = int(state.get("opened_until") or 0)
    if opened_until > now:
        return {
            "ok": True,
            "processed": 0,
            "failed": 0,
            "queue_depth": len(queue),
            "circuit_open": True,
            "opened_until": opened_until,
            "last_error": str(state.get("last_error") or ""),
        }

    process_item = processor or process_side_effect_event

    processed = 0
    failed = 0
    skipped_terminal = 0
    for item in list(queue):
        if processed >= max(0, int(max_items)):
            break
        if int(item.get("next_retry_at") or 0) > now:
            continue

        out = process_item(root=root, kind=str(item.get("kind") or ""), payload=dict(item.get("payload") or {}))
        if bool(out.get("ok")):
            processed += 1
            if bool(out.get("terminal_skipped")):
                skipped_terminal += 1
            queue.remove(item)
            state["consecutive_failures"] = 0
            state["opened_until"] = 0
            state["last_error"] = ""
            continue

        failed += 1
        item["attempts"] = int(item.get("attempts") or 0) + 1
        backoff = min(300, 2 ** min(8, item["attempts"]))
        item["next_retry_at"] = now + backoff
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        err = out.get("error") or {}
        state["last_error"] = str((err.get("code") if isinstance(err, dict) else err) or "side_effect_failed")
        if int(state["consecutive_failures"]) >= 3:
            state["opened_until"] = now + 30

    _write_json(qpath, queue)
    _write_json(spath, state)

    return {
        "ok": True,
        "processed": processed,
        "failed": failed,
        "skipped_terminal": skipped_terminal,
        "queue_depth": len(queue),
        "circuit_open": bool(int(state.get("opened_until") or 0) > int(time.time())),
        "opened_until": int(state.get("opened_until") or 0),
        "last_error": str(state.get("last_error") or ""),
    }


__all__ = [
    "enqueue_side_effect_event",
    "side_effect_queue_status",
    "drain_side_effect_queue",
    "process_side_effect_event",
]

"""Metrics operations extracted from MemoryStore.

Handles all KPI tracking, metrics state, reports, and autonomy analysis.
Methods here accept a `store` parameter (MemoryStore instance) for shared state.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..persistence.io_utils import store_lock, append_jsonl


def read_metrics_state(store: Any) -> dict:
    default = {
        "current": {
            "run_id": None,
            "task_id": None,
            "mode": "core_memory",
            "phase": "core_memory",
            "steps": 0,
            "tool_calls": 0,
            "turns_processed": 0,
            "beads_created": 0,
            "beads_recalled": 0,
        }
    }
    if not store.metrics_state_file.exists():
        return default
    try:
        data = json.loads(store.metrics_state_file.read_text(encoding="utf-8"))
        data.setdefault("current", {})
        for k, v in default["current"].items():
            data["current"].setdefault(k, v)
        return data
    except json.JSONDecodeError:
        return default


def write_metrics_state(store: Any, state: dict) -> None:
    store.metrics_state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.metrics_state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(store.metrics_state_file)


def start_task_run(store: Any, run_id: str, task_id: str, mode: str = "core_memory", phase: str = "core_memory") -> dict:
    with store_lock(store.root):
        state = read_metrics_state(store)
        state["current"] = {
            "run_id": run_id,
            "task_id": task_id,
            "mode": mode,
            "phase": phase,
            "steps": 0,
            "tool_calls": 0,
            "turns_processed": 0,
            "beads_created": 0,
            "beads_recalled": 0,
        }
        write_metrics_state(store, state)
        return state["current"]


def _track(store: Any, field: str, count: int = 1) -> dict:
    with store_lock(store.root):
        state = read_metrics_state(store)
        state.setdefault("current", {}).setdefault(field, 0)
        state["current"][field] += max(0, int(count))
        write_metrics_state(store, state)
        return state["current"]


def track_step(store: Any, count: int = 1) -> dict:
    return _track(store, "steps", count)


def track_tool_call(store: Any, count: int = 1) -> dict:
    return _track(store, "tool_calls", count)


def track_turn_processed(store: Any, count: int = 1) -> dict:
    return _track(store, "turns_processed", count)


def track_bead_created(store: Any, count: int = 1) -> dict:
    return _track(store, "beads_created", count)


def track_bead_recalled(store: Any, count: int = 1) -> dict:
    return _track(store, "beads_recalled", count)


def current_run_metrics(store: Any) -> dict:
    with store_lock(store.root):
        return read_metrics_state(store).get("current", {})


def finalize_task_run(store: Any, result: str = "success", **extra: Any) -> dict:
    cur = current_run_metrics(store)
    turns = int(cur.get("turns_processed", 0) or 0)
    beads_created = int(cur.get("beads_created", 0) or 0)
    compression_ratio = (turns / beads_created) if beads_created > 0 else 0.0
    rec = {
        "run_id": cur.get("run_id"),
        "task_id": cur.get("task_id"),
        "mode": cur.get("mode"),
        "phase": cur.get("phase"),
        "result": result,
        "steps": cur.get("steps", 0),
        "tool_calls": cur.get("tool_calls", 0),
        "beads_created": beads_created,
        "beads_recalled": int(cur.get("beads_recalled", 0) or 0),
        "turns_processed": turns,
        "compression_ratio": compression_ratio,
    }
    rec.update(extra)
    return append_metric(store, rec)


def append_metric(store: Any, record: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    cur = current_run_metrics(store)
    m = {
        "ts": record.get("ts", now),
        "run_id": record.get("run_id") or cur.get("run_id") or f"run-{uuid.uuid4().hex[:12]}",
        "mode": record.get("mode") or cur.get("mode") or "core_memory",
        "task_id": record.get("task_id") or cur.get("task_id") or "unknown",
        "result": record.get("result", "success"),
        "steps": int(record.get("steps", cur.get("steps", 0)) or 0),
        "tool_calls": int(record.get("tool_calls", cur.get("tool_calls", 0)) or 0),
        "beads_created": int(record.get("beads_created", cur.get("beads_created", 0)) or 0),
        "beads_recalled": int(record.get("beads_recalled", cur.get("beads_recalled", 0)) or 0),
    }
    for k in ("repeat_failure", "task_completed", "plan_text"):
        if k in record:
            m[k] = record[k]

    kpi_file = store.root / ".beads" / "events" / "kpi-log.jsonl"
    kpi_file.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(kpi_file, m)
    return {"ok": True, "metric": m}

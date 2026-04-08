from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from core_memory.persistence import events
from core_memory.persistence.io_utils import store_lock


def _default_state() -> dict[str, Any]:
    return {
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


def read_metrics_state_for_store(store: Any) -> dict[str, Any]:
    default = _default_state()
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


def write_metrics_state_for_store(store: Any, state: dict[str, Any]) -> None:
    store.metrics_state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.metrics_state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(store.metrics_state_file)


def start_task_run_for_store(store: Any, run_id: str, task_id: str, mode: str = "core_memory", phase: str = "core_memory") -> dict[str, Any]:
    with store_lock(store.root):
        state = read_metrics_state_for_store(store)
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
        write_metrics_state_for_store(store, state)
        return state["current"]


def increment_metric_counter_for_store(store: Any, *, key: str, count: int = 1) -> dict[str, Any]:
    with store_lock(store.root):
        state = read_metrics_state_for_store(store)
        state.setdefault("current", {}).setdefault(key, 0)
        state["current"][key] += max(0, int(count))
        write_metrics_state_for_store(store, state)
        return state["current"]


def current_run_metrics_for_store(store: Any) -> dict[str, Any]:
    with store_lock(store.root):
        return read_metrics_state_for_store(store).get("current", {})


def append_metric_for_store(store: Any, record: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    current = current_run_metrics_for_store(store)
    m = {
        "ts": record.get("ts", now),
        "run_id": record.get("run_id") or current.get("run_id") or f"run-{uuid.uuid4().hex[:12]}",
        "mode": record.get("mode") or current.get("mode") or "core_memory",
        "task_id": record.get("task_id") or current.get("task_id") or "unknown",
        "result": record.get("result", "success"),
        "steps": int(record.get("steps", current.get("steps", 0)) or 0),
        "tool_calls": int(record.get("tool_calls", current.get("tool_calls", 0)) or 0),
        "beads_created": int(record.get("beads_created", current.get("beads_created", 0)) or 0),
        "beads_recalled": int(record.get("beads_recalled", current.get("beads_recalled", 0)) or 0),
        "repeat_failure": bool(record.get("repeat_failure", False)),
        "decision_conflicts": int(record.get("decision_conflicts", 0) or 0),
        "unjustified_flips": int(record.get("unjustified_flips", 0) or 0),
        "rationale_recall_score": int(record.get("rationale_recall_score", 0) or 0),
        "turns_processed": int(record.get("turns_processed", current.get("turns_processed", 0)) or 0),
        "compression_ratio": float(record.get("compression_ratio", 0) or 0),
        "phase": record.get("phase") or current.get("phase") or "core_memory",
    }
    if m["compression_ratio"] <= 0 and m["beads_created"] > 0 and m["turns_processed"] > 0:
        m["compression_ratio"] = round(m["turns_processed"] / m["beads_created"], 6)

    for k, v in record.items():
        if k.startswith("kpi_") and k not in m:
            m[k] = v

    events.append_metric(store.root, m)
    return m


def finalize_task_run_for_store(store: Any, result: str = "success", **extra: Any) -> dict[str, Any]:
    cur = current_run_metrics_for_store(store)
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
    return append_metric_for_store(store, rec)


__all__ = [
    "read_metrics_state_for_store",
    "write_metrics_state_for_store",
    "start_task_run_for_store",
    "increment_metric_counter_for_store",
    "current_run_metrics_for_store",
    "append_metric_for_store",
    "finalize_task_run_for_store",
]

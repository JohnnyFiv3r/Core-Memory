from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.soul.summary import build_soul_summary

TENSION_RESOLUTION_SCHEMA = "tension_resolution_meter.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return int(default)


def compute_tension_resolution_meter(
    root: str | Path,
    *,
    since: str | None = None,
    subject: str = "self",
    soul_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    window = str(since or os.getenv("CORE_MEMORY_QUALITY_METER_SINCE", "30d"))
    summary = soul_summary if isinstance(soul_summary, dict) else build_soul_summary(root, subject=subject)
    tensions = dict(summary.get("persistent_tensions") or {})
    rows = [dict(row) for row in (tensions.get("tensions") or []) if isinstance(row, dict)]
    status_counts = Counter(str(row.get("status") or "unknown").strip().lower() or "unknown" for row in rows)

    pending_count = int(status_counts.get("candidate", 0) + status_counts.get("pending", 0))
    accepted_count = int(status_counts.get("accepted", 0) + status_counts.get("active", 0))
    deferred_count = int(status_counts.get("deferred", 0))
    rejected_count = int(status_counts.get("rejected", 0))
    resolved_count = int(status_counts.get("resolved", 0))
    new_rate = float(tensions.get("new_tension_rate") or 0.0)
    resolution_rate = float(tensions.get("resolution_rate") or 0.0)
    accumulation_rate = round(new_rate - resolution_rate, 6)
    persistence_count = int(tensions.get("persistence_qualified_count") or 0)
    active_load = float(tensions.get("active_load") or persistence_count or accepted_count or 0.0)
    churn = tensions.get("churn")
    try:
        churn_value = None if churn is None else float(churn)
    except Exception:
        churn_value = None

    flags: list[str] = []
    if pending_count > _int_env("CORE_MEMORY_TENSION_STALE_PENDING_THRESHOLD", 10) and accumulation_rate > 0:
        flags.append("stale_accumulation")
    if accumulation_rate > _float_env("CORE_MEMORY_TENSION_HIGH_ACCUMULATION_THRESHOLD", 3.0):
        flags.append("high_accumulation")
    if new_rate > 0 and resolution_rate <= 0:
        flags.append("zero_resolution")

    status = "healthy"
    if "high_accumulation" in flags:
        status = "accumulating"
    elif "zero_resolution" in flags:
        status = "stalled"
    elif "stale_accumulation" in flags:
        status = "accumulating"

    return {
        "schema": TENSION_RESOLUTION_SCHEMA,
        "window": window,
        "generated_at": _now(),
        "status": status,
        "active_load": active_load,
        "pending_count": pending_count,
        "accepted_count": accepted_count,
        "deferred_count": deferred_count,
        "rejected_count": rejected_count,
        "resolved_count": resolved_count,
        "new_tension_rate": new_rate,
        "resolution_rate": resolution_rate,
        "accumulation_rate": accumulation_rate,
        "churn": churn_value,
        "new_tension_rate_per_30d": new_rate,
        "resolution_rate_per_30d": resolution_rate,
        "accumulation_rate_per_30d": accumulation_rate,
        "persistence_qualified_count": persistence_count,
        "flags": flags,
        "human_review_reminder": "Inferred contradictions always route to human review.",
        "tensions_by_status": dict(status_counts),
        "limitations": list(tensions.get("limitations") or []),
    }


__all__ = ["TENSION_RESOLUTION_SCHEMA", "compute_tension_resolution_meter"]

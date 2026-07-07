from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core_memory.soul.summary import build_soul_summary

TENSION_RESOLUTION_SCHEMA = "tension_resolution_meter.v1"
_CANDIDATE_PATH = Path(".beads") / "events" / "dreamer-candidates.json"

_PENDING_STATUSES = {"candidate", "pending"}
_ACCEPTED_STATUSES = {"accepted", "applied", "approved"}
_DEFERRED_STATUSES = {"deferred"}
_REJECTED_STATUSES = {"rejected"}


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


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def _since_cutoff(since: str) -> datetime | None:
    m = re.fullmatch(r"(\d+)\s*([dh])", str(since or "").strip().lower())
    if not m:
        return None
    hours = int(m.group(1)) * (24 if m.group(2) == "d" else 1)
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _load_tension_candidates(root: str | Path) -> list[dict[str, Any]]:
    """Load tension_candidate rows from the Dreamer candidate queue.

    The candidate queue is the contract source for lifecycle counts. The SOUL
    summary still owns rate fields such as new_tension_rate and resolution_rate.
    """
    path = Path(root) / _CANDIDATE_PATH
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [
        dict(row)
        for row in rows
        if isinstance(row, dict)
        and str(row.get("hypothesis_type") or "").strip().lower() == "tension_candidate"
    ]


def _row_ts(row: dict[str, Any]) -> datetime | None:
    for key in ("decided_at", "updated_at", "created_at", "first_seen_at"):
        dt = _parse_iso(row.get(key))
        if dt is not None:
            return dt
    return None


def _candidate_history_days(
    rows: list[dict[str, Any]],
    tension_rows: list[dict[str, Any]],
) -> float | None:
    earliest: datetime | None = None
    for row in rows:
        dt = _row_ts(row)
        if dt is not None and (earliest is None or dt < earliest):
            earliest = dt
    for row in tension_rows:
        dt = _parse_iso(row.get("first_seen_at"))
        if dt is not None and (earliest is None or dt < earliest):
            earliest = dt
    if earliest is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - earliest).total_seconds() / 86_400)


def compute_tension_resolution_meter(
    root: str | Path,
    *,
    since: str | None = None,
    subject: str = "self",
    soul_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    window = str(since or os.getenv("CORE_MEMORY_QUALITY_METER_SINCE", "30d"))
    cutoff = _since_cutoff(window)
    summary = soul_summary if isinstance(soul_summary, dict) else build_soul_summary(root, subject=subject)
    tensions = dict(summary.get("persistent_tensions") or {})
    tension_rows = [dict(row) for row in (tensions.get("tensions") or []) if isinstance(row, dict)]

    rows = _load_tension_candidates(root)
    status_counts: Counter[str] = Counter()
    pending_count = accepted_count = deferred_count = rejected_count = 0
    for row in rows:
        status = str(row.get("status") or "unknown").strip().lower() or "unknown"
        status_counts[status] += 1
        if status in _PENDING_STATUSES:
            pending_count += 1
            continue
        ts = _row_ts(row)
        if cutoff is not None and ts is not None and ts < cutoff:
            continue
        if status in _ACCEPTED_STATUSES:
            accepted_count += 1
        elif status in _DEFERRED_STATUSES:
            deferred_count += 1
        elif status in _REJECTED_STATUSES:
            rejected_count += 1

    new_rate = float(tensions.get("new_tension_rate") or 0.0)
    resolution_rate = float(tensions.get("resolution_rate") or 0.0)
    accumulation_rate = round(new_rate - resolution_rate, 6)
    persistence_count = int(tensions.get("persistence_qualified_count") or 0)
    resolved_count = int(status_counts.get("resolved", 0))
    active_load = float(tensions.get("active_load") or persistence_count or accepted_count or 0.0)
    churn = tensions.get("churn")
    try:
        churn_value = None if churn is None else float(churn)
    except Exception:
        churn_value = None

    limitations: list[str] = list(tensions.get("limitations") or [])
    if not rows:
        limitations.append("candidates_queue_unavailable")

    history_days = _candidate_history_days(rows, tension_rows)
    zero_resolution_min_days = _int_env("CORE_MEMORY_TENSION_ZERO_RESOLUTION_MIN_HISTORY_DAYS", 7)

    flags: list[str] = []
    if pending_count > _int_env("CORE_MEMORY_TENSION_STALE_PENDING_THRESHOLD", 10) and accumulation_rate > 0:
        flags.append("stale_accumulation")
    if accumulation_rate > _float_env("CORE_MEMORY_TENSION_HIGH_ACCUMULATION_THRESHOLD", 3.0):
        flags.append("high_accumulation")
    if (
        new_rate > 0
        and resolution_rate <= 0
        and history_days is not None
        and history_days >= zero_resolution_min_days
    ):
        flags.append("zero_resolution")

    status = "healthy"
    if "high_accumulation" in flags:
        status = "high_accumulation"
    elif "zero_resolution" in flags:
        status = "zero_resolution"
    elif "stale_accumulation" in flags:
        status = "stale_accumulation"

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
        "candidate_history_days": 0.0 if history_days is None else round(history_days, 6),
        "zero_resolution_min_history_days": zero_resolution_min_days,
        "flags": flags,
        "human_review_reminder": "Inferred contradictions always route to human review.",
        "tensions_by_status": dict(status_counts),
        "limitations": limitations,
    }


__all__ = ["TENSION_RESOLUTION_SCHEMA", "compute_tension_resolution_meter"]

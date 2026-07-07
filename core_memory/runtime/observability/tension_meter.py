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
    """Load ``tension_candidate`` rows from the Dreamer candidates queue.

    The queue — not ``build_soul_summary`` — is the contract source of truth for
    candidate lifecycle counts (§5.2 steps 2-3): pending vs terminal
    accepted/deferred/rejected. Returns [] when the queue is absent/unreadable.
    """
    p = Path(root) / ".beads" / "events" / "dreamer-candidates.json"
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
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
    tension_rows = [dict(r) for r in (tensions.get("tensions") or []) if isinstance(r, dict)]

    # --- Candidate lifecycle COUNTS from the queue (contract §5.2 steps 2-3) ---
    candidates = _load_tension_candidates(root)
    pending_count = accepted_count = deferred_count = rejected_count = 0
    for row in candidates:
        status = str(row.get("status") or "unknown").strip().lower() or "unknown"
        if status in _PENDING_STATUSES:
            pending_count += 1  # pending has no terminal timestamp; always counted
            continue
        # Terminal candidates are windowed by their decision time.
        ts = _row_ts(row)
        if cutoff is not None and ts is not None and ts < cutoff:
            continue
        if status in _ACCEPTED_STATUSES:
            accepted_count += 1
        elif status in _DEFERRED_STATUSES:
            deferred_count += 1
        elif status in _REJECTED_STATUSES:
            rejected_count += 1

    # --- tensions_by_status: SOUL-filed tension breakdown (candidate/active/resolved) ---
    tensions_by_status: Counter[str] = Counter(
        str(r.get("status") or "unknown").strip().lower() or "unknown" for r in tension_rows
    )

    limitations: list[str] = list(tensions.get("limitations") or [])
    if not candidates:
        limitations.append("candidates_queue_unavailable")

    # --- Engine-computed rates from persistent_tensions (unchanged inputs) ---
    new_rate = float(tensions.get("new_tension_rate") or 0.0)
    resolution_rate = float(tensions.get("resolution_rate") or 0.0)
    accumulation_rate = round(new_rate - resolution_rate, 6)
    persistence_count = int(tensions.get("persistence_qualified_count") or 0)
    resolved_count = int(tensions_by_status.get("resolved", 0))
    active_load = float(tensions.get("active_load") or persistence_count or 0.0)
    churn = tensions.get("churn")
    try:
        churn_value = None if churn is None else float(churn)
    except Exception:
        churn_value = None

    # History span for the zero_resolution guard: earliest first_seen among the
    # tensions (or terminal candidate decisions) → now.
    earliest: datetime | None = None
    for r in tension_rows:
        dt = _parse_iso(r.get("first_seen_at"))
        if dt is not None and (earliest is None or dt < earliest):
            earliest = dt
    for row in candidates:
        dt = _row_ts(row)
        if dt is not None and (earliest is None or dt < earliest):
            earliest = dt
    history_days = None if earliest is None else (datetime.now(timezone.utc) - earliest).total_seconds() / 86400.0
    min_history_days = float(_int_env("CORE_MEMORY_TENSION_ZERO_RESOLUTION_MIN_HISTORY_DAYS", 7))

    # --- Flags (contract §5.2 step 6) ---
    flags: list[str] = []
    if pending_count > _int_env("CORE_MEMORY_TENSION_STALE_PENDING_THRESHOLD", 10) and accumulation_rate > 0:
        flags.append("stale_accumulation")
    if accumulation_rate > _float_env("CORE_MEMORY_TENSION_HIGH_ACCUMULATION_THRESHOLD", 3.0):
        flags.append("high_accumulation")
    if (
        new_rate > 0
        and resolution_rate <= 0
        and history_days is not None
        and history_days >= min_history_days
    ):
        flags.append("zero_resolution")

    # --- Status = most severe flag, matching the contract enum ---
    if "high_accumulation" in flags:
        status = "high_accumulation"
    elif "zero_resolution" in flags:
        status = "zero_resolution"
    elif "stale_accumulation" in flags:
        status = "stale_accumulation"
    else:
        status = "healthy"

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
        "history_days": None if history_days is None else round(history_days, 3),
        "flags": flags,
        "human_review_reminder": "Inferred contradictions always route to human review (Decision #4).",
        "tensions_by_status": dict(tensions_by_status),
        "limitations": limitations,
    }


__all__ = ["TENSION_RESOLUTION_SCHEMA", "compute_tension_resolution_meter"]

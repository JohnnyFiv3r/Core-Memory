from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def normalize_as_of(value: Any) -> datetime | None:
    return parse_timestamp(value)


def _claim_start(claim: dict[str, Any]) -> datetime | None:
    for k in ("effective_from", "observed_at", "recorded_at", "created_at"):
        dt = parse_timestamp((claim or {}).get(k))
        if dt is not None:
            return dt
    return None


def _claim_end(claim: dict[str, Any]) -> datetime | None:
    return parse_timestamp((claim or {}).get("effective_to"))


def _update_time(update: dict[str, Any]) -> datetime | None:
    for k in ("effective_from", "observed_at", "recorded_at", "created_at"):
        dt = parse_timestamp((update or {}).get(k))
        if dt is not None:
            return dt
    return None


def claim_visible_as_of(claim: dict[str, Any], as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    start = _claim_start(claim)
    end = _claim_end(claim)
    if start is not None and as_of < start:
        return False
    # effective_to is exclusive
    if end is not None and as_of >= end:
        return False
    return True


def update_visible_as_of(update: dict[str, Any], as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    ts = _update_time(update)
    # No timestamp on update => treat as globally applicable for deterministic compatibility.
    if ts is None:
        return True
    return ts <= as_of


def claim_temporal_sort_key(claim: dict[str, Any]) -> tuple[datetime, str]:
    ts = _claim_start(claim) or parse_timestamp((claim or {}).get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
    cid = str((claim or {}).get("id") or "")
    return (ts, cid)

"""Durable turn semantic-write lifecycle state, history, and flush waivers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_memory.persistence.io_utils import append_jsonl, store_lock
from core_memory.schema.turn_receipt import SEMANTIC_STATUSES

SEMANTIC_WRITE_STATE_V1 = "memory.semantic_write_state.v1"
SEMANTIC_WRITE_STATUS_V1 = "memory.semantic_write_status.v1"
SEMANTIC_FLUSH_WAIVER_V1 = "memory.semantic_flush_waiver.v1"
PENDING_SEMANTIC_WARNING_SECONDS = 5 * 60
PENDING_SEMANTIC_CRITICAL_SECONDS = 60 * 60
_UNRESOLVED_STATUSES = frozenset({"pending", "repair_required"})


def semantic_write_key(session_id: str, turn_id: str) -> str:
    return f"{session_id}:{turn_id}"


def _now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(now: datetime | None = None) -> str:
    return _now(now).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _events_dir(root: str | Path) -> Path:
    return Path(root) / ".beads" / "events"


def _state_file(root: str | Path) -> Path:
    return _events_dir(root) / "semantic-write-state.json"


def _status_file(root: str | Path) -> Path:
    return _events_dir(root) / "semantic-write-status.jsonl"


def _waiver_file(root: str | Path) -> Path:
    return _events_dir(root) / "semantic-flush-waivers.jsonl"


def _read_state_unlocked(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": SEMANTIC_WRITE_STATE_V1, "records": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": SEMANTIC_WRITE_STATE_V1, "records": {}}
    if not isinstance(payload, dict):
        return {"schema": SEMANTIC_WRITE_STATE_V1, "records": {}}
    records = payload.get("records")
    if not isinstance(records, dict):
        records = {}
    return {"schema": SEMANTIC_WRITE_STATE_V1, "records": records}


def _write_state_unlocked(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def mark_semantic_write_state(
    root: str | Path,
    *,
    session_id: str,
    turn_id: str,
    status: str,
    event_id: str = "",
    bead_id: str = "",
    retryable: bool = False,
    error_code: str = "",
    derived_failures: list[dict[str, Any]] | None = None,
    association_status: str = "",
    queue_status: str = "",
    authorship: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    association_receipt: dict[str, Any] | None = None,
    queue_receipt: dict[str, Any] | None = None,
    waiver_id: str = "",
    reason: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update current semantic state and append an immutable status record."""

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in SEMANTIC_STATUSES:
        raise ValueError(f"invalid semantic write status: {status!r}")
    sid = str(session_id or "").strip()
    tid = str(turn_id or "").strip()
    if not sid or not tid:
        raise ValueError("session_id and turn_id are required")

    timestamp = _iso(now)
    state_path = _state_file(root)
    key = semantic_write_key(sid, tid)
    with store_lock(Path(root)):
        payload = _read_state_unlocked(state_path)
        records = dict(payload.get("records") or {})
        prior = dict(records.get(key) or {})
        prior_status = str(prior.get("status") or "")
        status_since = str(prior.get("status_since") or timestamp) if prior_status == normalized_status else timestamp
        pending_since = str(prior.get("pending_since") or "")
        if normalized_status in _UNRESOLVED_STATUSES and not pending_since:
            pending_since = timestamp
        if normalized_status not in _UNRESOLVED_STATUSES:
            pending_since = ""

        row = {
            "schema": SEMANTIC_WRITE_STATE_V1,
            "session_id": sid,
            "turn_id": tid,
            "status": normalized_status,
            "event_id": str(event_id or prior.get("event_id") or ""),
            "bead_id": str(bead_id or prior.get("bead_id") or ""),
            "retryable": bool(retryable),
            "error_code": str(error_code or ""),
            "derived_failures": list(derived_failures or []),
            "association_status": str(association_status or ""),
            "queue_status": str(queue_status or ""),
            "authorship": dict(authorship or prior.get("authorship") or {}),
            "validation": dict(validation or prior.get("validation") or {}),
            "associations": dict(association_receipt or prior.get("associations") or {}),
            "queue": dict(queue_receipt or prior.get("queue") or {}),
            "waiver_id": str(waiver_id or ""),
            "reason": str(reason or ""),
            "status_since": status_since,
            "pending_since": pending_since,
            "updated_at": timestamp,
        }
        records[key] = row
        _write_state_unlocked(state_path, {"schema": SEMANTIC_WRITE_STATE_V1, "records": records})
        append_jsonl(
            _status_file(root),
            {
                **row,
                "schema": SEMANTIC_WRITE_STATUS_V1,
                "previous_status": prior_status,
                "recorded_at": timestamp,
            },
        )
    return row


def get_semantic_write_state(root: str | Path, session_id: str, turn_id: str) -> dict[str, Any] | None:
    payload = _read_state_unlocked(_state_file(root))
    row = (payload.get("records") or {}).get(semantic_write_key(session_id, turn_id))
    return dict(row) if isinstance(row, dict) else None


def list_semantic_write_states(
    root: str | Path,
    *,
    statuses: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Return current semantic-write rows without exposing the state file.

    Maintenance and observability callers need the current row for every turn,
    while append-only lifecycle history remains an implementation detail of
    this module.  Results are ordered oldest-pending first so bounded repair
    batches naturally address the longest-lived gaps.
    """

    allowed = {str(value).strip().lower() for value in (statuses or set()) if str(value).strip()}
    payload = _read_state_unlocked(_state_file(root))
    rows = [dict(row) for row in (payload.get("records") or {}).values() if isinstance(row, dict)]
    if allowed:
        rows = [row for row in rows if str(row.get("status") or "").strip().lower() in allowed]
    rows.sort(
        key=lambda row: (
            str(row.get("pending_since") or row.get("status_since") or row.get("updated_at") or ""),
            str(row.get("session_id") or ""),
            str(row.get("turn_id") or ""),
        )
    )
    return rows


def semantic_write_health(root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Return pending semantic count and age metrics for doctor and hosts."""

    current = _now(now)
    payload = _read_state_unlocked(_state_file(root))
    records = [dict(row) for row in (payload.get("records") or {}).values() if isinstance(row, dict)]
    status_counts: dict[str, int] = {}
    pending: list[dict[str, Any]] = []
    for row in records:
        status = str(row.get("status") or "unknown")
        status_counts[status] = int(status_counts.get(status) or 0) + 1
        if status not in _UNRESOLVED_STATUSES:
            continue
        started = _parse_iso(row.get("pending_since") or row.get("status_since") or row.get("updated_at"))
        age = max(0.0, (current - started).total_seconds()) if started else 0.0
        pending.append(
            {
                "session_id": str(row.get("session_id") or ""),
                "turn_id": str(row.get("turn_id") or ""),
                "status": status,
                "event_id": str(row.get("event_id") or ""),
                "error_code": str(row.get("error_code") or ""),
                "pending_since": str(row.get("pending_since") or ""),
                "age_seconds": round(age, 3),
            }
        )
    pending.sort(key=lambda row: float(row.get("age_seconds") or 0), reverse=True)
    oldest = float((pending[0] if pending else {}).get("age_seconds") or 0)
    warning_count = sum(
        1 for row in pending if float(row.get("age_seconds") or 0) >= PENDING_SEMANTIC_WARNING_SECONDS
    )
    critical_count = sum(
        1 for row in pending if float(row.get("age_seconds") or 0) >= PENDING_SEMANTIC_CRITICAL_SECONDS
    )
    severity = "critical" if critical_count else ("warning" if warning_count else "ok")
    return {
        "schema": "memory.semantic_write_health.v1",
        "pending_count": len(pending),
        "oldest_pending_age_seconds": round(oldest, 3),
        "warning_count": warning_count,
        "critical_count": critical_count,
        "severity": severity,
        "warning_after_seconds": PENDING_SEMANTIC_WARNING_SECONDS,
        "critical_after_seconds": PENDING_SEMANTIC_CRITICAL_SECONDS,
        "status_counts": status_counts,
        "turns": pending,
    }


def latest_finalized_turn(root: str | Path, session_id: str) -> dict[str, Any] | None:
    """Return the newest persisted TURN_FINALIZED event for one session."""

    path = _events_dir(root) / "memory-events.jsonl"
    if not path.exists():
        return None
    latest: dict[str, Any] | None = None
    latest_ts = -1
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        envelope = row.get("envelope") or {}
        if str(envelope.get("session_id") or "") != str(session_id or ""):
            continue
        turn_id = str(envelope.get("turn_id") or "")
        if not turn_id:
            continue
        ts_ms = int(envelope.get("ts_ms") or (row.get("event") or {}).get("ts_ms") or 0)
        if ts_ms < latest_ts:
            continue
        latest_ts = ts_ms
        latest = {
            "session_id": str(session_id or ""),
            "turn_id": turn_id,
            "event_id": str((row.get("event") or {}).get("event_id") or ""),
            "envelope_hash": str(envelope.get("envelope_hash") or ""),
            "ts_ms": ts_ms,
        }
    return latest


def event_for_turn(root: str | Path, session_id: str, turn_id: str) -> dict[str, Any] | None:
    path = _events_dir(root) / "memory-events.jsonl"
    if not path.exists():
        return None
    latest: dict[str, Any] | None = None
    latest_ts = -1
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        envelope = row.get("envelope") or {}
        if str(envelope.get("session_id") or "") != str(session_id or ""):
            continue
        if str(envelope.get("turn_id") or "") != str(turn_id or ""):
            continue
        ts_ms = int(envelope.get("ts_ms") or (row.get("event") or {}).get("ts_ms") or 0)
        if ts_ms >= latest_ts:
            latest_ts = ts_ms
            latest = row
    return latest


def record_semantic_flush_waiver(
    root: str | Path,
    *,
    session_id: str,
    turn_id: str,
    operator: str,
    reason: str,
    event_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append an explicit operator waiver and mark the turn waived."""

    if not str(operator or "").strip() or not str(reason or "").strip():
        raise ValueError("operator and reason are required for a semantic flush waiver")
    row = {
        "schema": SEMANTIC_FLUSH_WAIVER_V1,
        "waiver_id": f"swv-{uuid.uuid4().hex[:12]}",
        "session_id": str(session_id or ""),
        "turn_id": str(turn_id or ""),
        "event_id": str(event_id or ""),
        "operator": str(operator).strip(),
        "reason": str(reason).strip(),
        "recorded_at": _iso(now),
    }
    with store_lock(Path(root)):
        append_jsonl(_waiver_file(root), row)
    prior = get_semantic_write_state(root, session_id, turn_id) or {}
    mark_semantic_write_state(
        root,
        session_id=session_id,
        turn_id=turn_id,
        status="waived",
        event_id=str(event_id or prior.get("event_id") or ""),
        bead_id=str(prior.get("bead_id") or ""),
        retryable=False,
        waiver_id=row["waiver_id"],
        reason="operator_flush_waiver",
        now=now,
    )
    return row


def get_semantic_flush_waiver(root: str | Path, session_id: str, turn_id: str) -> dict[str, Any] | None:
    path = _waiver_file(root)
    if not path.exists():
        return None
    latest: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("session_id") or "") == str(session_id or "") and str(row.get("turn_id") or "") == str(
            turn_id or ""
        ):
            latest = row
    return dict(latest) if isinstance(latest, dict) else None


__all__ = [
    "PENDING_SEMANTIC_CRITICAL_SECONDS",
    "PENDING_SEMANTIC_WARNING_SECONDS",
    "SEMANTIC_FLUSH_WAIVER_V1",
    "SEMANTIC_WRITE_STATE_V1",
    "SEMANTIC_WRITE_STATUS_V1",
    "event_for_turn",
    "get_semantic_flush_waiver",
    "get_semantic_write_state",
    "latest_finalized_turn",
    "list_semantic_write_states",
    "mark_semantic_write_state",
    "record_semantic_flush_waiver",
    "semantic_write_health",
    "semantic_write_key",
]

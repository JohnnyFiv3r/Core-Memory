"""Canonical event-state implementation.

Owns event envelope/data contracts, idempotency pass-state, and memory event
append surfaces for finalized-turn ingestion.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from ..persistence.io_utils import append_jsonl, store_lock
from ..integrations.openclaw_flags import transcript_archive_enabled
from .turn_archive import append_turn_record


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _parse_iso(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _compute_next_retry_at(retry_count: int) -> str:
    seconds = min(300, max(5, 5 * (2 ** max(0, int(retry_count) - 1))))
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _canon_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class TurnEnvelope:
    schema: str = "openclaw.memory.turn_envelope.v1"
    session_id: str = ""
    turn_id: str = ""
    transaction_id: str = ""
    trace_id: str = ""
    origin: str = "USER_TURN"
    ts: str = field(default_factory=_iso_now)
    ts_ms: int = field(default_factory=_ts_ms)
    user_query: str = ""
    assistant_final: Optional[str] = None
    assistant_final_ref: Optional[str] = None
    assistant_final_hash: str = ""
    envelope_hash: str = ""
    tools_trace: list[dict] = field(default_factory=list)
    mesh_trace: list[dict] = field(default_factory=list)
    window_turn_ids: list[str] = field(default_factory=list)
    window_bead_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def finalize_hashes(self, full_text_override: Optional[str] = None) -> None:
        final = full_text_override if full_text_override is not None else (self.assistant_final or "")
        self.assistant_final_hash = sha256_hex(final)
        envelope_basis = {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "user_query": self.user_query,
            "assistant_final_hash": self.assistant_final_hash,
            "tools_trace": self.tools_trace,
            "mesh_trace": self.mesh_trace,
            "metadata": self.metadata,
        }
        self.envelope_hash = sha256_hex(_canon_json(envelope_basis))

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d.get("assistant_final_hash") or not d.get("envelope_hash"):
            self.finalize_hashes()
            d = asdict(self)
        return d


@dataclass
class MemoryEvent:
    schema: str = "openclaw.memory.event.v1"
    event_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    transaction_id: str = ""
    trace_id: str = ""
    ts: str = field(default_factory=_iso_now)
    ts_ms: int = field(default_factory=_ts_ms)
    kind: str = "TURN_FINALIZED"
    envelope_ref: str = "inline"

    def to_dict(self) -> dict:
        return asdict(self)


def memory_pass_key(session_id: str, turn_id: str) -> str:
    return f"{session_id}:{turn_id}"


def _state_file(root: Path) -> Path:
    return root / ".beads" / "events" / "memory-pass-state.json"


def _events_file(root: Path) -> Path:
    return root / ".beads" / "events" / "memory-events.jsonl"


def _status_log_file(root: Path) -> Path:
    return root / ".beads" / "events" / "memory-pass-status.jsonl"


def _read_state_unlocked(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_state_unlocked(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_file)


def _append_status_unlocked(
    root: Path,
    *,
    session_id: str,
    turn_id: str,
    status: str,
    envelope_hash: str = "",
    retry_count: int = 0,
    reason: str = "",
    error: str = "",
    next_retry_at: str = "",
    supersedes_envelope_hash: str = "",
) -> None:
    append_jsonl(
        _status_log_file(root),
        {
            "ts": _iso_now(),
            "session_id": session_id,
            "turn_id": turn_id,
            "status": status,
            "envelope_hash": envelope_hash,
            "retry_count": retry_count,
            "reason": reason,
            "error": error,
            "next_retry_at": next_retry_at,
            "supersedes_envelope_hash": supersedes_envelope_hash,
        },
    )


def mark_memory_pass(
    root: Path,
    session_id: str,
    turn_id: str,
    status: str,
    envelope_hash: str = "",
    *,
    reason: str = "",
    error: str = "",
    next_retry_at: str = "",
    supersedes_envelope_hash: str = "",
) -> None:
    state_file = _state_file(root)
    with store_lock(root):
        state = _read_state_unlocked(state_file)
        key = memory_pass_key(session_id, turn_id)
        prior = state.get(key) or {}
        retry_count = int(prior.get("retry_count", 0) or 0)
        if status == "failed":
            retry_count += 1
            if not next_retry_at:
                next_retry_at = _compute_next_retry_at(retry_count)
        elif status in {"running", "done", "pending"}:
            next_retry_at = ""

        state[key] = {
            "status": status,
            "envelope_hash": envelope_hash,
            "updated_at": _iso_now(),
            "retry_count": retry_count,
            "error": error,
            "next_retry_at": next_retry_at,
            "supersedes_envelope_hash": supersedes_envelope_hash,
        }
        _write_state_unlocked(state_file, state)
        _append_status_unlocked(
            root,
            session_id=session_id,
            turn_id=turn_id,
            status=status,
            envelope_hash=envelope_hash,
            retry_count=retry_count,
            reason=reason,
            error=error,
            next_retry_at=next_retry_at,
            supersedes_envelope_hash=supersedes_envelope_hash,
        )


def try_claim_memory_pass(root: Path, session_id: str, turn_id: str) -> tuple[bool, Optional[dict]]:
    state_file = _state_file(root)
    with store_lock(root):
        state = _read_state_unlocked(state_file)
        key = memory_pass_key(session_id, turn_id)
        prior = state.get(key)
        if not prior:
            return False, None
        st = str(prior.get("status", ""))
        if st not in {"pending", "failed"}:
            return False, prior

        if st == "failed":
            due = _parse_iso(prior.get("next_retry_at"))
            if due and datetime.now(timezone.utc) < due:
                return False, prior

        claimed = {**prior, "status": "running", "updated_at": _iso_now()}
        state[key] = claimed
        _write_state_unlocked(state_file, state)
        _append_status_unlocked(
            root,
            session_id=session_id,
            turn_id=turn_id,
            status="running",
            envelope_hash=str(claimed.get("envelope_hash", "")),
            retry_count=int(claimed.get("retry_count", 0) or 0),
            reason="claim",
            supersedes_envelope_hash=str(claimed.get("supersedes_envelope_hash", "") or ""),
        )
        return True, claimed


def get_memory_pass(root: Path, session_id: str, turn_id: str) -> Optional[dict]:
    state_file = _state_file(root)
    if not state_file.exists():
        return None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return state.get(memory_pass_key(session_id, turn_id))


def emit_memory_event(root: Path, envelope: TurnEnvelope) -> MemoryEvent:
    envelope.finalize_hashes()
    event = MemoryEvent(
        event_id=f"mev-{sha256_hex(envelope.session_id + envelope.turn_id + str(envelope.ts_ms))[:12]}",
        session_id=envelope.session_id,
        turn_id=envelope.turn_id,
        transaction_id=envelope.transaction_id,
        trace_id=envelope.trace_id,
        envelope_ref="inline",
    )

    payload = {
        "event": event.to_dict(),
        "envelope": envelope.to_dict(),
    }

    with store_lock(root):
        if transcript_archive_enabled():
            append_turn_record(
                root=root,
                session_id=envelope.session_id,
                turn_id=envelope.turn_id,
                transaction_id=envelope.transaction_id,
                trace_id=envelope.trace_id,
                origin=envelope.origin,
                ts=envelope.ts,
                user_query=envelope.user_query,
                assistant_final=envelope.assistant_final,
                assistant_final_ref=envelope.assistant_final_ref,
                assistant_final_hash=envelope.assistant_final_hash,
                tools_trace=envelope.tools_trace,
                mesh_trace=envelope.mesh_trace,
                metadata=envelope.metadata,
            )
        append_jsonl(_events_file(root), payload)

    return event


__all__ = [
    "TurnEnvelope",
    "MemoryEvent",
    "memory_pass_key",
    "sha256_hex",
    "emit_memory_event",
    "mark_memory_pass",
    "get_memory_pass",
    "try_claim_memory_pass",
]

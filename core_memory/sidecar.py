"""Memory sidecar contracts/utilities for coordinator integration.

This module is coordinator-agnostic and provides deterministic envelope/event
shapes plus idempotency helpers for one-memory-pass-per-turn workflows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .io_utils import append_jsonl, store_lock


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


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

    def finalize_hashes(self) -> None:
        final = self.assistant_final or ""
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


def mark_memory_pass(root: Path, session_id: str, turn_id: str, status: str, envelope_hash: str = "") -> None:
    """Persist pass status atomically under store lock."""
    state_file = _state_file(root)
    with store_lock(root):
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
        else:
            state = {}
        key = memory_pass_key(session_id, turn_id)
        state[key] = {
            "status": status,
            "envelope_hash": envelope_hash,
            "updated_at": _iso_now(),
        }
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(state_file)


def get_memory_pass(root: Path, session_id: str, turn_id: str) -> Optional[dict]:
    state_file = _state_file(root)
    if not state_file.exists():
        return None
    state = json.loads(state_file.read_text(encoding="utf-8"))
    return state.get(memory_pass_key(session_id, turn_id))


def emit_memory_event(root: Path, envelope: TurnEnvelope) -> MemoryEvent:
    """Emit one finalized memory event for a top-level turn."""
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
        append_jsonl(_events_file(root), payload)

    return event

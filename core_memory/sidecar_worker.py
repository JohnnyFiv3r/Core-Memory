"""DEPRECATED transitional compatibility module.

Canonical replacement: `core_memory.event_worker`.

This worker is mechanical/bookkeeping-only and retained as implementation
backing during migration. New runtime-facing code should import
`event_worker` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sidecar import mark_memory_pass
from .store import MemoryStore


@dataclass
class SidecarPolicy:
    create_threshold: float = 0.75
    promote_threshold: float = 0.85
    max_create_per_turn: int = 1
    max_promote_per_turn: int = 1


def process_memory_event(root: str, payload: dict[str, Any], policy: SidecarPolicy | None = None) -> dict[str, Any]:
    """Process one memory event payload and return a MemoryDelta-like result.

    Mechanical responsibilities only:
    - normalize envelope/session/turn values
    - mark pass completion
    - emit lightweight metrics

    Non-responsibilities (moved to crawler-reviewed authority path):
    - semantic bead creation judgment
    - promotion judgment
    - association judgment
    """
    _ = policy or SidecarPolicy()
    envelope = payload.get("envelope") or {}

    session_id = envelope.get("session_id", "main")
    turn_id = envelope.get("turn_id", "unknown")

    created: list[dict[str, Any]] = []
    creation_candidates: list[dict[str, Any]] = []
    promoted: list[dict[str, Any]] = []
    promotion_candidates: list[dict[str, Any]] = []
    suppressed = [{"candidate": "semantic_judgment", "reason": "worker_mechanical_only"}]
    gates = {
        "score_threshold": False,
        "explicit_emphasis": False,
        "confirmed_outcome": False,
        "repeated_pattern": False,
        "authoritative": False,
    }

    delta = {
        "session_id": session_id,
        "turn_id": turn_id,
        "created": created,
        "creation_candidates": creation_candidates,
        "promoted": promoted,
        "promotion_candidates": promotion_candidates,
        "promotion_gates": gates,
        "suppressed": suppressed,
        "metrics": {
            "runtime_ms": 0,
            "created_count": 0,
            "creation_candidate_count": 0,
            "promoted_count": 0,
            "candidates_evaluated": 0,
            "candidates_auto_archived": 0,
            "mode": "mechanical_only",
        },
    }

    env_hash = envelope.get("envelope_hash", "")
    supersedes = (envelope.get("metadata") or {}).get("supersedes_envelope_hash", "")
    mark_memory_pass(
        Path(root),
        session_id,
        turn_id,
        "done",
        env_hash,
        supersedes_envelope_hash=str(supersedes or ""),
    )

    store = MemoryStore(root=root)
    store.append_metric(
        {
            "run_id": f"event-worker-{session_id}-{turn_id}",
            "mode": "core_memory",
            "task_id": "memory_pass",
            "result": "success",
            "steps": 1,
            "tool_calls": 0,
            "beads_created": 0,
            "beads_recalled": 0,
            "repeat_failure": False,
            "decision_conflicts": 0,
            "unjustified_flips": 0,
            "rationale_recall_score": 0,
            "turns_processed": 1,
            "compression_ratio": 0.0,
            "phase": "event_worker",
        }
    )

    return delta

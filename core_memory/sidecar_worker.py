"""Memory sidecar worker (Ticket 3 skeleton).

Processes TURN_FINALIZED memory events with strict per-turn budgets.
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


def _score_signal(user_query: str, assistant_final: str) -> float:
    text = f"{user_query} {assistant_final}".lower()
    score = 0.35
    strong = ["remember", "always", "never forget", "important", "decision", "lesson", "root cause"]
    for kw in strong:
        if kw in text:
            score += 0.15
    return min(score, 1.0)


def _bead_type_for_text(user_query: str, assistant_final: str) -> str:
    t = f"{user_query} {assistant_final}".lower()
    if "decision" in t or "decide" in t:
        return "decision"
    if "lesson" in t or "learn" in t:
        return "lesson"
    if "evidence" in t or "source" in t:
        return "evidence"
    return "context"


def process_memory_event(root: str, payload: dict[str, Any], policy: SidecarPolicy | None = None) -> dict[str, Any]:
    """Process one memory event payload and return a MemoryDelta-like result."""
    policy = policy or SidecarPolicy()
    envelope = payload.get("envelope") or {}

    session_id = envelope.get("session_id", "main")
    turn_id = envelope.get("turn_id", "unknown")
    user_query = envelope.get("user_query", "")
    assistant_final = envelope.get("assistant_final") or ""
    window_bead_ids = envelope.get("window_bead_ids") or []

    store = MemoryStore(root=root)
    score = _score_signal(user_query, assistant_final)

    created = []
    promoted = []
    suppressed = []

    # Create budgeted bead (0..1)
    if score >= policy.create_threshold and policy.max_create_per_turn > 0:
        bead_type = _bead_type_for_text(user_query, assistant_final)
        title = (assistant_final.strip().splitlines()[0] if assistant_final.strip() else user_query.strip())[:120] or "Turn memory"
        kwargs = {
            "type": bead_type,
            "title": title,
            "summary": [assistant_final[:220]] if assistant_final else [user_query[:220]],
            "because": ["captured from finalized turn sidecar pass"] if bead_type in {"decision", "lesson"} else [],
            "source_turn_ids": [turn_id],
            "session_id": session_id,
            "tags": ["sidecar", "turn-finalized"],
        }
        if bead_type == "evidence" and not kwargs["summary"]:
            kwargs["summary"] = ["evidence from finalized turn"]
        bead_id = store.add_bead(**kwargs)
        created.append({"bead_id": bead_id, "type": bead_type, "score": score, "reason": "threshold_met"})
    else:
        suppressed.append({"candidate": "create_bead", "reason": "below_threshold_or_budget"})

    # Promotion budgeted pass (0..1)
    if score >= policy.promote_threshold and policy.max_promote_per_turn > 0:
        # deterministic: first available window bead that is open
        idx = store._read_json(store.beads_dir / "index.json")
        for bid in window_bead_ids:
            bead = (idx.get("beads") or {}).get(bid)
            if not bead:
                continue
            if bead.get("status") == "open":
                if store.promote(bid):
                    promoted.append({"bead_id": bid, "score": score, "reason": "promotion_gate"})
                break

    delta = {
        "session_id": session_id,
        "turn_id": turn_id,
        "created": created,
        "promoted": promoted,
        "promotion_candidates": [],
        "suppressed": suppressed,
        "metrics": {
            "runtime_ms": 0,
            "created_count": len(created),
            "promoted_count": len(promoted),
        },
    }

    # mark pass done + metrics row
    env_hash = envelope.get("envelope_hash", "")
    mark_memory_pass(Path(root), session_id, turn_id, "done", env_hash)
    store.append_metric({
        "run_id": f"sidecar-{session_id}-{turn_id}",
        "mode": "core_memory",
        "task_id": "memory_pass",
        "result": "success",
        "steps": 1,
        "tool_calls": 0,
        "beads_created": len(created),
        "beads_recalled": 0,
        "repeat_failure": False,
        "decision_conflicts": 0,
        "unjustified_flips": 0,
        "rationale_recall_score": 0,
        "turns_processed": 1,
        "compression_ratio": (1.0 / len(created)) if created else 0.0,
        "phase": "sidecar",
    })

    return delta

"""Memory sidecar worker (Ticket 3 skeleton).

Processes TURN_FINALIZED memory events with strict per-turn budgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

from .sidecar import mark_memory_pass
from .store import MemoryStore
from .io_utils import append_jsonl


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
    if any(k in t for k in ["resolved", "fixed", "failed", "completed", "confirmed", "outcome"]):
        return "outcome"
    if "decision" in t or "decide" in t:
        return "decision"
    if "lesson" in t or "learn" in t:
        return "lesson"
    if "evidence" in t or "source" in t:
        return "evidence"
    return "context"


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_\-]+", (text or "").lower()) if len(t) >= 4}


def _canonical_tags_from_text(user_query: str, assistant_final: str, max_tags: int = 8) -> list[str]:
    text = f"{user_query} {assistant_final}".lower()
    tags: list[str] = []

    def add(tag: str) -> None:
        if tag not in tags and len(tags) < max_tags:
            tags.append(tag)

    if "openclaw" in text:
        add("openclaw")
    if "pydanticai" in text:
        add("pydanticai")
    if "springai" in text:
        add("springai")
    if "orchestrator" in text or "framework" in text:
        add("orchestrator")
    if "multi orchestrator" in text or "multiple orchestrator" in text or "cross-framework" in text:
        add("multi-orchestrator")
    if "emit_turn_finalized" in text or "integration port" in text:
        add("emit-turn-finalized")
    if "adapter" in text:
        add("adapter")
    if "migrat" in text or "switch" in text or "transition" in text:
        add("migration")
    if "decision" in text:
        add("decision")
    if "lesson" in text:
        add("lesson")

    return tags


def _infer_outcome_result(user_query: str, assistant_final: str) -> str:
    t = f"{user_query} {assistant_final}".lower()
    if any(k in t for k in ["resolved", "fixed", "completed", "confirmed", "working"]):
        return "resolved"
    if any(k in t for k in ["failed", "broken", "regression", "error persists"]):
        return "failed"
    if any(k in t for k in ["partial", "partially", "some improvement"]):
        return "partial"
    return "confirmed"


def _promotion_gates(envelope: dict[str, Any], score: float) -> dict[str, bool]:
    user_query = envelope.get("user_query") or ""
    assistant_final = envelope.get("assistant_final") or ""
    text = f"{user_query} {assistant_final}".lower()

    explicit_emphasis = any(k in text for k in ["important", "must", "never", "always", "decision", "lesson"]) 
    confirmed_outcome = any(k in text for k in ["resolved", "fixed", "completed", "confirmed", "working"]) 

    # Cheap repetition proxy: if rolling window turn context exists and we have recurring tokens
    # in this turn, consider it a repeated pattern signal.
    rep_tokens = _tokenize(text)
    repeated_pattern = bool((envelope.get("window_turn_ids") or [])) and len(rep_tokens) >= 4

    return {
        "score_threshold": score >= 0.85,
        "explicit_emphasis": explicit_emphasis,
        "confirmed_outcome": confirmed_outcome,
        "repeated_pattern": repeated_pattern,
    }


def _enqueue_promotion_candidates(root: str, rows: list[dict[str, Any]]) -> None:
    q = Path(root) / ".beads" / "events" / "promotion-candidates.jsonl"
    for r in rows:
        append_jsonl(q, r)


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
    promotion_candidates = []
    suppressed = []

    # Create budgeted bead (0..1)
    if score >= policy.create_threshold and policy.max_create_per_turn > 0:
        bead_type = _bead_type_for_text(user_query, assistant_final)
        title = (assistant_final.strip().splitlines()[0] if assistant_final.strip() else user_query.strip())[:120] or "Turn memory"
        canonical_tags = _canonical_tags_from_text(user_query, assistant_final, max_tags=8)
        kwargs = {
            "type": bead_type,
            "title": title,
            "summary": [assistant_final[:220]] if assistant_final else [user_query[:220]],
            "because": ["captured from finalized turn sidecar pass"] if bead_type in {"decision", "lesson"} else [],
            "source_turn_ids": [turn_id],
            "session_id": session_id,
            "tags": (["sidecar", "turn-finalized"] + canonical_tags)[:10],
            "detail": (assistant_final or user_query)[:900],
        }
        if bead_type == "outcome":
            kwargs["result"] = _infer_outcome_result(user_query, assistant_final)
            if window_bead_ids:
                kwargs["linked_bead_id"] = str(window_bead_ids[0])
        if bead_type in {"decision", "lesson", "outcome", "precedent", "design_principle", "failed_hypothesis", "evidence"} and score >= policy.promote_threshold:
            kwargs["status"] = "candidate"
        if bead_type == "evidence":
            if not kwargs["summary"]:
                kwargs["summary"] = ["evidence from finalized turn"]
            if window_bead_ids:
                kwargs["supports_bead_ids"] = [str(window_bead_ids[0])]
        bead_id = store.add_bead(**kwargs)
        created.append({"bead_id": bead_id, "type": bead_type, "score": score, "reason": "threshold_met"})
    else:
        suppressed.append({"candidate": "create_bead", "reason": "below_threshold_or_budget"})

    gates = _promotion_gates(envelope, score)

    # Promotion budgeted pass (0..1)
    gate_pass = gates.get("score_threshold", False) and (
        gates.get("explicit_emphasis", False)
        or gates.get("confirmed_outcome", False)
        or gates.get("repeated_pattern", False)
    )
    if gate_pass and policy.max_promote_per_turn > 0:
        # deterministic: open beads in provided window order
        idx = store._read_json(store.beads_dir / "index.json")
        open_window = []
        for bid in window_bead_ids:
            bead = (idx.get("beads") or {}).get(bid)
            if not bead:
                continue
            if bead.get("status") == "open":
                open_window.append(bid)

        for bid in open_window[: policy.max_promote_per_turn]:
            try:
                if store.promote(bid):
                    promoted.append({"bead_id": bid, "score": score, "reason": "promotion_gate"})
            except Exception:
                promotion_candidates.append(
                    {
                        "bead_id": bid,
                        "score": score,
                        "reason": "promote_error_deferred",
                    }
                )

        # If more candidates existed than budget, defer remainder.
        deferred_budget = open_window[policy.max_promote_per_turn :]
        for bid in deferred_budget:
            promotion_candidates.append(
                {
                    "bead_id": bid,
                    "score": score,
                    "reason": "budget_exhausted",
                }
            )

        if promotion_candidates:
            _enqueue_promotion_candidates(
                root,
                [
                    {
                        "ts": envelope.get("ts"),
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "candidate": c,
                        "gates": gates,
                    }
                    for c in promotion_candidates
                ],
            )

    delta = {
        "session_id": session_id,
        "turn_id": turn_id,
        "created": created,
        "promoted": promoted,
        "promotion_candidates": promotion_candidates,
        "promotion_gates": gates,
        "suppressed": suppressed,
        "metrics": {
            "runtime_ms": 0,
            "created_count": len(created),
            "promoted_count": len(promoted),
        },
    }

    # mark pass done + metrics row
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

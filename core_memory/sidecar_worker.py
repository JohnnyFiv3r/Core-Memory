"""Memory sidecar worker (legacy compatibility worker).

Processes TURN_FINALIZED memory events for compatibility execution.
V2P13: deterministic promotion/association judgment here is non-authoritative.
Canonical promotion/association authority lives in crawler-reviewed paths.
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
    creation_candidates = []
    promoted = []
    promotion_candidates = []
    suppressed = []

    # V2P14 Step 2: semantic bead creation in worker is non-authoritative.
    # Worker emits preview candidates for agent/crawler review; it does not create
    # canonical beads directly.
    if score >= policy.create_threshold and policy.max_create_per_turn > 0:
        bead_type = _bead_type_for_text(user_query, assistant_final)
        title = (assistant_final.strip().splitlines()[0] if assistant_final.strip() else user_query.strip())[:120] or "Turn memory"
        canonical_tags = _canonical_tags_from_text(user_query, assistant_final, max_tags=8)
        candidate = {
            "session_id": session_id,
            "turn_id": turn_id,
            "type": bead_type,
            "title": title,
            "summary": [assistant_final[:220]] if assistant_final else [user_query[:220]],
            "tags": (["event_worker", "turn-finalized"] + canonical_tags)[:10],
            "score": score,
            "reason": "threshold_met_preview_only",
            "authoritative": False,
        }
        if bead_type == "outcome":
            candidate["result"] = _infer_outcome_result(user_query, assistant_final)
            if window_bead_ids:
                candidate["linked_bead_id"] = str(window_bead_ids[0])
        creation_candidates.append(candidate)
    else:
        suppressed.append({"candidate": "create_bead", "reason": "below_threshold_or_budget"})

    gates = _promotion_gates(envelope, score)

    # V2P13 Step 2: deterministic promotion in worker is non-authoritative.
    # Worker may emit preview/deferred candidates for agent/crawler review,
    # but it does not mutate canonical promotion state.
    gate_pass = gates.get("score_threshold", False) and (
        gates.get("explicit_emphasis", False)
        or gates.get("confirmed_outcome", False)
        or gates.get("repeated_pattern", False)
    )
    if gate_pass and policy.max_promote_per_turn > 0:
        idx = store._read_json(store.beads_dir / "index.json")
        open_window = []
        for bid in window_bead_ids:
            bead = (idx.get("beads") or {}).get(bid)
            if not bead:
                continue
            if bead.get("status") == "open":
                open_window.append(bid)

        for bid in open_window[: policy.max_promote_per_turn]:
            promotion_candidates.append(
                {
                    "bead_id": bid,
                    "score": score,
                    "reason": "non_authoritative_preview_gate",
                    "authoritative": False,
                }
            )

        deferred_budget = open_window[policy.max_promote_per_turn :]
        for bid in deferred_budget:
            promotion_candidates.append(
                {
                    "bead_id": bid,
                    "score": score,
                    "reason": "budget_exhausted_preview",
                    "authoritative": False,
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
                        "authoritative": False,
                    }
                    for c in promotion_candidates
                ],
            )

    candidate_eval = {
        "ok": True,
        "evaluated": 0,
        "auto_archived": 0,
        "mode": "disabled_non_authoritative",
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
            "created_count": len(created),
            "creation_candidate_count": len(creation_candidates),
            "promoted_count": len(promoted),
            "candidates_evaluated": int(candidate_eval.get("evaluated", 0) if isinstance(candidate_eval, dict) else 0),
            "candidates_auto_archived": int(candidate_eval.get("auto_archived", 0) if isinstance(candidate_eval, dict) else 0),
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

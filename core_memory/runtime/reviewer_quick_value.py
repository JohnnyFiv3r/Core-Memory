from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core_memory import memory_execute, process_flush, process_turn_finalized
from core_memory.runtime.dreamer_candidates import decide_dreamer_candidate, enqueue_dreamer_candidates, list_dreamer_candidates


def _probe_retrieval(root: str | Path, query: str) -> dict[str, Any]:
    out = memory_execute(
        request={"raw_query": str(query), "intent": "remember", "k": 25},
        root=str(root),
        explain=True,
    )
    rows = [r for r in (out.get("results") or []) if isinstance(r, dict) and str(r.get("type") or "") != "session_start"]
    return {
        "result_count": len(rows),
        "ok": bool(out.get("ok")),
        "confidence": str(out.get("confidence") or ""),
        "next_action": str(out.get("next_action") or ""),
        "raw": out,
    }


def _find_bead_id_by_turn_and_title(root: Path, *, turn_id: str, title: str = "") -> str:
    idx = root / ".beads" / "index.json"
    if not idx.exists():
        return ""
    try:
        payload = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        return ""
    beads = (payload.get("beads") or {}) if isinstance(payload, dict) else {}
    candidates: list[dict[str, Any]] = []
    for _bid, bead in beads.items():
        if not isinstance(bead, dict):
            continue
        turns = [str(x) for x in (bead.get("source_turn_ids") or [])]
        if str(turn_id) not in turns:
            continue
        if title and str(bead.get("title") or "") != str(title):
            continue
        candidates.append(bead)

    if not candidates:
        return ""

    preferred = [
        b
        for b in candidates
        if str((b.get("type") or "")).lower() not in {"process_flush", "session_start", "session_end"}
    ]
    pool = preferred or candidates
    pool = sorted(pool, key=lambda b: str((b or {}).get("created_at") or ""), reverse=True)
    return str((pool[0] or {}).get("id") or "")


def reviewer_quick_value_v2(root: str | Path) -> dict[str, Any]:
    os.environ.setdefault("CORE_MEMORY_CANONICAL_SEMANTIC_MODE", "degraded_allowed")

    root_p = Path(root)
    root_p.mkdir(parents=True, exist_ok=True)

    # Step 1: one canonical write
    canonical_turn = process_turn_finalized(
        root=str(root_p),
        session_id="reviewer-eval-v2",
        turn_id="rv2-t1",
        user_query="rv2-canonical-write-token payments deployment policy",
        assistant_final="Outcome: full rollout increased risk. Lesson: use canary-first rollout for payments.",
    )

    # Step 2: one retrieval (before/after tokenized probe)
    retrieval_query = "rv2-retrieval-token payments retrieval"
    retrieval_before = _probe_retrieval(root_p, retrieval_query)
    process_turn_finalized(
        root=str(root_p),
        session_id="reviewer-eval-v2",
        turn_id="rv2-t2",
        user_query=retrieval_query,
        assistant_final="Reinforcement: payments deployment policy remains retrievable after canonical write.",
    )
    retrieval_after = _probe_retrieval(root_p, retrieval_query)
    retrieval_improved = int(retrieval_after.get("result_count") or 0) > int(retrieval_before.get("result_count") or 0)

    # Step 3: repeated-incident improvement (tokenized two-pass)
    repeat_query = "rv2-repeated-incident-token checkout incident"
    repeat_before = _probe_retrieval(root_p, repeat_query)
    process_turn_finalized(
        root=str(root_p),
        session_id="reviewer-eval-v2",
        turn_id="rv2-t3",
        user_query=repeat_query,
        assistant_final="Incident baseline: checkout outage repeated after full rollout.",
    )
    repeat_mid = _probe_retrieval(root_p, repeat_query)
    process_turn_finalized(
        root=str(root_p),
        session_id="reviewer-eval-v2",
        turn_id="rv2-t4",
        user_query=repeat_query,
        assistant_final="Improvement lesson: repeated incident now governed by canary-first fallback.",
    )
    repeat_after = _probe_retrieval(root_p, repeat_query)
    repeated_incident_improved = int(repeat_after.get("result_count") or 0) > int(repeat_mid.get("result_count") or 0)

    # Step 4: Dreamer-assisted transfer improvement
    transfer_query = "rv2-dreamer-transfer-token billing migration"
    transfer_before = _probe_retrieval(root_p, transfer_query)

    process_turn_finalized(
        root=str(root_p),
        session_id="s-checkout",
        turn_id="rv2-src",
        user_query="seed source lesson for dreamer transfer quick-value path",
        assistant_final="Checkout transfer lesson captured via canonical turn path.",
        metadata={
            "crawler_updates": {
                "creations": [
                    {
                        "type": "lesson",
                        "title": "Checkout transfer lesson",
                        "summary": ["checkout incidents improved after canary-first fallback"],
                        "source_turn_ids": ["rv2-src"],
                        "tags": ["checkout", "canary"],
                    }
                ]
            }
        },
    )
    process_flush(
        root=str(root_p),
        session_id="s-checkout",
        source="reviewer_quick_value_v2",
        promote=True,
        token_budget=1200,
        max_beads=12,
    )
    process_turn_finalized(
        root=str(root_p),
        session_id="s-billing",
        turn_id="rv2-tgt",
        user_query="seed target outcome for dreamer transfer quick-value path",
        assistant_final="Billing migration risk captured via canonical turn path.",
        metadata={
            "crawler_updates": {
                "creations": [
                    {
                        "type": "outcome",
                        "title": "Billing migration risk",
                        "summary": ["billing migration still exhibits rollout risk"],
                        "result": "partial",
                        "linked_bead_id": "rv2-logical-link",
                        "source_turn_ids": ["rv2-tgt"],
                        "tags": ["billing", "migration"],
                    }
                ]
            }
        },
    )
    process_flush(
        root=str(root_p),
        session_id="s-billing",
        source="reviewer_quick_value_v2",
        promote=True,
        token_budget=1200,
        max_beads=12,
    )

    src = _find_bead_id_by_turn_and_title(root_p, turn_id="rv2-src", title="Checkout transfer lesson")
    if not src:
        src = _find_bead_id_by_turn_and_title(root_p, turn_id="rv2-src")
    tgt = _find_bead_id_by_turn_and_title(root_p, turn_id="rv2-tgt", title="Billing migration risk")
    if not tgt:
        tgt = _find_bead_id_by_turn_and_title(root_p, turn_id="rv2-tgt")

    queue_out = {"ok": False, "added": 0}
    if src and tgt:
        queue_out = enqueue_dreamer_candidates(
            root=root_p,
            associations=[
                {
                    "source": src,
                    "target": tgt,
                    "relationship": "transferable_lesson",
                    "novelty": 0.8,
                    "grounding": 0.9,
                    "confidence": 0.8,
                    "rationale": "billing migration shares rollout-risk structure with checkout incidents",
                    "expected_decision_impact": "transfer canary-first fallback policy",
                }
            ],
            run_metadata={"run_id": "reviewer-qv2", "mode": "suggest", "source": "reviewer_quick_value_v2"},
        )

    pending = list_dreamer_candidates(root=root_p, status="pending", limit=20).get("results") or []
    candidate_id = ""
    for c in pending:
        if str(c.get("source_bead_id") or "") == src and str(c.get("target_bead_id") or "") == tgt:
            candidate_id = str(c.get("id") or "")
            break

    decision_out: dict[str, Any] = {"ok": False, "status": "pending"}
    if candidate_id:
        decision_out = decide_dreamer_candidate(
            root=root_p,
            candidate_id=candidate_id,
            decision="accept",
            reviewer="reviewer-quick-value-v2",
            notes="accepted in quick-value walkthrough",
            apply=True,
        )

    accepted = bool(decision_out.get("ok")) and str(decision_out.get("status") or "") == "accepted"
    if accepted:
        process_turn_finalized(
            root=str(root_p),
            session_id="reviewer-eval-v2",
            turn_id="rv2-t5",
            user_query=transfer_query,
            assistant_final="Accepted Dreamer transfer candidate: apply canary-first fallback policy to billing migration.",
        )

    transfer_after = _probe_retrieval(root_p, transfer_query)
    dreamer_transfer_improved = accepted and int(transfer_after.get("result_count") or 0) > int(transfer_before.get("result_count") or 0)

    payload = {
        "schema": "core_memory.reviewer_quick_value_v2.v1",
        "root": str(root_p),
        "steps": {
            "canonical_write": {
                "ok": bool(canonical_turn.get("ok")),
                "turn_id": "rv2-t1",
                "authority_path": bool(canonical_turn.get("authority_path")),
            },
            "retrieval": {
                "before_result_count": int(retrieval_before.get("result_count") or 0),
                "after_result_count": int(retrieval_after.get("result_count") or 0),
                "improved": bool(retrieval_improved),
            },
            "repeated_incident_improvement": {
                "before_result_count": int(repeat_before.get("result_count") or 0),
                "mid_result_count": int(repeat_mid.get("result_count") or 0),
                "after_result_count": int(repeat_after.get("result_count") or 0),
                "improved": bool(repeated_incident_improved),
            },
            "dreamer_transfer_improvement": {
                "candidate_queued": int(queue_out.get("added") or 0) > 0,
                "candidate_id": candidate_id,
                "accepted": accepted,
                "applied_association": str((((decision_out.get("applied") or {}) if isinstance(decision_out, dict) else {}).get("association_id") or "")),
                "before_result_count": int(transfer_before.get("result_count") or 0),
                "after_result_count": int(transfer_after.get("result_count") or 0),
                "improved": bool(dreamer_transfer_improved),
            },
        },
    }

    s = payload.get("steps") or {}
    quick_value_passed = bool(
        (s.get("canonical_write") or {}).get("ok")
        and (s.get("retrieval") or {}).get("improved")
        and (s.get("repeated_incident_improvement") or {}).get("improved")
        and (s.get("dreamer_transfer_improvement") or {}).get("improved")
    )
    payload["overall"] = {
        "quick_value_passed": quick_value_passed,
        "required_demo_path": [
            "one_canonical_write",
            "one_retrieval",
            "repeated_incident_improvement",
            "dreamer_assisted_transfer_improvement",
        ],
    }
    return payload


__all__ = ["reviewer_quick_value_v2"]

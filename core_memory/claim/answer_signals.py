"""
Compute answer signals from retrieval results and claim state.
Returns four scores used by answer_policy to decide how to answer.
"""
from __future__ import annotations


def compute_answer_signals(
    results: list[dict],
    current_state: dict | None,
    query: str,
) -> dict:
    """
    Compute four answer quality signals.

    Returns:
        {
            anchor_confidence: float (0-1) — how confident is the best anchor claim
            evidence_sufficiency: float (0-1) — how much supporting evidence exists
            currentness_fit: float (0-1) — how current/fresh the evidence is
            conflict_penalty: float (0-1) — penalty for conflicting claims (higher = worse)
        }
    """
    anchor_confidence = 0.0
    evidence_sufficiency = 0.0
    currentness_fit = 0.5  # default neutral
    conflict_penalty = 0.0

    # Anchor confidence: from best active claim
    if current_state and current_state.get("slots"):
        active_claims = [
            slot["current_claim"]
            for slot in current_state["slots"].values()
            if slot.get("status") == "active" and slot.get("current_claim")
        ]
        if active_claims:
            confidences = [c.get("confidence", 0.0) for c in active_claims]
            anchor_confidence = max(confidences)

        # Conflict penalty: fraction of slots in conflict
        total = len(current_state["slots"])
        conflicts = current_state.get("conflict_slots", 0)
        if total > 0:
            conflict_penalty = conflicts / total

    # Evidence sufficiency: based on number of retrieval results
    if results:
        # Normalize: 5+ results = 1.0, 0 = 0.0
        evidence_sufficiency = min(len(results) / 5.0, 1.0)

        # Currentness: if results have scores, use top score as proxy
        scores = [r.get("score", 0.0) for r in results if "score" in r]
        if scores:
            currentness_fit = max(scores)

    return {
        "anchor_confidence": round(anchor_confidence, 3),
        "evidence_sufficiency": round(evidence_sufficiency, 3),
        "currentness_fit": round(currentness_fit, 3),
        "conflict_penalty": round(conflict_penalty, 3),
    }

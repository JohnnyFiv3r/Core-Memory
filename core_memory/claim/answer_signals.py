"""
Compute answer signals from retrieval results and claim state.
Returns four scores used by answer_policy to decide how to answer.
"""
from __future__ import annotations


def _query_terms(text: str) -> set[str]:
    import re

    s = str(text or "").lower().replace("_", " ").replace("-", " ")
    return {tok for tok in re.findall(r"[a-z0-9]+", s) if len(tok) >= 2}


def compute_answer_signals(
    results: list[dict],
    current_state: dict | None,
    query: str,
    as_of: str | None = None,
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

    q_terms = _query_terms(query)
    claim_anchor_hits = [r for r in (results or []) if str((r or {}).get("anchor_reason") or "") == "claim_current_state"]

    # Anchor confidence: from query-matched active claims when possible.
    if current_state and current_state.get("slots"):
        active_claims = []
        matched_claims = []
        for key, slot in (current_state.get("slots") or {}).items():
            if slot.get("status") != "active" or not slot.get("current_claim"):
                continue
            claim = slot["current_claim"]
            active_claims.append(claim)

            key_terms = _query_terms(str(key or ""))
            value_terms = _query_terms(str(claim.get("value") or ""))
            if q_terms.intersection(key_terms.union(value_terms)):
                matched_claims.append(claim)

        if matched_claims:
            anchor_confidence = max(float(c.get("confidence", 0.0) or 0.0) for c in matched_claims)
        elif active_claims:
            anchor_confidence = max(float(c.get("confidence", 0.0) or 0.0) for c in active_claims)

        # Conflict penalty: fraction of slots in conflict
        total = len(current_state["slots"])
        conflicts = current_state.get("conflict_slots", 0)
        if total > 0:
            conflict_penalty = conflicts / total

    # Evidence sufficiency: based on number/quality of retrieval results
    if results:
        evidence_sufficiency = min(len(results) / 5.0, 1.0)

        scores = [float(r.get("score", 0.0) or 0.0) for r in results if "score" in r]
        if scores:
            currentness_fit = max(scores)

    temporal_scores = [
        float(((r or {}).get("feature_scores") or {}).get("temporal_fit") or 0.0)
        for r in (results or [])
        if isinstance((r or {}).get("feature_scores"), dict)
    ]
    if temporal_scores:
        currentness_fit = max(currentness_fit, max(temporal_scores))

    # Claim-state anchor is considered stronger evidence for fact-first queries.
    if claim_anchor_hits:
        evidence_sufficiency = max(evidence_sufficiency, 0.65)
        currentness_fit = max(currentness_fit, 0.75)

    if str(as_of or "").strip():
        # as_of queries should have explicit temporal-fit pressure
        evidence_sufficiency = max(evidence_sufficiency, 0.35 if results else evidence_sufficiency)
        if temporal_scores:
            currentness_fit = max(currentness_fit, max(temporal_scores))

    return {
        "anchor_confidence": round(anchor_confidence, 3),
        "evidence_sufficiency": round(evidence_sufficiency, 3),
        "currentness_fit": round(currentness_fit, 3),
        "conflict_penalty": round(conflict_penalty, 3),
    }

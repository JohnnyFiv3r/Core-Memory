"""
Answer policy: decides how to answer based on claim state and retrieval signals.

Outcomes:
  answer_current   — answer from current active claim(s), high confidence
  answer_historical — answer from historical claims, acknowledging staleness
  answer_partial   — fuzzy-but-grounded answer, partial evidence
  abstain          — no credible anchor, do not answer from memory
"""
from __future__ import annotations
from core_memory.claim.answer_signals import compute_answer_signals


ANSWER_OUTCOMES = {
    "answer_current": "Answer directly from current active claims.",
    "answer_historical": "Answer from historical claims, note potential staleness.",
    "answer_partial": "Answer partially — some grounding exists but incomplete.",
    "abstain": "No credible anchor claim — do not answer from memory.",
}


def decide_answer_outcome(
    results: list[dict],
    current_state: dict | None,
    query: str,
) -> str:
    """
    Decide the answer outcome based on signals.

    Bias: prefer answer_partial over abstain.
    abstain only when anchor_confidence == 0 AND evidence_sufficiency == 0.
    """
    signals = compute_answer_signals(results, current_state, query)

    anchor = signals["anchor_confidence"]
    evidence = signals["evidence_sufficiency"]
    currentness = signals["currentness_fit"]
    conflict = signals["conflict_penalty"]
    claim_anchor_hit = any(str((r or {}).get("anchor_reason") or "") == "claim_current_state" for r in (results or []))

    # High conflict: answer_partial regardless of confidence
    if conflict > 0.5:
        return "answer_partial"

    # Strong anchor, good evidence → answer_current
    if anchor >= 0.7 and (evidence >= 0.4 or (claim_anchor_hit and evidence >= 0.2)):
        return "answer_current"

    # Decent anchor but low evidence, or historical signals → answer_historical
    if anchor >= 0.5 and evidence < 0.4:
        return "answer_historical"

    # Some evidence but low anchor → answer_partial
    if evidence > 0.0 or anchor > 0.0:
        return "answer_partial"

    # No anchor, no evidence → abstain
    return "abstain"


def score_answer(
    results: list[dict],
    current_state: dict | None,
    query: str,
) -> dict:
    """
    Full answer scoring: returns outcome + signals.
    """
    signals = compute_answer_signals(results, current_state, query)
    outcome = decide_answer_outcome(results, current_state, query)
    return {
        "outcome": outcome,
        "signals": signals,
    }

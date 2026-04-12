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
    as_of: str | None = None,
) -> str:
    """
    Decide the answer outcome based on signals.

    Bias: prefer answer_partial over abstain.
    abstain only when anchor_confidence == 0 AND evidence_sufficiency == 0.
    """
    signals = compute_answer_signals(results, current_state, query, as_of=as_of)

    anchor = signals["anchor_confidence"]
    evidence = signals["evidence_sufficiency"]
    currentness = signals["currentness_fit"]
    conflict = signals["conflict_penalty"]
    claim_anchor_hit = any(str((r or {}).get("anchor_reason") or "") == "claim_current_state" for r in (results or []))
    lower_q = str(query or "").lower()
    historical_intent = bool(str(as_of or "").strip()) or any(x in lower_q for x in ["last ", "used to", "historical", "as of", "previous"])

    temporal_fit_scores = [
        float(((r or {}).get("feature_scores") or {}).get("temporal_fit") or 0.0)
        for r in (results or [])
        if isinstance((r or {}).get("feature_scores"), dict)
    ]
    temporal_fit_max = max(temporal_fit_scores) if temporal_fit_scores else 0.0
    explicit_as_of = bool(str(as_of or "").strip())
    # Historical promotion requires explicit alignment, not only lexical cues.
    historical_alignment_ok = bool(temporal_fit_max >= 0.90 or (explicit_as_of and temporal_fit_max >= 0.60))

    # High conflict: answer_partial regardless of confidence
    if conflict > 0.5:
        return "answer_partial"

    if historical_intent and historical_alignment_ok and anchor >= 0.45 and evidence >= 0.25:
        return "answer_historical"

    # Strong anchor, good evidence → answer_current
    if (not historical_intent) and anchor >= 0.7 and (evidence >= 0.4 or (claim_anchor_hit and evidence >= 0.2)):
        return "answer_current"

    # Historical outcome may still be valid for explicit aligned evidence under low support.
    if historical_intent and historical_alignment_ok and anchor >= 0.5 and evidence < 0.4:
        return "answer_historical"

    # Some evidence but low anchor → answer_partial
    if evidence > 0.0 or anchor > 0.0:
        return "answer_partial"

    # No anchor, no evidence → abstain
    return "abstain"


def explain_answer_outcome(
    *,
    outcome: str,
    signals: dict,
    query: str,
    as_of: str | None = None,
) -> str:
    anchor = float((signals or {}).get("anchor_confidence") or 0.0)
    evidence = float((signals or {}).get("evidence_sufficiency") or 0.0)
    conflict = float((signals or {}).get("conflict_penalty") or 0.0)
    lower_q = str(query or "").lower()
    historical_intent = bool(str(as_of or "").strip()) or any(x in lower_q for x in ["last ", "used to", "historical", "as of", "previous"])

    if outcome == "answer_partial" and conflict > 0.5:
        return "conflict_penalty_high"
    if outcome == "answer_historical" and historical_intent:
        return "historical_intent_or_as_of"
    if outcome == "answer_current" and anchor >= 0.7:
        return "strong_current_anchor"
    if outcome == "answer_historical" and anchor >= 0.5 and evidence < 0.4:
        return "anchor_present_but_evidence_limited"
    if outcome == "answer_partial" and (anchor > 0.0 or evidence > 0.0):
        return "partial_grounding"
    if outcome == "abstain":
        return "no_credible_anchor"
    return "policy_default"


def score_answer(
    results: list[dict],
    current_state: dict | None,
    query: str,
    as_of: str | None = None,
) -> dict:
    """
    Full answer scoring: returns outcome + signals.
    """
    signals = compute_answer_signals(results, current_state, query, as_of=as_of)
    outcome = decide_answer_outcome(results, current_state, query, as_of=as_of)
    reason = explain_answer_outcome(outcome=outcome, signals=signals, query=query, as_of=as_of)
    return {
        "outcome": outcome,
        "signals": signals,
        "decision_reason": reason,
        "as_of": str(as_of or "") or None,
    }

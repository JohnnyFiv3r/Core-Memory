import pytest
from core_memory.claim.answer_policy import decide_answer_outcome, score_answer, ANSWER_OUTCOMES
from core_memory.claim.answer_signals import compute_answer_signals


def make_state(active=0, conflict=0):
    slots = {}
    for i in range(active):
        slots[f"user:slot{i}"] = {
            "status": "active",
            "current_claim": {"id": f"c{i}", "confidence": 0.8},
        }
    for i in range(conflict):
        slots[f"user:conflict{i}"] = {
            "status": "conflict",
            "current_claim": None,
        }
    return {
        "slots": slots,
        "total_slots": active + conflict,
        "active_slots": active,
        "conflict_slots": conflict,
    }


def make_results(n=3, score=0.8):
    return [{"id": f"r{i}", "score": score} for i in range(n)]


def test_abstain_when_no_anchor_no_evidence():
    result = decide_answer_outcome([], None, "what is my preference?")
    assert result == "abstain"


def test_answer_current_high_confidence():
    state = make_state(active=2)
    results = make_results(5)
    result = decide_answer_outcome(results, state, "what do I prefer?")
    assert result == "answer_current"


def test_answer_historical_low_evidence():
    state = make_state(active=1)
    results = []  # no retrieval results
    result = decide_answer_outcome(results, state, "what did I used to prefer?")
    assert result == "answer_historical"


def test_answer_partial_some_evidence():
    results = make_results(2, score=0.5)
    result = decide_answer_outcome(results, None, "anything about preferences?")
    assert result == "answer_partial"


def test_answer_partial_on_conflict():
    state = make_state(active=1, conflict=2)  # majority conflict
    results = make_results(3)
    result = decide_answer_outcome(results, state, "what do I prefer?")
    assert result == "answer_partial"


def test_abstain_is_rare():
    # abstain requires BOTH anchor=0 AND evidence=0
    # With any results, should not abstain
    results = make_results(1, score=0.3)
    result = decide_answer_outcome(results, None, "query")
    assert result != "abstain"


def test_all_outcomes_defined():
    for outcome in ["answer_current", "answer_historical", "answer_partial", "abstain"]:
        assert outcome in ANSWER_OUTCOMES


def test_score_answer_returns_outcome_and_signals():
    result = score_answer(make_results(3), make_state(active=2), "query")
    assert "outcome" in result
    assert "signals" in result
    assert "anchor_confidence" in result["signals"]
    assert "evidence_sufficiency" in result["signals"]
    assert "conflict_penalty" in result["signals"]


def test_signals_valid_range():
    signals = compute_answer_signals(make_results(3), make_state(active=1), "query")
    for key, val in signals.items():
        assert 0.0 <= val <= 1.0, f"{key} out of range: {val}"

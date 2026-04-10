import pytest
from core_memory.claim.retrieval_planner import plan_retrieval_mode, boost_claim_results, RETRIEVAL_MODES

def test_empty_query_returns_mixed():
    assert plan_retrieval_mode("", None, None) == "mixed"

def test_causal_query():
    assert plan_retrieval_mode("why did this happen?", None, None) == "causal_first"

def test_temporal_query():
    assert plan_retrieval_mode("what happened recently?", None, None) == "temporal_first"

def test_fact_query():
    assert plan_retrieval_mode("what is the capital of France?", None, None) == "fact_first"

def test_known_subject_boosts_fact_first():
    current_state = {
        "slots": {
            "user:preference": {"status": "active", "current_claim": {"id": "c1"}}
        }
    }
    result = plan_retrieval_mode("what is my preference?", None, current_state)
    assert result == "fact_first"

def test_mixed_default():
    assert plan_retrieval_mode("tell me something interesting", None, None) == "mixed"

def test_boost_empty_state():
    results = [{"score": 0.9, "id": "b1"}]
    assert boost_claim_results(results, None) == results

def test_boost_preserves_results():
    results = [{"score": 0.9, "id": "b1"}, {"score": 0.7, "id": "b2"}]
    state = {"slots": {}}
    boosted = boost_claim_results(results, state)
    assert len(boosted) == 2

def test_all_retrieval_modes_defined():
    for mode in ["fact_first", "causal_first", "temporal_first", "mixed"]:
        assert mode in RETRIEVAL_MODES

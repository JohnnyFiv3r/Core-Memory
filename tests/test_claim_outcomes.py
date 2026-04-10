import pytest
from core_memory.claim.outcomes import classify_memory_outcome, INTERACTION_ROLES

def test_none_when_no_memory_used():
    result = classify_memory_outcome({})
    assert result is None

def test_none_with_none_input():
    result = classify_memory_outcome(None)
    assert result is None

def test_none_when_used_memory_false_no_beads():
    result = classify_memory_outcome({"used_memory": False, "retrieved_beads": []})
    assert result is None

def test_memory_resolution_when_beads_retrieved():
    result = classify_memory_outcome({"retrieved_beads": [{"id": "b1"}]})
    assert result is not None
    assert result["interaction_role"] == "memory_resolution"
    assert "memory_outcome" in result
    assert result["memory_outcome"]["bead_count"] == 1

def test_memory_correction_takes_priority():
    result = classify_memory_outcome({
        "used_memory": True,
        "retrieved_beads": [{"id": "b1"}],
        "correction_triggered": True,
    })
    assert result["interaction_role"] == "memory_correction"

def test_memory_reflection_role():
    result = classify_memory_outcome({
        "used_memory": True,
        "retrieved_beads": [{"id": "b1"}],
        "reflection_triggered": True,
    })
    assert result["interaction_role"] == "memory_reflection"

def test_used_memory_true_no_beads_returns_resolution():
    result = classify_memory_outcome({"used_memory": True, "retrieved_beads": []})
    assert result is not None
    assert result["interaction_role"] == "memory_resolution"

def test_all_interaction_roles_defined():
    for role in ["memory_resolution", "memory_correction", "memory_reflection"]:
        assert role in INTERACTION_ROLES

def test_outcome_has_required_keys():
    result = classify_memory_outcome({"used_memory": True})
    assert "interaction_role" in result
    assert "memory_outcome" in result
    assert "role" in result["memory_outcome"]
    assert "description" in result["memory_outcome"]

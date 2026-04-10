import pytest
from core_memory.claim.resolver_helpers import is_claim_current, find_conflicts, build_claim_timeline


def test_is_current_no_updates():
    claim = {"id": "c1", "subject": "user", "slot": "pref"}
    assert is_claim_current(claim, []) == True


def test_is_current_superseded():
    claim = {"id": "c1"}
    updates = [{"decision": "supersede", "target_claim_id": "c1"}]
    assert is_claim_current(claim, updates) == False


def test_is_current_retracted():
    claim = {"id": "c1"}
    updates = [{"decision": "retract", "target_claim_id": "c1"}]
    assert is_claim_current(claim, updates) == False


def test_is_current_reaffirm_doesnt_remove():
    claim = {"id": "c1"}
    updates = [{"decision": "reaffirm", "target_claim_id": "c1"}]
    assert is_claim_current(claim, updates) == True


def test_find_conflicts_empty():
    assert find_conflicts([], []) == []


def test_find_conflicts_found():
    claims = [{"id": "c1"}, {"id": "c2"}]
    updates = [{"decision": "conflict", "target_claim_id": "c1"}]
    result = find_conflicts(claims, updates)
    assert len(result) == 1
    assert result[0]["id"] == "c1"


def test_build_timeline_structure():
    claims = [{"id": "c1", "subject": "user", "slot": "pref", "value": "coffee"}]
    updates = [{"decision": "retract", "target_claim_id": "c1"}]
    timeline = build_claim_timeline(claims, updates)
    assert len(timeline) == 2
    event_types = {e["event_type"] for e in timeline}
    assert "assert" in event_types
    assert "retract" in event_types

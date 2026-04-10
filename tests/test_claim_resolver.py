import pytest
from core_memory.claim.resolver import resolve_all_current_state
from core_memory.persistence.store_claim_ops import write_claims_to_bead, write_claim_updates_to_bead


def make_claim(claim_id, subject="user", slot="preference", value="coffee"):
    return {
        "id": claim_id,
        "claim_kind": "preference",
        "subject": subject,
        "slot": slot,
        "value": value,
        "reason_text": "stated",
        "confidence": 0.8,
    }


def test_empty_store(tmp_path):
    result = resolve_all_current_state(str(tmp_path))
    assert result["total_slots"] == 0
    assert result["slots"] == {}


def test_single_active_claim(tmp_path):
    write_claims_to_bead(str(tmp_path), "bead1", [make_claim("c1")])
    result = resolve_all_current_state(str(tmp_path))
    assert result["total_slots"] == 1
    assert result["active_slots"] == 1
    slot = result["slots"]["user:preference"]
    assert slot["status"] == "active"
    assert slot["current_claim"]["id"] == "c1"


def test_supersession(tmp_path):
    write_claims_to_bead(str(tmp_path), "bead1", [make_claim("c1", value="tea")])
    write_claims_to_bead(str(tmp_path), "bead2", [make_claim("c2", value="coffee")])
    update = {
        "id": "u1", "decision": "supersede",
        "target_claim_id": "c1", "replacement_claim_id": "c2",
        "subject": "user", "slot": "preference",
        "reason_text": "changed", "trigger_bead_id": "bead2",
    }
    write_claim_updates_to_bead(str(tmp_path), "bead2", [update])
    result = resolve_all_current_state(str(tmp_path))
    slot = result["slots"]["user:preference"]
    assert slot["current_claim"]["id"] == "c2"
    assert slot["status"] == "active"


def test_retraction(tmp_path):
    write_claims_to_bead(str(tmp_path), "bead1", [make_claim("c1")])
    update = {
        "id": "u1", "decision": "retract",
        "target_claim_id": "c1",
        "subject": "user", "slot": "preference",
        "reason_text": "no longer true", "trigger_bead_id": "bead2",
    }
    write_claim_updates_to_bead(str(tmp_path), "bead2", [update])
    result = resolve_all_current_state(str(tmp_path))
    slot = result["slots"]["user:preference"]
    assert slot["current_claim"] is None
    assert slot["status"] == "retracted"


def test_conflict_detection(tmp_path):
    write_claims_to_bead(str(tmp_path), "bead1", [make_claim("c1")])
    update = {
        "id": "u1", "decision": "conflict",
        "target_claim_id": "c1",
        "subject": "user", "slot": "preference",
        "reason_text": "contradicts other claim", "trigger_bead_id": "bead2",
    }
    write_claim_updates_to_bead(str(tmp_path), "bead2", [update])
    result = resolve_all_current_state(str(tmp_path))
    assert result["conflict_slots"] == 1
    slot = result["slots"]["user:preference"]
    assert slot["status"] == "conflict"
    assert len(slot["conflicts"]) == 1


def test_multiple_slots(tmp_path):
    write_claims_to_bead(str(tmp_path), "bead1", [
        make_claim("c1", subject="user", slot="preference"),
        make_claim("c2", subject="user", slot="occupation", value="engineer"),
    ])
    result = resolve_all_current_state(str(tmp_path))
    assert result["total_slots"] == 2
    assert "user:preference" in result["slots"]
    assert "user:occupation" in result["slots"]


def test_history_preserved(tmp_path):
    write_claims_to_bead(str(tmp_path), "bead1", [make_claim("c1", value="tea")])
    write_claims_to_bead(str(tmp_path), "bead2", [make_claim("c2", value="coffee")])
    result = resolve_all_current_state(str(tmp_path))
    slot = result["slots"]["user:preference"]
    assert len(slot["history"]) == 2

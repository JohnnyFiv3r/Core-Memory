import os
import json
import pytest
import tempfile
from core_memory.persistence.store_claim_ops import (
    write_claims_to_bead, write_claim_updates_to_bead,
    read_claims_for_bead, resolve_current_state
)

@pytest.fixture
def tmp_root(tmp_path):
    return str(tmp_path)

def make_claim(claim_id, subject="user", slot="preference", value="coffee"):
    return {
        "id": claim_id,
        "claim_kind": "preference",
        "subject": subject,
        "slot": slot,
        "value": value,
        "reason_text": "stated in turn",
        "confidence": 0.8,
    }

def test_write_and_read_claims(tmp_root):
    claim = make_claim("c1")
    write_claims_to_bead(tmp_root, "bead1", [claim])
    result = read_claims_for_bead(tmp_root, "bead1")
    assert len(result) == 1
    assert result[0]["id"] == "c1"

def test_append_claims(tmp_root):
    write_claims_to_bead(tmp_root, "bead1", [make_claim("c1")])
    write_claims_to_bead(tmp_root, "bead1", [make_claim("c2")])
    result = read_claims_for_bead(tmp_root, "bead1")
    assert len(result) == 2

def test_read_empty_bead(tmp_root):
    result = read_claims_for_bead(tmp_root, "nonexistent")
    assert result == []

def test_resolve_not_found(tmp_root):
    result = resolve_current_state(tmp_root, "user", "preference")
    assert result["status"] == "not_found"
    assert result["current_claim"] is None

def test_resolve_active_claim(tmp_root):
    claim = make_claim("c1", subject="user", slot="preference", value="coffee")
    write_claims_to_bead(tmp_root, "bead1", [claim])
    result = resolve_current_state(tmp_root, "user", "preference")
    assert result["status"] == "active"
    assert result["current_claim"]["id"] == "c1"

def test_resolve_retraction(tmp_root):
    claim = make_claim("c1")
    write_claims_to_bead(tmp_root, "bead1", [claim])
    update = {"id": "u1", "decision": "retract", "target_claim_id": "c1", "subject": "user", "slot": "preference", "reason_text": "no longer true", "trigger_bead_id": "bead2"}
    write_claim_updates_to_bead(tmp_root, "bead2", [update])
    result = resolve_current_state(tmp_root, "user", "preference")
    assert result["current_claim"] is None
    assert result["status"] == "retracted"

def test_resolve_supersession(tmp_root):
    c1 = make_claim("c1", value="coffee")
    c2 = make_claim("c2", value="tea")
    write_claims_to_bead(tmp_root, "bead1", [c1])
    write_claims_to_bead(tmp_root, "bead2", [c2])
    update = {"id": "u1", "decision": "supersede", "target_claim_id": "c1", "subject": "user", "slot": "preference", "reason_text": "changed preference", "trigger_bead_id": "bead2"}
    write_claim_updates_to_bead(tmp_root, "bead2", [update])
    result = resolve_current_state(tmp_root, "user", "preference")
    assert result["current_claim"]["id"] == "c2"

def test_write_claim_updates(tmp_root):
    update = {"id": "u1", "decision": "reaffirm", "target_claim_id": "c1", "subject": "user", "slot": "preference", "reason_text": "still true", "trigger_bead_id": "bead1"}
    write_claim_updates_to_bead(tmp_root, "bead1", [update])
    from core_memory.persistence.store_claim_ops import read_claim_updates_for_bead
    result = read_claim_updates_for_bead(tmp_root, "bead1")
    assert len(result) == 1
    assert result[0]["decision"] == "reaffirm"

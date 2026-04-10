import pytest
from unittest.mock import patch

def test_emit_updates_no_existing_claim(tmp_path):
    from core_memory.claim.update_policy import emit_claim_updates
    claims = [{"id": "c1", "subject": "user", "slot": "preference", "claim_kind": "preference", "value": "coffee", "reason_text": "r", "confidence": 0.8}]
    updates = emit_claim_updates(str(tmp_path), claims, "bead1")
    # No existing claim, no update emitted
    assert updates == []

def test_emit_updates_supersedes_existing(tmp_path):
    from core_memory.persistence.store_claim_ops import write_claims_to_bead
    from core_memory.claim.update_policy import emit_claim_updates

    # Write an existing claim
    old_claim = {"id": "old1", "subject": "user", "slot": "preference", "claim_kind": "preference", "value": "tea", "reason_text": "r", "confidence": 0.8}
    write_claims_to_bead(str(tmp_path), "bead0", [old_claim])

    # New claim for same subject+slot
    new_claim = {"id": "new1", "subject": "user", "slot": "preference", "claim_kind": "preference", "value": "coffee", "reason_text": "r", "confidence": 0.9}
    updates = emit_claim_updates(str(tmp_path), [new_claim], "bead1")

    assert len(updates) == 1
    assert updates[0]["decision"] == "supersede"
    assert updates[0]["target_claim_id"] == "old1"
    assert updates[0]["trigger_bead_id"] == "bead1"

def test_emit_updates_require_trigger_bead_id(tmp_path):
    from core_memory.persistence.store_claim_ops import write_claims_to_bead
    from core_memory.claim.update_policy import emit_claim_updates

    old_claim = {"id": "old1", "subject": "user", "slot": "food", "claim_kind": "preference", "value": "pizza", "reason_text": "r", "confidence": 0.8}
    write_claims_to_bead(str(tmp_path), "bead0", [old_claim])

    new_claim = {"id": "new1", "subject": "user", "slot": "food", "claim_kind": "preference", "value": "sushi", "reason_text": "r", "confidence": 0.9}
    updates = emit_claim_updates(str(tmp_path), [new_claim], "bead1")

    for u in updates:
        assert u.get("trigger_bead_id"), "trigger_bead_id must be set"

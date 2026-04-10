import tempfile
import unittest


class TestClaimUpdatePolicy(unittest.TestCase):
    def test_emit_updates_no_existing_claim(self):
        from core_memory.claim.update_policy import emit_claim_updates

        with tempfile.TemporaryDirectory() as td:
            claims = [
                {
                    "id": "c1",
                    "subject": "user",
                    "slot": "preference",
                    "claim_kind": "preference",
                    "value": "coffee",
                    "reason_text": "r",
                    "confidence": 0.8,
                }
            ]
            updates = emit_claim_updates(td, claims, "bead1")
        self.assertEqual([], updates)

    def test_emit_updates_supersedes_existing(self):
        from core_memory.claim.update_policy import emit_claim_updates
        from core_memory.persistence.store_claim_ops import write_claims_to_bead

        with tempfile.TemporaryDirectory() as td:
            old_claim = {
                "id": "old1",
                "subject": "user",
                "slot": "preference",
                "claim_kind": "preference",
                "value": "tea",
                "reason_text": "r",
                "confidence": 0.8,
            }
            write_claims_to_bead(td, "bead0", [old_claim])

            new_claim = {
                "id": "new1",
                "subject": "user",
                "slot": "preference",
                "claim_kind": "preference",
                "value": "coffee",
                "reason_text": "r",
                "confidence": 0.9,
            }
            updates = emit_claim_updates(td, [new_claim], "bead1")

        self.assertEqual(1, len(updates))
        self.assertEqual("supersede", updates[0]["decision"])
        self.assertEqual("old1", updates[0]["target_claim_id"])
        self.assertEqual("bead1", updates[0]["trigger_bead_id"])

    def test_emit_updates_require_trigger_bead_id(self):
        from core_memory.claim.update_policy import emit_claim_updates
        from core_memory.persistence.store_claim_ops import write_claims_to_bead

        with tempfile.TemporaryDirectory() as td:
            old_claim = {
                "id": "old1",
                "subject": "user",
                "slot": "food",
                "claim_kind": "preference",
                "value": "pizza",
                "reason_text": "r",
                "confidence": 0.8,
            }
            write_claims_to_bead(td, "bead0", [old_claim])

            new_claim = {
                "id": "new1",
                "subject": "user",
                "slot": "food",
                "claim_kind": "preference",
                "value": "sushi",
                "reason_text": "r",
                "confidence": 0.9,
            }
            updates = emit_claim_updates(td, [new_claim], "bead1")

        for update in updates:
            self.assertTrue(update.get("trigger_bead_id"), "trigger_bead_id must be set")


if __name__ == "__main__":
    unittest.main()

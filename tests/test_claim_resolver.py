import tempfile
import unittest

from core_memory.claim.resolver import resolve_all_current_state
from core_memory.persistence.store_claim_ops import write_claim_updates_to_bead, write_claims_to_bead


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


class TestClaimResolver(unittest.TestCase):
    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as td:
            result = resolve_all_current_state(td)
            self.assertEqual(0, result["total_slots"])
            self.assertEqual({}, result["slots"])

    def test_single_active_claim(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            result = resolve_all_current_state(td)
            self.assertEqual(1, result["total_slots"])
            self.assertEqual(1, result["active_slots"])
            slot = result["slots"]["user:preference"]
            self.assertEqual("active", slot["status"])
            self.assertEqual("c1", slot["current_claim"]["id"])

    def test_supersession(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1", value="tea")])
            write_claims_to_bead(td, "bead2", [make_claim("c2", value="coffee")])
            update = {
                "id": "u1",
                "decision": "supersede",
                "target_claim_id": "c1",
                "replacement_claim_id": "c2",
                "subject": "user",
                "slot": "preference",
                "reason_text": "changed",
                "trigger_bead_id": "bead2",
            }
            write_claim_updates_to_bead(td, "bead2", [update])
            result = resolve_all_current_state(td)
            slot = result["slots"]["user:preference"]
            self.assertEqual("c2", slot["current_claim"]["id"])
            self.assertEqual("active", slot["status"])

    def test_retraction(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            update = {
                "id": "u1",
                "decision": "retract",
                "target_claim_id": "c1",
                "subject": "user",
                "slot": "preference",
                "reason_text": "no longer true",
                "trigger_bead_id": "bead2",
            }
            write_claim_updates_to_bead(td, "bead2", [update])
            result = resolve_all_current_state(td)
            slot = result["slots"]["user:preference"]
            self.assertIsNone(slot["current_claim"])
            self.assertEqual("retracted", slot["status"])

    def test_conflict_detection(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1")])
            update = {
                "id": "u1",
                "decision": "conflict",
                "target_claim_id": "c1",
                "subject": "user",
                "slot": "preference",
                "reason_text": "contradicts other claim",
                "trigger_bead_id": "bead2",
            }
            write_claim_updates_to_bead(td, "bead2", [update])
            result = resolve_all_current_state(td)
            self.assertEqual(1, result["conflict_slots"])
            slot = result["slots"]["user:preference"]
            self.assertEqual("conflict", slot["status"])
            self.assertEqual(1, len(slot["conflicts"]))

    def test_multiple_slots(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(
                td,
                "bead1",
                [make_claim("c1", subject="user", slot="preference"), make_claim("c2", subject="user", slot="occupation", value="engineer")],
            )
            result = resolve_all_current_state(td)
            self.assertEqual(2, result["total_slots"])
            self.assertIn("user:preference", result["slots"])
            self.assertIn("user:occupation", result["slots"])

    def test_history_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(td, "bead1", [make_claim("c1", value="tea")])
            write_claims_to_bead(td, "bead2", [make_claim("c2", value="coffee")])
            result = resolve_all_current_state(td)
            slot = result["slots"]["user:preference"]
            self.assertEqual(2, len(slot["history"]))


if __name__ == "__main__":
    unittest.main()

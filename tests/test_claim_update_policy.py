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

    def test_emit_explicit_retract_and_conflict_from_reviewed_updates(self):
        from core_memory.claim.update_policy import emit_claim_updates
        from core_memory.persistence.store_claim_ops import write_claims_to_bead, resolve_current_state

        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(
                td,
                "bead0",
                [
                    {
                        "id": "old1",
                        "subject": "user",
                        "slot": "preference",
                        "claim_kind": "preference",
                        "value": "tea",
                        "reason_text": "r",
                        "confidence": 0.8,
                    }
                ],
            )
            updates = emit_claim_updates(
                td,
                [],
                "bead1",
                reviewed_updates={
                    "claim_updates": [
                        {
                            "decision": "retract",
                            "target_claim_id": "old1",
                            "subject": "user",
                            "slot": "preference",
                            "reason_text": "explicit retract",
                            "evidence_bead_ids": ["retract-evidence"],
                        },
                        {
                            "decision": "conflict",
                            "target_claim_id": "old1",
                            "subject": "user",
                            "slot": "preference",
                            "reason_text": "explicit conflict",
                            "evidence_bead_ids": ["conflict-evidence"],
                        },
                    ]
                },
            )

            self.assertGreaterEqual(len(updates), 2)
            decisions = {u.get("decision") for u in updates}
            self.assertIn("retract", decisions)
            self.assertIn("conflict", decisions)

            state = resolve_current_state(td, "user", "preference")
            self.assertEqual("conflict", state.get("status"))

    def test_emit_reaffirm_when_same_value_repeated(self):
        from core_memory.claim.update_policy import emit_claim_updates
        from core_memory.persistence.store_claim_ops import write_claims_to_bead

        with tempfile.TemporaryDirectory() as td:
            write_claims_to_bead(
                td,
                "bead0",
                [
                    {
                        "id": "old1",
                        "subject": "user",
                        "slot": "timezone",
                        "claim_kind": "condition",
                        "value": "UTC",
                        "reason_text": "r",
                        "confidence": 0.7,
                    }
                ],
            )
            updates = emit_claim_updates(
                td,
                [
                    {
                        "id": "new1",
                        "subject": "user",
                        "slot": "timezone",
                        "claim_kind": "condition",
                        "value": "UTC",
                        "reason_text": "r2",
                        "confidence": 0.9,
                    }
                ],
                "bead1",
            )
            self.assertTrue(any(u.get("decision") == "reaffirm" for u in updates))

    def test_emit_updates_includes_grounding_hash_and_dedupes_same_grounding(self):
        from core_memory.claim.update_policy import emit_claim_updates
        from core_memory.persistence.store_claim_ops import read_claim_updates_for_bead

        with tempfile.TemporaryDirectory() as td:
            updates = emit_claim_updates(
                td,
                [],
                "bead1",
                reviewed_updates={
                    "claim_updates": [
                        {
                            "id": "u1",
                            "decision": "reaffirm",
                            "target_claim_id": "old1",
                            "subject": "user",
                            "slot": "preference",
                            "reason_text": "judged",
                            "evidence_bead_ids": ["ctx2", "ctx1"],
                            "judge_model": "judge-v1",
                            "prompt_version": "prompt-v1",
                            "rubric_version": "rubric-v1",
                        },
                        {
                            "id": "u2",
                            "decision": "reaffirm",
                            "target_claim_id": "old1",
                            "subject": "user",
                            "slot": "preference",
                            "reason_text": "same judged evidence",
                            "evidence_bead_ids": ["ctx1", "ctx2"],
                            "judge_model": "judge-v1",
                            "prompt_version": "prompt-v1",
                            "rubric_version": "rubric-v1",
                        },
                    ]
                },
            )
            stored = read_claim_updates_for_bead(td, "bead1")

        self.assertEqual(1, len(updates))
        self.assertEqual(1, len(stored))
        self.assertTrue(updates[0]["grounding_hash"].startswith("sha256:"))
        self.assertEqual(updates[0]["grounding_hash"], stored[0]["grounding_hash"])

    def test_emit_updates_keeps_distinct_decisions_with_same_grounding(self):
        from core_memory.claim.update_policy import emit_claim_updates
        from core_memory.persistence.store_claim_ops import read_claim_updates_for_bead

        with tempfile.TemporaryDirectory() as td:
            updates = emit_claim_updates(
                td,
                [],
                "bead1",
                reviewed_updates={
                    "claim_updates": [
                        {
                            "id": "u1",
                            "decision": "reaffirm",
                            "target_claim_id": "old1",
                            "subject": "user",
                            "slot": "preference",
                            "reason_text": "same evidence reaffirms current state",
                            "evidence_bead_ids": ["ctx1"],
                            "judge_model": "judge-v1",
                            "prompt_version": "prompt-v1",
                            "rubric_version": "rubric-v1",
                        },
                        {
                            "id": "u2",
                            "decision": "conflict",
                            "target_claim_id": "old1",
                            "subject": "user",
                            "slot": "preference",
                            "reason_text": "same evidence also marks conflict",
                            "evidence_bead_ids": ["ctx1"],
                            "judge_model": "judge-v1",
                            "prompt_version": "prompt-v1",
                            "rubric_version": "rubric-v1",
                        },
                    ]
                },
            )
            stored = read_claim_updates_for_bead(td, "bead1")

        self.assertEqual(2, len(updates))
        self.assertEqual(2, len(stored))
        self.assertEqual({"reaffirm", "conflict"}, {u.get("decision") for u in updates})
        self.assertEqual(1, len({u.get("grounding_hash") for u in updates}))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from core_memory.runtime.agent_authored_contract import validate_agent_authored_updates


class TestAgentAuthoredContractSlice2(unittest.TestCase):
    def test_requires_exactly_one_bead_row(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"], "retrieval_title": "A", "retrieval_eligible": True, "retrieval_facts": ["x"], "entities": ["A"], "topics": ["topic"]},
                    {"type": "context", "title": "B", "summary": ["y"], "retrieval_title": "B", "retrieval_eligible": True, "retrieval_facts": ["y"], "entities": ["B"], "topics": ["topic"]},
                ],
                "associations": [
                    {
                        "source_bead_id": "a",
                        "target_bead_id": "b",
                        "relationship": "supports",
                        "reason_text": "r",
                        "confidence": 0.7,
                    }
                ],
            }
        )
        self.assertFalse(ok)
        self.assertEqual("agent_bead_fields_missing", code)
        self.assertIn("beads_create_must_have_exactly_one_row", str(details.get("reason") or ""))

    def test_allows_missing_associations_for_first_turn(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"], "retrieval_title": "A", "retrieval_eligible": True, "retrieval_facts": ["x"], "entities": ["A"], "topics": ["topic"]},
                ]
            }
        )
        self.assertTrue(ok)
        self.assertIsNone(code)
        self.assertEqual(0, details.get("associations_count"))

    def test_rejects_invalid_association_confidence(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"], "retrieval_title": "A", "retrieval_eligible": True, "retrieval_facts": ["x"], "entities": ["A"], "topics": ["topic"]},
                ],
                "associations": [
                    {
                        "source_bead_id": "a",
                        "target_bead_id": "b",
                        "relationship": "supports",
                        "reason_text": "r",
                        "confidence": 1.5,
                    }
                ],
            }
        )
        self.assertFalse(ok)
        self.assertEqual("agent_updates_invalid", code)
        bad_rows = details.get("bad_association_rows") or []
        self.assertTrue(bad_rows)
        self.assertEqual("invalid_confidence", (bad_rows[0] or {}).get("reason"))

    def test_accepts_valid_payload(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"], "retrieval_title": "A", "retrieval_eligible": True, "retrieval_facts": ["x"], "entities": ["A"], "topics": ["topic"]},
                ],
                "associations": [
                    {
                        "source_bead_id": "a",
                        "target_bead_id": "b",
                        "relationship": "supports",
                        "reason_text": "r",
                        "confidence": 0.9,
                    }
                ],
            }
        )
        self.assertTrue(ok)
        self.assertIsNone(code)
        self.assertEqual(1, details.get("beads_create_count"))
        self.assertEqual(1, details.get("associations_count"))


if __name__ == "__main__":
    unittest.main()

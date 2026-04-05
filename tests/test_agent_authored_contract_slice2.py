from __future__ import annotations

import unittest

from core_memory.runtime.agent_authored_contract import validate_agent_authored_updates


class TestAgentAuthoredContractSlice2(unittest.TestCase):
    def test_requires_exactly_one_bead_row(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"]},
                    {"type": "context", "title": "B", "summary": ["y"]},
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

    def test_requires_associations_list(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"]},
                ]
            }
        )
        self.assertFalse(ok)
        self.assertEqual("agent_associations_missing", code)
        self.assertIn("associations_missing_or_empty", str(details.get("reason") or ""))

    def test_rejects_invalid_association_confidence(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"]},
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
                    {"type": "decision", "title": "A", "summary": ["x"]},
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

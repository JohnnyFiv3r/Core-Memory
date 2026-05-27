from __future__ import annotations

import unittest

from core_memory.runtime.agent_authored_contract import validate_agent_authored_updates


class TestAgentAuthoredContractSlice2(unittest.TestCase):
    def test_requires_at_least_one_bead_row(self):
        # Multiple bead rows are now accepted; the old exactly-one constraint was too strict.
        ok, _code, _details = validate_agent_authored_updates(
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
        self.assertTrue(ok)

    def test_requires_zero_bead_rows_fails(self):
        ok, code, details = validate_agent_authored_updates(
            {"beads_create": [], "associations": []}
        )
        self.assertFalse(ok)
        self.assertEqual("agent_bead_fields_missing", code)
        self.assertIn("at_least_one_row", str(details.get("reason") or ""))

    def test_associations_are_optional(self):
        # Crawler passes may find no linkable priors; omitting associations is valid.
        ok, _code, _details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {"type": "decision", "title": "A", "summary": ["x"]},
                ]
            }
        )
        self.assertTrue(ok)

    def test_associations_must_be_list_when_present(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [{"type": "decision", "title": "A", "summary": ["x"]}],
                "associations": "not-a-list",
            }
        )
        self.assertFalse(ok)
        self.assertEqual("agent_associations_missing", code)

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

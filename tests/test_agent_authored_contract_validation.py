from __future__ import annotations

import unittest

from core_memory.runtime.passes.agent_authored_contract import validate_agent_authored_updates


class TestAgentAuthoredContractSlice2(unittest.TestCase):
    def test_allows_multiple_bead_rows(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "decision",
                        "title": "A",
                        "summary": ["x"],
                        "because": ["rationale"],
                        "retrieval_title": "A",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["x"],
                        "entities": ["A"],
                        "topics": ["topic"],
                    },
                    {
                        "type": "context",
                        "title": "B",
                        "summary": ["y"],
                        "retrieval_title": "B",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["y"],
                        "entities": ["B"],
                        "topics": ["topic"],
                    },
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
        self.assertIsNone(code)
        self.assertEqual(2, details.get("beads_create_count"))

    def test_requires_zero_bead_rows_fails(self):
        ok, code, details = validate_agent_authored_updates({"beads_create": [], "associations": []})
        self.assertFalse(ok)
        self.assertEqual("agent_bead_fields_missing", code)
        self.assertIn("at_least_one_row", str(details.get("reason") or ""))

    def test_policy_does_not_override_contract_cardinality(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "context",
                        "title": "A",
                        "summary": ["x"],
                        "retrieval_title": "A",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["x"],
                        "entities": ["A"],
                        "topics": ["topic"],
                    },
                    {
                        "type": "context",
                        "title": "B",
                        "summary": ["y"],
                        "retrieval_title": "B",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["y"],
                        "entities": ["B"],
                        "topics": ["topic"],
                    },
                ]
            },
            max_create_per_turn=1,
        )
        self.assertTrue(ok)
        self.assertIsNone(code)
        self.assertEqual(2, details.get("beads_create_count"))

    def test_allows_missing_associations_for_first_turn(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "decision",
                        "title": "A",
                        "summary": ["x"],
                        "because": ["rationale"],
                        "retrieval_title": "A",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["x"],
                        "entities": ["A"],
                        "topics": ["topic"],
                    },
                ]
            }
        )
        self.assertTrue(ok)
        self.assertIsNone(code)
        self.assertEqual(0, details.get("associations_count"))

    def test_rejects_causal_type_without_because(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "decision",
                        "title": "A",
                        "summary": ["x"],
                        "retrieval_title": "A",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["x"],
                        "entities": ["A"],
                        "topics": ["topic"],
                    },
                ]
            }
        )
        self.assertFalse(ok)
        self.assertEqual("agent_causal_rationale_missing", code)

    def test_accepts_explicit_false_retrieval_eligibility(self):
        ok, code, _details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "context",
                        "title": "Thin continuity bead",
                        "summary": ["No durable retrieval claim."],
                        "entities": ["Core Memory"],
                        "retrieval_eligible": False,
                    }
                ]
            }
        )
        self.assertTrue(ok)
        self.assertIsNone(code)

    def test_rejects_missing_retrieval_eligibility(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "context",
                        "title": "Missing retrieval decision",
                        "summary": ["Hard mode requires an explicit decision."],
                        "entities": ["Core Memory"],
                    }
                ]
            }
        )
        self.assertFalse(ok)
        self.assertEqual("agent_bead_fields_missing", code)
        self.assertIn("retrieval_eligible", details["missing_bead_fields"])

    def test_associations_must_be_list_when_present(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [{"type": "decision", "title": "A", "summary": ["x"]}],
                "associations": "not-a-list",
            }
        )
        self.assertFalse(ok)
        self.assertIn(
            code, {"agent_associations_missing", "agent_causal_rationale_missing", "agent_bead_fields_missing"}
        )

    def test_rejects_string_summary_shape(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "context",
                        "title": "A",
                        "summary": "x",
                        "retrieval_title": "A",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["x"],
                        "entities": ["A"],
                        "topics": ["topic"],
                    },
                ]
            }
        )
        self.assertFalse(ok)
        self.assertEqual("agent_bead_fields_missing", code)
        self.assertIn("summary", details.get("missing_bead_fields") or [])

    def test_rejects_invalid_association_confidence(self):
        ok, code, details = validate_agent_authored_updates(
            {
                "beads_create": [
                    {
                        "type": "decision",
                        "title": "A",
                        "summary": ["x"],
                        "because": ["rationale"],
                        "retrieval_title": "A",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["x"],
                        "entities": ["A"],
                        "topics": ["topic"],
                    },
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
                    {
                        "type": "decision",
                        "title": "A",
                        "summary": ["x"],
                        "because": ["rationale"],
                        "retrieval_title": "A",
                        "retrieval_eligible": True,
                        "retrieval_facts": ["x"],
                        "entities": ["A"],
                        "topics": ["topic"],
                    },
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

"""Tests for the canonical bead retrieval-text projection."""
import unittest
from core_memory.schema.bead_projection import build_retrieval_text


class TestBuildRetrievalText(unittest.TestCase):
    def test_minimal_bead(self):
        text = build_retrieval_text({"title": "Hello", "type": "context"})
        self.assertIn("Hello", text)
        self.assertIn("context", text)

    def test_association_anchor_fields_included(self):
        bead = {
            "title": "t",
            "entities": ["Alice", "Bob"],
            "entity_ids": ["e-1", "e-2"],
            "topics": ["planning", "budget"],
            "decision_keys": ["approve_budget"],
            "goal_keys": ["reduce_cost"],
            "action_keys": ["send_proposal"],
            "outcome_keys": ["approved"],
            "time_keys": ["Q3-2025"],
            "evidence_refs": ["bead-abc"],
            "cause_candidates": ["market_pressure"],
            "effect_candidates": ["cost_reduction"],
            "supporting_facts": ["revenue up 10%"],
        }
        text = build_retrieval_text(bead)
        for expected in ("Alice", "Bob", "planning", "budget", "approve budget",
                         "reduce cost", "send proposal", "approved", "Q3-2025",
                         "bead-abc", "market pressure", "cost reduction", "revenue up 10%"):
            self.assertIn(expected, text, f"missing: {expected!r}")

    def test_summary_because_facts_included(self):
        bead = {
            "title": "t",
            "summary": ["user wants dark mode"],
            "because": ["high contrast helps focus"],
            "retrieval_facts": ["preference set on 2025-01-01"],
            "supporting_facts": ["confirmed in session 42"],
        }
        text = build_retrieval_text(bead)
        self.assertIn("dark mode", text)
        self.assertIn("high contrast", text)
        self.assertIn("preference set", text)
        self.assertIn("confirmed in session", text)

    def test_retrieval_title_preferred_over_title(self):
        bead = {"title": "Generic Title", "retrieval_title": "Specific Retrieval Title"}
        text = build_retrieval_text(bead)
        self.assertIn("Specific Retrieval Title", text)
        self.assertNotIn("Generic Title", text)

    def test_claims_included(self):
        bead = {
            "title": "t",
            "claims": [{"subject": "user", "slot": "timezone", "claim_kind": "preference",
                        "value": "America/Chicago", "reason_text": "user said so"}],
        }
        text = build_retrieval_text(bead)
        self.assertIn("timezone", text)
        self.assertIn("preference", text)
        self.assertIn("America/Chicago", text)
        self.assertIn("user said so", text)

    def test_detail_included_for_non_archived(self):
        bead = {"title": "t", "detail": "long detail text", "status": "open"}
        text = build_retrieval_text(bead)
        self.assertIn("long detail text", text)

    def test_detail_excluded_for_archived(self):
        bead = {"title": "t", "detail": "should not appear", "status": "archived"}
        text = build_retrieval_text(bead)
        self.assertNotIn("should not appear", text)

    def test_empty_bead_does_not_raise(self):
        text = build_retrieval_text({})
        self.assertIsInstance(text, str)

    def test_write_path_and_read_path_are_identical(self):
        """The write-path embed and read-path semantic_text must produce the same output."""
        from core_memory.retrieval.visible_corpus import _semantic_text
        bead = {
            "title": "Meeting notes",
            "type": "context",
            "summary": ["discussed Q3 goals"],
            "entities": ["Alice"],
            "topics": ["planning"],
            "decision_keys": ["hire_engineer"],
        }
        from core_memory.schema.bead_projection import build_retrieval_text as proj
        self.assertEqual(proj(bead), _semantic_text(bead))


if __name__ == "__main__":
    unittest.main()

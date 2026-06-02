"""Tests for the canonical bead retrieval-text projection."""
import unittest
from core_memory.schema.bead_projection import build_retrieval_text


class TestBuildRetrievalText(unittest.TestCase):
    def test_minimal_bead(self):
        text = build_retrieval_text({"title": "Hello", "type": "context"})
        self.assertIn("Hello", text)
        self.assertIn("context", text)

    def test_entity_and_evidence_fields_included(self):
        bead = {
            "title": "t",
            "entities": ["Alice", "Bob", "charity race", "mental health"],
            "entity_ids": ["e-1", "e-2"],
            "evidence_refs": ["bead-abc"],
            "supporting_facts": ["revenue up 10%"],
        }
        text = build_retrieval_text(bead)
        for expected in ("Alice", "Bob", "charity race", "mental health",
                         "bead-abc", "revenue up 10%"):
            self.assertIn(expected, text, f"missing: {expected!r}")

    def test_dropped_fields_not_projected(self):
        bead = {
            "title": "t",
            "topics": ["planning"],
            "decision_keys": ["approve_budget"],
            "cause_candidates": ["market_pressure"],
        }
        text = build_retrieval_text(bead)
        for absent in ("planning", "approve_budget", "market_pressure"):
            self.assertNotIn(absent, text, f"dropped field leaked: {absent!r}")

    def test_legacy_retrieval_facts_projected_when_no_supporting_facts(self):
        # Pre-upgrade beads may only have retrieval_facts. Their content must
        # remain searchable without a store re-write.
        bead = {
            "title": "t",
            "retrieval_facts": ["pgvector chosen for deploy parity"],
        }
        text = build_retrieval_text(bead)
        self.assertIn("pgvector", text)

    def test_retrieval_facts_not_duplicated_when_supporting_facts_present(self):
        # When a bead has both fields (impossible post-upgrade but safe to handle),
        # retrieval_facts must not be double-projected.
        bead = {
            "title": "t",
            "supporting_facts": ["canonical fact"],
            "retrieval_facts": ["legacy fact"],
        }
        text = build_retrieval_text(bead)
        self.assertIn("canonical fact", text)
        self.assertNotIn("legacy fact", text)

    def test_summary_because_facts_included(self):
        bead = {
            "title": "t",
            "summary": ["user wants dark mode"],
            "because": ["high contrast helps focus"],
            "supporting_facts": ["confirmed in session 42"],
        }
        text = build_retrieval_text(bead)
        self.assertIn("dark mode", text)
        self.assertIn("high contrast", text)
        self.assertIn("confirmed in session", text)

    def test_title_used_directly(self):
        bead = {"title": "Specific Title"}
        text = build_retrieval_text(bead)
        self.assertIn("Specific Title", text)

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

import unittest

from core_memory.claim.outcomes import INTERACTION_ROLES, classify_memory_outcome


class TestClaimOutcomes(unittest.TestCase):
    def test_none_when_no_memory_used(self):
        self.assertIsNone(classify_memory_outcome({}))

    def test_none_with_none_input(self):
        self.assertIsNone(classify_memory_outcome(None))

    def test_none_when_used_memory_false_no_beads(self):
        self.assertIsNone(classify_memory_outcome({"used_memory": False, "retrieved_beads": []}))

    def test_memory_resolution_when_beads_retrieved(self):
        result = classify_memory_outcome({"retrieved_beads": [{"id": "b1"}]})
        self.assertIsNotNone(result)
        self.assertEqual("memory_resolution", result["interaction_role"])
        self.assertIn("memory_outcome", result)
        self.assertEqual(1, result["memory_outcome"]["bead_count"])

    def test_memory_correction_takes_priority(self):
        result = classify_memory_outcome({"used_memory": True, "retrieved_beads": [{"id": "b1"}], "correction_triggered": True})
        self.assertEqual("memory_correction", result["interaction_role"])

    def test_memory_reflection_role(self):
        result = classify_memory_outcome({"used_memory": True, "retrieved_beads": [{"id": "b1"}], "reflection_triggered": True})
        self.assertEqual("memory_reflection", result["interaction_role"])

    def test_used_memory_true_no_beads_returns_resolution(self):
        result = classify_memory_outcome({"used_memory": True, "retrieved_beads": []})
        self.assertIsNotNone(result)
        self.assertEqual("memory_resolution", result["interaction_role"])

    def test_all_interaction_roles_defined(self):
        for role in ["memory_resolution", "memory_correction", "memory_reflection"]:
            self.assertIn(role, INTERACTION_ROLES)

    def test_outcome_has_required_keys(self):
        result = classify_memory_outcome({"used_memory": True})
        self.assertIn("interaction_role", result)
        self.assertIn("memory_outcome", result)
        self.assertIn("role", result["memory_outcome"])
        self.assertIn("description", result["memory_outcome"])


if __name__ == "__main__":
    unittest.main()

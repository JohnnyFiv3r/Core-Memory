import unittest

from core_memory.policy.association_contract import normalize_assoc_row, assoc_row_is_valid, assoc_dedupe_key


class TestAssociationPolicyContract(unittest.TestCase):
    def test_normalize_and_validate(self):
        row = {"source_bead": "A", "target_bead_id": "B", "relationship": "Supports "}
        n = normalize_assoc_row(row)
        self.assertEqual("A", n.get("source_bead_id"))
        self.assertEqual("B", n.get("target_bead_id"))
        self.assertEqual("supports", n.get("relationship"))
        self.assertTrue(assoc_row_is_valid(n))
        self.assertEqual(("A", "B", "supports"), assoc_dedupe_key(n))

    def test_normalize_legacy_relation_aliases_via_schema_vocabulary(self):
        row = {"source_bead": "A", "target_bead": "B", "relationship": "Causes"}
        n = normalize_assoc_row(row)
        self.assertEqual("causes", n.get("relationship"))

    def test_normalize_caused_by_swaps_endpoints(self):
        row = {"source_bead": "effect", "target_bead": "cause", "relationship": "caused_by"}
        n = normalize_assoc_row(row)
        self.assertEqual("cause", n.get("source_bead_id"))
        self.assertEqual("effect", n.get("target_bead_id"))
        self.assertEqual("causes", n.get("relationship"))
        self.assertTrue(n.get("endpoints_swapped"))

    def test_inverse_relation_aliases_swap_endpoints(self):
        row = {"source_bead": "blocked", "target_bead": "blocker", "relationship": "blocked_by"}
        n = normalize_assoc_row(row)
        self.assertEqual("blocker", n.get("source_bead_id"))
        self.assertEqual("blocked", n.get("target_bead_id"))
        self.assertEqual("blocks", n.get("relationship"))
        self.assertTrue(n.get("endpoints_swapped"))


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

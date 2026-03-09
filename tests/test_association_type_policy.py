import unittest

from core_memory.schema import association_policy, CANONICAL_BEAD_TYPES


class TestAssociationTypePolicy(unittest.TestCase):
    def test_association_policy_enforced(self):
        self.assertEqual("keep_as_bead_and_edge", association_policy())
        self.assertIn("association", CANONICAL_BEAD_TYPES)


if __name__ == "__main__":
    unittest.main()

import unittest

import tempfile

from core_memory.schema.normalization import association_policy, CANONICAL_BEAD_TYPES
from core_memory.store import MemoryStore


class TestAssociationTypePolicy(unittest.TestCase):
    def test_association_policy_enforced(self):
        self.assertEqual("edge_primary_no_association_bead", association_policy())
        self.assertNotIn("association", CANONICAL_BEAD_TYPES)

    def test_association_bead_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            with self.assertRaises(ValueError):
                s.add_bead(type="association", title="x", summary=["y"], session_id="s1", source_turn_ids=["t1"])


if __name__ == "__main__":
    unittest.main()

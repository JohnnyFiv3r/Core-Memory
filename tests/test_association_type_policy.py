import unittest

import os
import tempfile

from core_memory.schema import association_policy, CANONICAL_BEAD_TYPES
from core_memory.store import MemoryStore


class TestAssociationTypePolicy(unittest.TestCase):
    def test_association_policy_enforced(self):
        self.assertEqual("edge_primary_explicit_bead_only", association_policy())
        self.assertIn("association", CANONICAL_BEAD_TYPES)

    def test_association_bead_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            with self.assertRaises(ValueError):
                s.add_bead(type="association", title="x", summary=["y"], session_id="s1", source_turn_ids=["t1"])

    def test_association_bead_compat_override(self):
        old = os.environ.get("CORE_MEMORY_ALLOW_IMPLICIT_ASSOCIATION_BEAD")
        try:
            os.environ["CORE_MEMORY_ALLOW_IMPLICIT_ASSOCIATION_BEAD"] = "1"
            with tempfile.TemporaryDirectory() as td:
                s = MemoryStore(td)
                bid = s.add_bead(type="association", title="x", summary=["y"], session_id="s1", source_turn_ids=["t1"])
                self.assertTrue(bid)
        finally:
            if old is None:
                os.environ.pop("CORE_MEMORY_ALLOW_IMPLICIT_ASSOCIATION_BEAD", None)
            else:
                os.environ["CORE_MEMORY_ALLOW_IMPLICIT_ASSOCIATION_BEAD"] = old


if __name__ == "__main__":
    unittest.main()

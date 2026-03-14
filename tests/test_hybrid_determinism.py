import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.persistence.store import MemoryStore


class TestHybridDeterminism(unittest.TestCase):
    def test_deterministic_ordering(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            for i in range(8):
                s.add_bead(type="context", title=f"Item {i}", summary=["promotion inflation test"], session_id="main", source_turn_ids=[f"t{i}"])
            a = hybrid_lookup(Path(td), "promotion inflation", k=5)
            b = hybrid_lookup(Path(td), "promotion inflation", k=5)
            self.assertEqual([x["bead_id"] for x in a.get("results")], [x["bead_id"] for x in b.get("results")])
            self.assertTrue(str(a.get("retrieval_query") or "").strip())


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.lexical import lexical_lookup
from core_memory.persistence.store import MemoryStore


class TestLexicalFieldWeights(unittest.TestCase):
    def test_title_and_tag_weighting_surfaces_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="graph archive retrieval", summary=["misc note"], tags=["graph_archive_retrieval"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="misc", summary=["graph archive retrieval details"], session_id="main", source_turn_ids=["t2"])
            out = lexical_lookup(Path(td), "graph archive retrieval", k=5)
            ids = [r.get("bead_id") for r in (out.get("results") or [])]
            self.assertTrue(ids)
            self.assertEqual(a, ids[0])
            self.assertIn(b, ids)


if __name__ == "__main__":
    unittest.main()

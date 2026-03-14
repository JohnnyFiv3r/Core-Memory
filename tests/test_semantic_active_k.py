import tempfile
import unittest
from pathlib import Path

from core_memory.graph import add_semantic_edge, build_graph
from core_memory.persistence.store import MemoryStore


class TestSemanticActiveK(unittest.TestCase):
    def test_top_k_semantic_edges_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            src = s.add_bead(type="context", title="src", summary=["s"], session_id="main", source_turn_ids=["t1"])
            dst_ids = [
                s.add_bead(type="context", title=f"d{i}", summary=["s"], session_id="main", source_turn_ids=[f"t{i+2}"])
                for i in range(5)
            ]
            for i, d in enumerate(dst_ids):
                add_semantic_edge(Path(td), src_id=src, dst_id=d, rel="related_to", w=0.9 - (i * 0.1))

            g = build_graph(Path(td), write_snapshot=False, semantic_active_k=2)
            self.assertEqual(2, len((g.get("adj_semantic_out") or {}).get(src, [])))
            self.assertGreaterEqual(int(g.get("semantic_evicted", 0)), 3)


if __name__ == "__main__":
    unittest.main()

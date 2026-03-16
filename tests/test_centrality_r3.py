import tempfile
import unittest
from pathlib import Path

from core_memory.graph.api import add_structural_edge, build_graph, graph_stats
from core_memory.persistence.store import MemoryStore


class TestCentralityR3(unittest.TestCase):
    def test_graph_exposes_centrality(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="lesson", title="B", summary=["b"], session_id="main", source_turn_ids=["t2"])
            c = s.add_bead(type="evidence", title="C", summary=["c"], session_id="main", source_turn_ids=["t3"])

            add_structural_edge(Path(td), src_id=a, dst_id=b, rel="supports")
            add_structural_edge(Path(td), src_id=a, dst_id=c, rel="supports")

            g = build_graph(Path(td), write_snapshot=False)
            cent = g.get("node_centrality") or {}
            self.assertIn(a, cent)
            self.assertGreaterEqual(int(cent.get(a, 0)), 2)

            stats = graph_stats(Path(td))
            self.assertTrue(stats.get("ok"))
            self.assertIn("top_central_nodes", stats)


if __name__ == "__main__":
    unittest.main()

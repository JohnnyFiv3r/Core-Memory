import tempfile
import unittest
from pathlib import Path

from core_memory.graph.api import (
    add_structural_edge,
    build_graph,
    backfill_structural_edges,
    update_semantic_edge,
)
from core_memory.persistence.store import MemoryStore


class TestGraphR2(unittest.TestCase):
    def test_graph_build_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="decision", title="D1", summary=["s"], session_id="main", source_turn_ids=["t1"])
            b2 = s.add_bead(type="evidence", title="E1", summary=["s"], session_id="main", source_turn_ids=["t2"], links=[{"type": "supports", "bead_id": b1}])
            _ = b2
            backfill_structural_edges(Path(td))

            g1 = build_graph(Path(td), write_snapshot=False)
            g2 = build_graph(Path(td), write_snapshot=False)
            self.assertEqual(g1["adj_structural_out"], g2["adj_structural_out"])
            self.assertEqual(g1["edge_head"], g2["edge_head"])

    def test_structural_immutability_ignores_update(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            b1 = s.add_bead(type="decision", title="D1", summary=["s"], session_id="main", source_turn_ids=["t1"])
            b2 = s.add_bead(type="lesson", title="L1", summary=["s"], session_id="main", source_turn_ids=["t2"])
            ev = add_structural_edge(Path(td), src_id=b2, dst_id=b1, rel="supports")
            update_semantic_edge(Path(td), edge_id=ev["edge_id"], w=0.99, reinforcement_count=3)
            g = build_graph(Path(td), write_snapshot=False)
            self.assertTrue(any("ignored_update_on_immutable" in w for w in g.get("warnings", [])))
            edge = g["edge_head"][ev["edge_id"]]
            self.assertEqual("structural", edge.get("class"))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from core_memory.graph import (
    add_semantic_edge,
    add_structural_edge,
    build_graph,
    causal_traverse,
    decay_semantic_edges,
    reinforce_semantic_edges,
)
from core_memory.semantic_index import build_semantic_index, semantic_lookup
from core_memory.persistence.store import MemoryStore


class TestR3GraphSemantic(unittest.TestCase):
    def test_semantic_lookup_and_traverse_grounded_chain(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Candidate-based promotion gate", summary=["promotion requires candidate status"], session_id="main", source_turn_ids=["t1"])
            l = s.add_bead(type="lesson", title="Promotion overuse breaks compaction", summary=["too many promoted beads"], session_id="main", source_turn_ids=["t2"])
            e = s.add_bead(type="evidence", title="Compaction metrics", summary=["728/790 promoted"], session_id="main", source_turn_ids=["t3"], supports_bead_ids=[d])

            add_structural_edge(Path(td), src_id=d, dst_id=l, rel="supports")
            add_structural_edge(Path(td), src_id=l, dst_id=e, rel="derived_from")
            build_graph(Path(td), write_snapshot=False)

            b = build_semantic_index(Path(td))
            self.assertTrue(b.get("ok"))
            q = semantic_lookup(Path(td), "why candidate promotion gate", k=5)
            self.assertTrue(q.get("ok"))
            anchors = [r.get("bead_id") for r in (q.get("results") or []) if r.get("bead_id")]
            self.assertTrue(len(anchors) >= 1)

            t = causal_traverse(Path(td), anchor_ids=anchors, max_depth=4)
            self.assertTrue(t.get("ok"))
            self.assertTrue(len(t.get("chains") or []) >= 1)

    def test_semantic_decay_and_reinforcement(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            a = s.add_bead(type="context", title="A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="context", title="B", summary=["b"], session_id="main", source_turn_ids=["t2"])
            ev = add_semantic_edge(Path(td), src_id=a, dst_id=b, rel="related_to", w=0.5)

            r = reinforce_semantic_edges(Path(td), [ev["edge_id"]], alpha=0.2)
            self.assertTrue(r.get("ok"))
            self.assertEqual(1, r.get("reinforced"))

            d = decay_semantic_edges(Path(td), w_drop=0.99, half_life_days=1.0)
            self.assertTrue(d.get("ok"))
            self.assertGreaterEqual(d.get("deactivated", 0), 1)


if __name__ == "__main__":
    unittest.main()

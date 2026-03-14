import tempfile
import unittest
from pathlib import Path

from core_memory.graph.api import add_structural_edge, build_graph
from core_memory.retrieval.hybrid import hybrid_lookup
from core_memory.retrieval.rerank import rerank_candidates
from core_memory.persistence.store import MemoryStore


class TestStructuralFeaturesRadius1(unittest.TestCase):
    def test_chain_features_use_radius1_structural_neighbors(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="D", summary=["d"], session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="E", summary=["e"], session_id="main", source_turn_ids=["t2"])
            add_structural_edge(Path(td), src_id=d, dst_id=e, rel="supports")
            build_graph(Path(td), write_snapshot=True)

            h = hybrid_lookup(Path(td), "decision evidence", k=5)
            rr = rerank_candidates(Path(td), "decision evidence", h.get("results") or [])
            rows = rr.get("results") or []
            row = next(r for r in rows if r.get("bead_id") == d)
            f = row.get("features") or {}
            self.assertEqual(1, f.get("chain_has_decision"))
            self.assertEqual(1, f.get("chain_has_evidence"))
            self.assertEqual(1, f.get("has_grounding_structural_edge"))


if __name__ == "__main__":
    unittest.main()

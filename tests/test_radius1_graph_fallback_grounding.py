import tempfile
import unittest
from pathlib import Path

from core_memory.graph import add_structural_edge, build_graph
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestRadius1GraphFallbackGrounding(unittest.TestCase):
    def test_graph_fallback_uses_structural_edge_heads(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Gate", summary=["candidate"], session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="Evidence", summary=["metrics"], session_id="main", source_turn_ids=["t2"])
            add_structural_edge(Path(td), src_id=d, dst_id=e, rel="supports")
            build_graph(Path(td), write_snapshot=True)

            out = memory_reason("why gate", root=td)
            self.assertTrue(out.get("ok"))
            chains = out.get("chains") or []
            self.assertTrue(any(len(c.get("edges") or []) > 0 for c in chains))


if __name__ == "__main__":
    unittest.main()

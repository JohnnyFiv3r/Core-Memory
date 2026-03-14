import tempfile
import unittest
from pathlib import Path

from core_memory.graph import add_semantic_edge, add_structural_edge, build_graph
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestMemoryReasonR4(unittest.TestCase):
    def test_memory_reason_returns_grounded_chain_and_citations(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            ctx = s.add_bead(type="context", title="Promotion problem thread", summary=["remember promotion problem"], session_id="main", source_turn_ids=["t1"])
            dec = s.add_bead(type="decision", title="Promotion requires candidate gate", summary=["stop blanket promotion"], session_id="main", source_turn_ids=["t2"])
            ev = s.add_bead(type="evidence", title="Compaction metrics", summary=["728/790 promoted"], session_id="main", source_turn_ids=["t3"], supports_bead_ids=[dec])

            sem = add_semantic_edge(Path(td), src_id=ctx, dst_id=dec, rel="related_to", w=0.6)
            _ = sem
            add_structural_edge(Path(td), src_id=dec, dst_id=ev, rel="supports")
            build_graph(Path(td), write_snapshot=False)

            out = memory_reason("remember promotion problem", k=5, root=td)
            self.assertTrue(out.get("ok"))
            self.assertTrue(len(out.get("chains") or []) >= 1)
            self.assertTrue(len(out.get("citations") or []) >= 1)
            self.assertIn("answer", out)


if __name__ == "__main__":
    unittest.main()

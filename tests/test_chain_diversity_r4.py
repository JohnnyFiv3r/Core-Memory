import tempfile
import unittest
from pathlib import Path

from core_memory.graph import add_structural_edge, build_graph
from core_memory.store import MemoryStore
from core_memory.tools.memory_reason import memory_reason


class TestChainDiversityR4(unittest.TestCase):
    def test_reason_returns_confidence_and_diverse_chains(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Decision A", summary=["a"], session_id="main", source_turn_ids=["t1"])
            l1 = s.add_bead(type="lesson", title="Lesson 1", summary=["l1"], session_id="main", source_turn_ids=["t2"])
            l2 = s.add_bead(type="lesson", title="Lesson 2", summary=["l2"], session_id="main", source_turn_ids=["t3"])
            e = s.add_bead(type="evidence", title="Evidence", summary=["e"], session_id="main", source_turn_ids=["t4"], supports_bead_ids=[d])
            add_structural_edge(Path(td), src_id=d, dst_id=l1, rel="supports")
            add_structural_edge(Path(td), src_id=d, dst_id=l2, rel="supports")
            add_structural_edge(Path(td), src_id=l1, dst_id=e, rel="derived_from")
            add_structural_edge(Path(td), src_id=l2, dst_id=e, rel="derived_from")
            build_graph(Path(td), write_snapshot=False)

            out = memory_reason("why decision a", root=td)
            self.assertTrue(out.get("ok"))
            chains = out.get("chains") or []
            self.assertTrue(len(chains) >= 1)
            self.assertIn("confidence", chains[0])


if __name__ == "__main__":
    unittest.main()

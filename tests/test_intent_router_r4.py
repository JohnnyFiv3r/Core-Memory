import tempfile
import unittest
from pathlib import Path

from core_memory.graph import add_structural_edge, build_graph
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestIntentRouterR4(unittest.TestCase):
    def test_why_intent_selected(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Candidate gate decision", summary=["decision"], session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="Metrics evidence", summary=["728/790 promoted"], session_id="main", source_turn_ids=["t2"], supports_bead_ids=[d])
            add_structural_edge(Path(td), src_id=d, dst_id=e, rel="supports")
            build_graph(Path(td), write_snapshot=False)
            out = memory_reason("why did we decide candidate gating?", root=td)
            self.assertTrue(out.get("ok"))
            self.assertEqual("why", (out.get("intent") or {}).get("selected"))

    def test_soft_router_fallback_when_low_confidence(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="context", title="General memory", summary=["misc"], session_id="main", source_turn_ids=["t1"])
            out = memory_reason("help", root=td)
            self.assertTrue(out.get("ok"))
            self.assertIn((out.get("intent") or {}).get("selected"), {"why", "remember", "when", "what_changed"})
            self.assertIn("answer", out)


if __name__ == "__main__":
    unittest.main()

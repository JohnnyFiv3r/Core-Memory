import tempfile
import unittest
from pathlib import Path

from core_memory.graph.api import add_structural_edge, build_graph
from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestCitationConfidenceR4(unittest.TestCase):
    def test_reason_includes_citation_confidence(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            d = s.add_bead(type="decision", title="Gate", summary=["candidate only"], status="candidate", session_id="main", source_turn_ids=["t1"])
            e = s.add_bead(type="evidence", title="Metrics", summary=["promotion inflation"], status="candidate", session_id="main", source_turn_ids=["t1"])
            add_structural_edge(Path(td), src_id=e, dst_id=d, rel="supports")
            build_graph(Path(td), write_snapshot=False)

            out = memory_reason("why candidate only", root=td)
            self.assertTrue(out.get("ok"))
            self.assertIn("confidence", out)
            self.assertIn("overall", out.get("confidence") or {})
            cits = out.get("citations") or []
            self.assertTrue(len(cits) >= 1)
            self.assertIn("confidence", cits[0])
            self.assertIn("grounded_role", cits[0])


if __name__ == "__main__":
    unittest.main()

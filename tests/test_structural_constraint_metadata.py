import tempfile
import unittest

from core_memory.store import MemoryStore
from core_memory.retrieval.tools.memory_reason import memory_reason


class TestStructuralConstraintMetadata(unittest.TestCase):
    def test_causal_query_reports_no_structural_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="D", summary=["x"], session_id="main", source_turn_ids=["t1"])
            out = memory_reason("why did we do this", root=td)
            self.assertTrue(out.get("ok"))
            g = out.get("grounding") or {}
            self.assertTrue(g.get("causal_intent"))
            self.assertIn("reason", g)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import memory_search_typed


class TestExplainContainsSnapsAndScores(unittest.TestCase):
    def test_explain_payload(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="candidate gate", summary=["promotion"], tags=["promotion_workflow"], session_id="main", source_turn_ids=["t1"])
            out = memory_search_typed(td, {
                "intent": "causal",
                "query_text": "why candidate gate",
                "topic_keys": ["promotion_workflow"],
                "k": 5,
            }, explain=True)
            self.assertTrue(out.get("ok"))
            ex = out.get("explain") or {}
            self.assertIn("snapped_query", ex)
            self.assertIn("snap_decisions", ex)
            self.assertIn("retrieval", ex)
            self.assertTrue((out.get("results") or []))
            self.assertIn("score", (out.get("results") or [])[0])


if __name__ == "__main__":
    unittest.main()

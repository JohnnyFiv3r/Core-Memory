import tempfile
import unittest

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.tools.memory_search import search_typed


class TestMemorySearchToolWrapper(unittest.TestCase):
    def test_tool_wrapper_search_only(self):
        with tempfile.TemporaryDirectory() as td:
            s = MemoryStore(td)
            s.add_bead(type="decision", title="Candidate-first promotion", summary=["promotion workflow"], tags=["promotion_workflow"], session_id="main", source_turn_ids=["t1"])

            out = search_typed(
                {
                    "intent": "causal",
                    "query_text": "why candidate-first promotion",
                    "topic_keys": ["promotion_workflow"],
                    "k": 5,
                },
                root=td,
                explain=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual("memory_search_result.v1", out.get("schema_version"))
            self.assertEqual("typed_search", out.get("contract"))
            self.assertIsInstance(out.get("results") or [], list)
            if out.get("results"):
                first = out["results"][0]
                self.assertIn("bead_id", first)
            self.assertIn("explain", out)


if __name__ == "__main__":
    unittest.main()

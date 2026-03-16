import tempfile
import unittest
from unittest.mock import patch

from core_memory.retrieval.pipeline.execute import execute_request


class TestMemoryExecuteDeterministicOrder(unittest.TestCase):
    @patch("core_memory.retrieval.pipeline.execute._load_beads")
    @patch("core_memory.retrieval.pipeline.execute.search_typed")
    @patch("core_memory.retrieval.pipeline.execute.snap_form")
    @patch("core_memory.retrieval.pipeline.execute.build_catalog")
    def test_execute_normalizes_result_and_chain_order(self, mcat, msnap, msearch, mbeads):
        mcat.return_value = {}
        msnap.return_value = {"snapped": {"intent": "remember", "query_text": "q"}, "decisions": {}}
        mbeads.return_value = {}
        msearch.return_value = {
            "ok": True,
            "results": [
                {"bead_id": "b2", "title": "B", "type": "context", "snippet": "", "score": 0.8, "source_surface": "session_bead"},
                {"bead_id": "b1", "title": "A", "type": "context", "snippet": "", "score": 0.8, "source_surface": "session_bead"},
            ],
            "chains": [
                {"path": ["x", "y"], "edges": [{"rel": "causes"}], "score": 0.4},
                {"path": ["a", "b"], "edges": [{"rel": "supports"}], "score": 0.4},
            ],
            "snapped_query": {"intent": "remember", "query_text": "q"},
            "warnings": [],
        }

        with tempfile.TemporaryDirectory() as td:
            out = execute_request({"raw_query": "q", "intent": "remember", "k": 5}, root=td, explain=False)

        self.assertTrue(out.get("ok"))
        self.assertEqual(["b1", "b2"], [r.get("bead_id") for r in out.get("results") or []])
        self.assertEqual(["a", "x"], [(c.get("path") or [""])[0] for c in out.get("chains") or []])


if __name__ == "__main__":
    unittest.main()

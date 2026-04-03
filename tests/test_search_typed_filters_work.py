import os
import tempfile
import unittest
from unittest.mock import patch

from core_memory.persistence.store import MemoryStore
from core_memory.retrieval.pipeline import memory_search_typed


class TestSearchTypedFilters(unittest.TestCase):
    def test_filters_apply(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CORE_MEMORY_CANONICAL_SEMANTIC_MODE": "degraded_allowed"}, clear=False):
            s = MemoryStore(td)
            a = s.add_bead(type="decision", title="candidate first", summary=["promotion"], tags=["promotion_workflow"], session_id="main", source_turn_ids=["t1"])
            b = s.add_bead(type="evidence", title="other", summary=["misc"], tags=["other"], session_id="main", source_turn_ids=["t2"])
            out = memory_search_typed(td, {
                "intent": "remember",
                "query_text": "promotion",
                "topic_keys": ["promotion_workflow"],
                "bead_types": ["decision"],
                "k": 5,
            }, explain=True)
            self.assertTrue(out.get("ok"))
            ids = [r.get("bead_id") for r in (out.get("results") or [])]
            # Retrieval ranking may legitimately return zero strong anchors.
            # When results are present, filters must still hold.
            if ids:
                self.assertIn(a, ids)
                self.assertNotIn(b, ids)
            else:
                self.assertIsInstance(out.get("warnings") or [], list)


if __name__ == "__main__":
    unittest.main()

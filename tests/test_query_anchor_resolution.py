import tempfile
import unittest
from pathlib import Path

from core_memory.retrieval.query_norm import resolve_query_anchors


class TestQueryAnchorResolution(unittest.TestCase):
    def test_resolver_returns_expanded_query_and_matches(self):
        with tempfile.TemporaryDirectory() as td:
            out = resolve_query_anchors("what caused the promotion inflation episode", Path(td))
        self.assertIn("expanded_query", out)
        self.assertTrue(isinstance(out.get("matched_incidents"), list))
        self.assertTrue(isinstance(out.get("matched_topics"), list))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_memory.persistence import semantic_lifecycle as persistence_lifecycle


class TestSemanticLifecycleBoundaries(unittest.TestCase):
    def test_persistence_semantic_dirty_does_not_autodrain(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("core_memory.retrieval.lifecycle._maybe_start_autodrain") as autodrain:
                out = persistence_lifecycle.mark_semantic_dirty(td, reason="unit")

        self.assertTrue(out.get("ok"))
        autodrain.assert_not_called()

    def test_retrieval_semantic_dirty_wraps_persistence_and_autodrain(self):
        from core_memory.retrieval import lifecycle as retrieval_lifecycle

        with tempfile.TemporaryDirectory() as td:
            expected = {"ok": True, "queue": {"queued": True}}
            with patch("core_memory.retrieval.lifecycle._mark_semantic_dirty", return_value=expected) as dirty:
                with patch("core_memory.retrieval.lifecycle._maybe_start_autodrain") as autodrain:
                    out = retrieval_lifecycle.mark_semantic_dirty(td, reason="unit")

        self.assertEqual(expected, out)
        dirty.assert_called_once_with(td, reason="unit", enqueue=True)
        autodrain.assert_called_once_with(Path(td))

    def test_retrieval_status_adds_autodrain_metadata(self):
        from core_memory.retrieval import lifecycle as retrieval_lifecycle

        with tempfile.TemporaryDirectory() as td:
            persistence_status = persistence_lifecycle.semantic_status(td)
            retrieval_status = retrieval_lifecycle.semantic_status(td)

        self.assertNotIn("autodrain", persistence_status)
        self.assertIn("autodrain", retrieval_status)
        self.assertIn("enabled", retrieval_status["autodrain"])
        self.assertIn("running", retrieval_status["autodrain"])


if __name__ == "__main__":
    unittest.main()

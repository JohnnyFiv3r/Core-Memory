import os
import tempfile
import unittest
from pathlib import Path

from core_memory.semantic_index import build_semantic_index, semantic_lookup
from core_memory.store import MemoryStore


class TestSemanticBackendModes(unittest.TestCase):
    def test_build_without_provider_uses_safe_backend(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.pop("CORE_MEMORY_EMBEDDINGS_PROVIDER", None)
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["alpha"], session_id="main", source_turn_ids=["t1"])
            out = build_semantic_index(Path(td))
            self.assertTrue(out.get("ok"))
            self.assertIn(out.get("backend"), {"faiss-hash", "lexical"})
            q = semantic_lookup(Path(td), "alpha", k=3)
            self.assertTrue(q.get("ok"))


if __name__ == "__main__":
    unittest.main()

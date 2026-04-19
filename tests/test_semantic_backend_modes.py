import os
import tempfile
import unittest
from pathlib import Path

import core_memory.retrieval.semantic_index as sem_mod
from core_memory.retrieval.semantic_index import build_semantic_index, semantic_lookup
from core_memory.persistence.store import MemoryStore


class TestSemanticBackendModes(unittest.TestCase):
    def setUp(self):
        sem_mod._startup_check_done = False

    def tearDown(self):
        sem_mod._startup_check_done = False

    def test_build_without_provider_uses_safe_backend(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ.pop("CORE_MEMORY_EMBEDDINGS_PROVIDER", None)
            os.environ.pop("CORE_MEMORY_VECTOR_BACKEND", None)
            os.environ["CORE_MEMORY_CANONICAL_SEMANTIC_MODE"] = "degraded_allowed"
            s = MemoryStore(td)
            s.add_bead(type="decision", title="A", summary=["alpha"], session_id="main", source_turn_ids=["t1"])
            out = build_semantic_index(Path(td))
            self.assertTrue(out.get("ok"))
            self.assertIn(out.get("backend"), {"faiss-hash", "lexical"})
            q = semantic_lookup(Path(td), "alpha", k=3)
            self.assertTrue(q.get("ok"))


if __name__ == "__main__":
    unittest.main()
